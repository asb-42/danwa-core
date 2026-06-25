"""STTService — Speech-to-Text transcription service.

Supports local Whisper (faster-whisper) with CPU fallback.
Cloud providers (whisper-api, azure-stt, google-stt) are stubbed
for future implementation.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class STTService:
    """Transcribes audio to text using STT profiles.

    Usage::

        service = STTService()
        text = await service.transcribe_chunk(audio_bytes, profile)
    """

    async def transcribe_chunk(
        self,
        audio_bytes: bytes,
        profile: Any,
        language: str = "de",
    ) -> str:
        """Transcribe audio bytes to text.

        Args:
            audio_bytes: Raw audio data (e.g. WebM/Opus from browser).
            profile: An LLMProfile with protocol='stt'.
            language: Language code for transcription.

        Returns:
            Transcribed text.

        Raises:
            RuntimeError: If transcription fails.
        """
        provider = getattr(profile, "provider", "whisper-local")
        model = getattr(profile, "model", "base")

        if provider == "whisper-local":
            return await self._transcribe_whisper_local(audio_bytes, model, language)
        elif provider == "whisper-api":
            return await self._transcribe_whisper_api(audio_bytes, model, language, profile=profile)
        else:
            raise RuntimeError(f"STT provider {provider!r} is not yet implemented. Supported: whisper-local, whisper-api")

    async def transcribe_file(
        self,
        file_path: Path,
        profile: Any,
        language: str = "de",
    ) -> str:
        """Transcribe an audio file to text.

        Args:
            file_path: Path to the audio file.
            profile: An LLMProfile with protocol='stt'.
            language: Language code.

        Returns:
            Transcribed text.
        """
        audio_bytes = file_path.read_bytes()
        return await self.transcribe_chunk(audio_bytes, profile, language)

    async def _transcribe_whisper_local(
        self,
        audio_bytes: bytes,
        model: str,
        language: str,
    ) -> str:
        """Transcribe using faster-whisper (local, CPU-capable)."""
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError("faster-whisper is not installed. Install with: pip install faster-whisper")

        # Write audio bytes to temp file
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = Path(tmp.name)

        try:
            # Load model (CPU fallback)
            whisper_model = WhisperModel(model, device="cpu", compute_type="int8")
            segments, info = whisper_model.transcribe(
                str(tmp_path),
                language=language,
                beam_size=5,
            )
            text_parts = [segment.text for segment in segments]
            result = " ".join(text_parts).strip()
            logger.info(
                "Whisper transcription complete: %d chars (model=%s, lang=%s)",
                len(result),
                model,
                language,
            )
            return result
        finally:
            tmp_path.unlink(missing_ok=True)

    async def _transcribe_whisper_api(
        self,
        audio_bytes: bytes,
        model: str,
        language: str,
        profile: Any = None,
    ) -> str:
        """Transcribe using OpenAI-compatible Whisper API (cloud).

        Uses the profile's api_key/api_key_env and api_base fields.
        Falls back to ``https://api.openai.com/v1`` if no api_base is set.
        """
        import os

        import httpx

        # Resolve API key
        api_key: str | None = None
        if profile:
            api_key = getattr(profile, "api_key", None)
            if not api_key:
                key_env = getattr(profile, "api_key_env", "OPENAI_API_KEY")
                api_key = os.environ.get(key_env)

        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY", "")

        if not api_key:
            raise RuntimeError(
                "No API key configured for Whisper API. Set api_key on the "
                "LLMProfile or export OPENAI_API_KEY."
            )

        # Resolve base URL
        base_url = "https://api.openai.com/v1"
        if profile:
            custom_base = getattr(profile, "api_base", None)
            if custom_base:
                base_url = custom_base.rstrip("/")

        url = f"{base_url}/audio/transcriptions"

        # Build multipart form data
        files = {
            "file": ("audio.wav", audio_bytes, "audio/wav"),
        }
        data = {
            "model": model,
            "language": language,
            "response_format": "text",
        }

        timeout = getattr(profile, "timeout", 60) if profile else 60

        try:
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    files=files,
                    data=data,
                )
                response.raise_for_status()
                result = response.text.strip()
                logger.info(
                    "Whisper API transcription complete: %d chars (model=%s, lang=%s)",
                    len(result),
                    model,
                    language,
                )
                return result
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                f"Whisper API returned {exc.response.status_code}: {exc.response.text}"
            ) from exc
        except httpx.RequestError as exc:
            raise RuntimeError(f"Whisper API request failed: {exc}") from exc
