"""Document processor — file parsing with optional OCR (PaddleOCR, EasyOCR, Tesseract)."""

import asyncio
import importlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from backend.services.doc_parser import DocumentParser

logger = logging.getLogger(__name__)
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"}


class DocumentProcessor:
    """Processes documents: file parsing with optional OCR for images.

    Supports multiple OCR engines with fallback chain:
    1. PaddleOCR (if available, recommended)
    2. EasyOCR (if available, fallback to PaddleOCR)
    3. Tesseract via pytesseract (if available, tertiary fallback)
    """

    def __init__(self, config: dict | None = None):
        """Initialise DocumentProcessor."""
        self.config = config or {}
        self._ocr = None
        self._ocr_engine = None  # "paddleocr", "easyocr", "tesseract", or None
        self._parser = DocumentParser()
        if self.config.get("ocr_enabled", False):
            self._initialize_ocr_sync()
        self._check_version_compatibility()

    async def process_file(self, file_path: str) -> dict[str, Any]:
        """Process a file and extract text. Uses OCR for images and scanned PDFs.

 Raises:
            ValueError: If the file is an image (or an unparseable scanned
                PDF) and OCR is not available or yields no text.
        """
        logger.info("Processing file: %s, config: %s", file_path, self.config)
        ext = Path(file_path).suffix.lower()
        if ext in IMAGE_EXTENSIONS:
            logger.info("File is image extension: %s", ext)
            if not self.config.get("ocr_enabled", False):
                logger.error("OCR disabled in config: %s", self.config)
                raise ValueError(
                    f"Image file '{Path(file_path).name}' requires OCR but "
                    "ocr_enabled is false. Enable OCR in config/settings.yaml "
                    "under dms.ocr_enabled: true"
                )
            return await self._process_with_ocr(file_path)
        logger.info("Processing as text file")
        result = await self._process_with_existing(file_path)

        # Scanned PDFs have an image-only page tree: pdfplumber extracts
        # "" per page and the document otherwise ends up in the DMS with
        # zero chunks (§2.4 of the 2026-08-31 review). Route empty-text
        # PDFs through OCR when it is enabled — rasterize each page and
        # OCR the images with the active engine.
        if (
            ext == ".pdf"
            and not result.get("text", "").strip()
            and self.config.get("ocr_enabled", False)
        ):
            logger.info("No text layer in PDF %s — falling back to page-wise OCR", file_path)
            return await self._process_pdf_with_ocr(file_path)
        return result

    async def _process_pdf_with_ocr(self, file_path: str) -> dict[str, Any]:
        """Rasterize a scanned PDF page-by-page and OCR each page image.

 Uses ``pdfplumber``'s ``page.to_image()`` (pypdfium2-backed since
        pdfplumber 0.11) for rendering; each page is OCR'd by the active
        engine via the same code path as standalone image files. If no
        OCR engine is available — or every page yields no text — a
        ``ValueError`` is raised so the upload fails loudly (422) instead
        of silently storing an empty document.
        """
        ocr = await asyncio.to_thread(self._get_ocr)
        if ocr is None:
            raise ValueError(
                f"Scanned PDF '{Path(file_path).name}' has no text layer and "
                "no OCR engine is available. Install paddleocr, easyocr, or "
                "tesseract (see config/settings.yaml dms.ocr_preferred_engine)."
            )

        page_count = 0
        page_texts: list[str] = []

        def _rasterize_and_ocr() -> tuple[int, list[str]]:
            import pdfplumber

            texts: list[str] = []
            with pdfplumber.open(file_path) as pdf:
                pages = list(pdf.pages)
                for i, page in enumerate(pages):
                    # ~150 DPI is plenty for OCR of body text.
                    rendered = page.to_image(resolution=150)
                    png_bytes = rendered.original.tobytes(format="PNG")
                    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                        tmp.write(png_bytes)
                        tmp_path = tmp.name
                    try:
                        # Reuse the single-image OCR paths (paddle/easy/
                        # tesseract) synchronously — we are already off
                        # the event loop in this worker thread.
                        page_text = self._ocr_image_sync(tmp_path, ocr)
                    finally:
                        Path(tmp_path).unlink(missing_ok=True)
                    texts.append(page_text or "")
            return len(pages), texts

        try:
            page_count, page_texts = await asyncio.to_thread(_rasterize_and_ocr)
        except Exception as exc:
            logger.warning("Scanned-PDF OCR failed for %s: %s", file_path, exc)
            raise ValueError(
                f"OCR processing failed for scanned PDF '{Path(file_path).name}': {exc}"
            ) from exc

        text = "\n\n".join(t for t in page_texts if t).strip()
        if not text:
            raise ValueError(
                f"Scanned PDF '{Path(file_path).name}' produced no text via OCR "
                f"({self._ocr_engine} engine). The scan quality may be too low "
                "or the language configuration wrong (dms.ocr_lang)."
            )

        metadata = self._build_metadata(file_path, text, {"pages": page_count}, ocr_used=True)
        metadata["ocr_engine"] = self._ocr_engine
        metadata["scanned_pdf"] = True
        return {"text": text, "metadata": metadata, "ocr_used": True}

    async def _process_with_existing(self, file_path: str) -> dict[str, Any]:
        """Process with existing internally."""
        result = await self._parser.parse_file(file_path)
        text = result.get("text", "")
        metadata = self._build_metadata(
            file_path,
            text,
            result.get("metadata"),
            ocr_used=False,
        )
        return {"text": text, "metadata": metadata, "ocr_used": False}

    async def _process_with_ocr(self, file_path: str) -> dict[str, Any]:
        """Process an image file with available OCR engine.

        Raises ``ValueError`` when no OCR engine is available — previously
        this fell back to reading the binary image bytes as "text",
        silently storing garbage (§2.4 of the 2026-08-31 review). The
        upload flow catches ``ValueError`` and surfaces it as 422.
        """
        ocr = await asyncio.to_thread(self._get_ocr)
        if ocr is None:
            logger.warning("No OCR engine available for %s", file_path)
            raise ValueError(
                f"Image file '{Path(file_path).name}' requires OCR but no OCR "
                "engine is available. Install paddleocr, easyocr, or tesseract "
                "(see config/settings.yaml dms.ocr_preferred_engine)."
            )

        try:
            if self._ocr_engine == "paddleocr":
                return await self._process_with_paddle(file_path, ocr)
            elif self._ocr_engine == "easyocr":
                return await self._process_with_easyocr(file_path, ocr)
            elif self._ocr_engine == "tesseract":
                return await self._process_with_tesseract(file_path, ocr)
            else:
                raise ValueError(
                    f"Image file '{Path(file_path).name}' requires OCR but no "
                    "OCR engine is available."
                )
        except ValueError:
            raise
        except Exception as exc:
            logger.warning("OCR failed for %s: %s", file_path, exc)
            # OCR-engine failure on a binary image is NOT recoverable by
            # text extraction — raise so the upload fails loudly instead
            # of indexing binary garbage (§2.4).
            raise ValueError(
                f"OCR failed for image '{Path(file_path).name}' "
                f"({self._ocr_engine} engine): {exc}"
            ) from exc

    def _ocr_image_sync(self, image_path: str, ocr) -> str:
        """Run the active OCR engine on one image file, synchronously.

        Used by ``_process_pdf_with_ocr`` for rasterized PDF pages —
        always called on a worker thread (``asyncio.to_thread``), never
        on the event loop. Returns the extracted text (possibly "").
        """
        if self._ocr_engine == "paddleocr":
            predict = getattr(ocr, "predict")
            results = predict(image_path)
            return self._extract_paddle_text(results)
        if self._ocr_engine == "easyocr":
            results = ocr.readtext(image_path)
            return "\n".join(r[1] for r in results).strip()
        if self._ocr_engine == "tesseract":
            import pytesseract
            from PIL import Image

            with Image.open(image_path) as img:
                return pytesseract.image_to_string(img, lang=self.config.get("ocr_lang", "deu+eng")).strip()
        return ""

    async def _process_with_paddle(self, file_path: str, ocr) -> dict[str, Any]:
        """Process with PaddleOCR."""
        predict = getattr(ocr, "predict")
        results = await asyncio.to_thread(predict, file_path)
        text = self._extract_paddle_text(results)
        metadata = self._build_metadata(file_path, text, ocr_used=True)
        return {"text": text, "metadata": metadata, "ocr_used": True}

    async def _process_with_tesseract(self, file_path: str, ocr) -> dict[str, Any]:
        """Process with Tesseract via pytesseract."""
        import pytesseract
        from PIL import Image

        with Image.open(file_path) as img:
            text = pytesseract.image_to_string(img, lang=self.config.get("ocr_lang", "deu+eng"))
        metadata = self._build_metadata(file_path, text, ocr_used=True)
        return {"text": text, "metadata": metadata, "ocr_used": True}

    def _initialize_ocr_sync(self):
        """Initialize OCR engine synchronously.

        Fallback chain respects ``ocr_preferred_engine`` config:
        - ``"auto"`` (default): PaddleOCR → EasyOCR → Tesseract
        - specific engine: try it first, then fallback to the rest
        """
        preferred = self.config.get("ocr_preferred_engine", "auto")

        if preferred == "auto":
            engines = ["paddleocr", "easyocr", "tesseract"]
        elif preferred == "paddleocr":
            engines = ["paddleocr", "easyocr", "tesseract"]
        elif preferred == "easyocr":
            engines = ["easyocr", "paddleocr", "tesseract"]
        elif preferred == "tesseract":
            engines = ["tesseract", "paddleocr", "easyocr"]
        else:
            engines = ["paddleocr", "easyocr", "tesseract"]

        init_map = {
            "paddleocr": self._try_init_paddleocr,
            "easyocr": self._try_init_easyocr,
            "tesseract": self._try_init_tesseract,
        }

        for engine in engines:
            ocr = init_map[engine]()
            if ocr is not None:
                self._ocr = ocr
                self._ocr_engine = engine
                return

        self._ocr = None
        self._ocr_engine = None
        logger.warning(
            "No OCR engine available. Install paddleocr (recommended), "
            "easyocr, or ensure tesseract is installed with pytesseract "
            "Python package for OCR support."
        )

    def _paddle_lang(self) -> str:
        """Resolve the PaddleOCR language code from config.

        PaddleOCR does not accept tesseract-style codes (``deu+eng``) nor
        compound languages. We map the configured ``ocr_lang`` codes to
        Paddle's naming (``deu``→``german``, ``eng``→``en``) and use the
        FIRST configured language — one PaddleOCR instance handles one
        language at a time. ``ocr_paddle_lang`` overrides the mapping
        wholesale for unusual setups.
        """
        override = self.config.get("ocr_paddle_lang")
        if override:
            return str(override)
        ocr_lang = str(self.config.get("ocr_lang", "deu+eng"))
        first = ocr_lang.replace("+", ",").split(",")[0].strip()
        return {"deu": "german", "ger": "german", "de": "german", "eng": "en", "en": "en"}.get(first.lower(), first or "en")

    def _try_init_paddleocr(self):
        """Attempt to initialize PaddleOCR."""
        if self._ocr is False:
            return None
        try:
            logger.info("Initializing PaddleOCR synchronously in main thread")
            logger.info("Importing paddleocr module")
            paddle_module = importlib.import_module("paddleocr")
            paddle_ocr = getattr(paddle_module, "PaddleOCR")
            paddle_lang = self._paddle_lang()
            logger.info("PaddleOCR class found, initializing with device: %s, lang: %s", self.config.get("ocr_device", "cpu"), paddle_lang)

            ocr = paddle_ocr(
                use_angle_cls=True,
                lang=paddle_lang,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                device=self.config.get("ocr_device", "cpu"),
                ocr_version="PP-OCRv4",
            )
            logger.info("PaddleOCR initialized successfully")
            return ocr
        except ImportError as e:
            logger.error("Failed to import PaddleOCR: %s", e)
            return None
        except (RuntimeError, AssertionError) as e:
            logger.warning("PaddleOCR init error (type=%s): %s", type(e).__name__, e)
            if "PDX has already been initialized" in str(e) or "paddle is unexpectedly loaded" in str(e):
                logger.warning("PaddleX/paddlex conflict - attempting to reuse existing instance")
                try:
                    paddle_module = importlib.import_module("paddleocr")
                    paddle_ocr = getattr(paddle_module, "PaddleOCR")
                    ocr = paddle_ocr(
                        use_angle_cls=True,
                        lang=self._paddle_lang(),
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False,
                        device=self.config.get("ocr_device", "cpu"),
                        ocr_version="PP-OCRv4",
                    )
                    logger.info("PaddleOCR reinitialized successfully after PDX conflict")
                    return ocr
                except (RuntimeError, AssertionError):
                    logger.error("Cannot create PaddleOCR instance due to PaddleX initialization conflict")
                    return None
            else:
                logger.error("PaddleOCR initialization failed: %s", e)
                return None
        except Exception as e:
            logger.error("Unexpected error initializing PaddleOCR: %s", e)
            return None

    def _try_init_tesseract(self):
        """Attempt to initialize Tesseract via pytesseract."""
        try:
            import pytesseract

            pytesseract.get_tesseract_version()
            logger.info("Tesseract OCR available via pytesseract: %s", pytesseract.get_tesseract_version())

            ocr_lang = self.config.get("ocr_lang", "deu+eng")
            available = pytesseract.get_languages()
            requested = [lang.strip() for part in ocr_lang.split("+") for lang in part.split(",") if lang.strip()]
            missing = [lang for lang in requested if lang not in available]
            if missing:
                logger.warning(
                    "Tesseract language pack(s) not installed: %s. "
                    "Install with: sudo apt install tesseract-ocr-%s  "
                    "(or the equivalent package for your distribution). "
                    "Configured ocr_lang='%s', available: %s",
                    ", ".join(missing),
                    " ".join(missing),
                    ocr_lang,
                    available,
                )
            return True
        except Exception as e:
            logger.warning("Tesseract OCR not available: %s", e)
            return None

    def _try_init_easyocr(self):
        """Attempt to initialize EasyOCR.

        EasyOCR is tried as the secondary fallback (after PaddleOCR). It
        pulls in PyTorch so it is not a lightweight dependency — it is
        an *optional* install (see pyproject.toml
        ``[project.optional-dependencies].gpu``) and is **not** installed
        by a plain ``uv sync``. To enable EasyOCR:

            uv sync --group gpu       # PEP 735 (preferred)
            # or
            pip install -e ".[gpu]"   # PEP 621

        If the import fails we fall back silently so the rest of the
        pipeline keeps working — the install-command hint goes to the
        log so a curious operator can recover.
        """
        try:
            import easyocr

            lang = self.config.get("ocr_lang", "deu+eng")
            langs = [lang_code.replace("deu", "de").replace("eng", "en") for lang_code in lang.replace("+", " ").split()]
            reader = easyocr.Reader(langs, gpu=("gpu" in self.config.get("ocr_device", "cpu")))
            logger.info("EasyOCR initialized (langs=%s, gpu=%s)", langs, "gpu" in self.config.get("ocr_device", "cpu"))
            return reader
        except ImportError:
            logger.info(
                "EasyOCR not installed — skipping (no GPU OCR available). "
                "To enable: 'uv sync --group gpu' (or 'pip install -e .[gpu]')."
            )
            return None
        except Exception as e:
            logger.warning("EasyOCR initialization failed: %s", e)
            return None

    async def _process_with_easyocr(self, file_path: str, reader) -> dict[str, Any]:
        """Process with EasyOCR."""
        results = await asyncio.to_thread(reader.readtext, file_path)
        text = "\n".join(r[1] for r in results).strip()
        metadata = self._build_metadata(file_path, text, ocr_used=True)
        return {"text": text, "metadata": metadata, "ocr_used": True}

    def _get_ocr(self):
        """Get the OCR instance, initializing if needed (fallback for async contexts)."""
        if self._ocr is None:
            logger.warning("OCR not initialized synchronously, falling back to async initialization")
            self._initialize_ocr_sync()
        if self._ocr_engine in ("tesseract",):
            return True
        return self._ocr if self._ocr is not False and self._ocr is not None else None

    def _extract_paddle_text(self, results: Any) -> str:
        """Extract paddle text the instance."""
        blocks = []
        for result in results or []:
            if isinstance(result, dict):
                rec_texts = result.get("rec_texts")
                if isinstance(rec_texts, list):
                    blocks.extend(t for t in rec_texts if t)
                    continue
                for block in result.get("ocr_results", []):
                    if isinstance(block, dict):
                        text = block.get("text", "")
                        if text:
                            blocks.append(text)
            elif hasattr(result, "json"):
                json_attr = result.json
                if callable(json_attr):
                    json_payload = json_attr()
                else:
                    json_payload = json_attr
                if isinstance(json_payload, dict):
                    for block in json_payload.get("ocr_results", []):
                        if isinstance(block, dict):
                            text = block.get("text", "")
                            if text:
                                blocks.append(text)
        return "\n".join(blocks).strip()

    def _build_metadata(
        self,
        file_path: str,
        text: str,
        metadata: dict | None = None,
        ocr_used: bool = False,
    ) -> dict[str, Any]:
        """Build metadata internally."""
        path = Path(file_path)
        merged = dict(metadata or {})
        merged["source"] = path.name
        merged["extension"] = path.suffix.lower()
        merged["pages"] = int(merged.get("pages", 0) or 0)
        merged["word_count"] = len(text.split())
        merged["char_count"] = len(text)
        merged["ocr_used"] = ocr_used
        merged["ocr_engine"] = self._ocr_engine
        return merged

    def _check_version_compatibility(self):
        """Check for known PaddlePaddle version compatibility issues."""
        try:
            import paddle

            version_str = getattr(paddle, "__version__", "0.0.0")
            parts = version_str.split(".")
            major = parts[0]
            minor = parts[1] if len(parts) > 1 else "0"
            if major == "3" and int(minor) >= 3:
                logger.warning(
                    "PaddlePaddle 3.3+ has known PIR compatibility issues with OneDNN "
                    "that cause OCR crashes. Consider downgrading to PaddlePaddle 3.2.x "
                    "for stable OCR operations. See ADR-2024-05-12 for details."
                )
        except ImportError:
            pass
        except AssertionError:
            logger.debug("Skipping version compatibility check due to paddlex import conflict")
        except Exception as e:
            logger.warning("Failed to check PaddlePaddle version compatibility: %s", e)

    @property
    def ocr_engine(self) -> str | None:
        """Return the currently active OCR engine name, or None."""
        return self._ocr_engine
