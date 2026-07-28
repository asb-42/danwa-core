"""EdgeTTSAdapter — wraps edge-tts for use with the TTS adapter interface.

License: AGPL-3.0 — part of Danwa core.
Edge-TTS itself uses Microsoft Edge's online TTS service (free, no API key).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from backend.services.output.plugins.tts_adapter import TTSAdapter, register_adapter

logger = logging.getLogger(__name__)


@register_adapter
class EdgeTTSAdapter(TTSAdapter):
    """Adapter for Microsoft Edge TTS via the ``edge-tts`` library.

    Free, online, multilingual.  No API key required.
    """

    @staticmethod
    def name() -> str:
        return "edge_tts"

    @staticmethod
    def display_name() -> str:
        return "Edge TTS"

    def is_available(self) -> bool:
        try:
            import edge_tts  # noqa: F401
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
            import edge_tts
        except ImportError:
            raise RuntimeError("edge-tts is not installed. Install with: pip install edge-tts")

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(str(output_path))

    def list_voices(
        self,
        language: str | None = None,
        gender: str | None = None,
    ) -> list[dict[str, Any]]:
        from backend.services.output.plugins.voice_store import VoiceStore

        store = VoiceStore()
        return store.list_voices(language=language, gender=gender)

    def license_info(self) -> dict[str, str]:
        return {
            "name": "MIT (edge-tts library) / Microsoft Edge TTS service",
            "type": "free",
            "url": "https://github.com/rany2/edge-tts",
            "attribution": "",
        }
