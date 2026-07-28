"""Pyttsx3Adapter — wraps pyttsx3 (espeak-ng) for use with the TTS adapter interface.

License: AGPL-3.0 — part of Danwa core.
pyttsx3 itself is MIT-licensed; espeak-ng is GPL-2.0.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from backend.services.output.plugins.tts_adapter import TTSAdapter, register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class Pyttsx3Adapter(TTSAdapter):
    """Adapter for offline TTS via pyttsx3 (espeak-ng backend).

    No API key needed.  Lower quality than online services.
    """

    @staticmethod
    def name() -> str:
        return "pyttsx3"

    @staticmethod
    def display_name() -> str:
        return "pyttsx3 (Offline)"

    def is_available(self) -> bool:
        try:
            import pyttsx3  # noqa: F401
            return True
        except ImportError:
            return False

    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        output_path: Path,
        *,
        style_hint: str = "",
        **kwargs: Any,
    ) -> None:
        try:
            import pyttsx3 as _pyttsx3  # noqa: F401
        except ImportError:
            raise RuntimeError("pyttsx3 is not installed. Install with: pip install pyttsx3>=2.90")

        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            self._render_segment,
            text,
            voice,
            output_path,
        )

    @staticmethod
    def _render_segment(text: str, voice_id: str, output_path: Path) -> None:
        """Render a single text segment to WAV via pyttsx3.

        Runs synchronously — caller should wrap in ``run_in_executor``.
        """
        import pyttsx3

        engine = pyttsx3.init()
        if voice_id:
            engine.setProperty("voice", voice_id)
        engine.save_to_file(text, str(output_path))
        engine.runAndWait()

    def list_voices(
        self,
        language: str | None = None,
        gender: str | None = None,
    ) -> list[dict[str, Any]]:
        """List available pyttsx3/espeak voices.

        Note: Voice metadata is limited compared to edge-tts.
        """
        try:
            import pyttsx3
        except ImportError:
            return []

        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        result: list[dict[str, Any]] = []
        for v in voices:
            entry: dict[str, Any] = {
                "voice_id": v.id,
                "name": v.name,
                "language": getattr(v, "languages", [""])[0] if hasattr(v, "languages") and v.languages else "",
                "gender": "Unknown",
            }
            if language and not entry["language"].startswith(language):
                continue
            if gender and entry["gender"] != gender:
                continue
            result.append(entry)
        return result

    def license_info(self) -> dict[str, str]:
        return {
            "name": "MIT (pyttsx3) / GPL-2.0 (espeak-ng)",
            "type": "free",
            "url": "https://github.com/nateshmbhat/pyttsx3",
            "attribution": "",
        }
