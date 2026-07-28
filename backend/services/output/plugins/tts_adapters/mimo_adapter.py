"""MiMoTTSAdapter — wraps MiMo-V2.5-TTS for use with the TTS adapter interface.

License: AGPL-3.0 — part of Danwa core (adapter code only).
MiMo TTS API: Xiaomi's TTS service (requires API key).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from backend.services.output.plugins.tts_adapter import TTSAdapter, register_adapter

logger = logging.getLogger(__name__)

# Default MiMo TTS configuration
_DEFAULT_API_BASE = "https://api.xiaomimimo.com/v1"
_DEFAULT_MODEL = "mimo-v2.5-tts"
_DEFAULT_VOICE = "Mia"

# Available MiMo voices
MIMO_VOICES: list[dict[str, Any]] = [
    {"voice_id": "Mia", "name": "Mia", "language": "en", "gender": "Female"},
    {"voice_id": "Chloe", "name": "Chloe", "language": "en", "gender": "Female"},
    {"voice_id": "Milo", "name": "Milo", "language": "en", "gender": "Male"},
    {"voice_id": "Dean", "name": "Dean", "language": "en", "gender": "Male"},
    {"voice_id": "冰糖", "name": "冰糖", "language": "zh", "gender": "Female"},
    {"voice_id": "茉莉", "name": "茉莉", "language": "zh", "gender": "Female"},
    {"voice_id": "苏打", "name": "苏打", "language": "zh", "gender": "Male"},
    {"voice_id": "白桦", "name": "白桦", "language": "zh", "gender": "Male"},
]


@register_adapter
class MiMoTTSAdapter(TTSAdapter):
    """Adapter for Xiaomi MiMo-V2.5-TTS API.

    Supports style hints via natural language.  Requires an API key
    (``XIAOMI_API_KEY`` env var or auto-resolved from LLM profile).
    """

    def __init__(
        self,
        api_base: str | None = None,
        api_key: str | None = None,
        api_key_env: str = "XIAOMI_API_KEY",
        model: str = _DEFAULT_MODEL,
    ) -> None:
        self._api_base = (api_base or _DEFAULT_API_BASE).rstrip("/")
        if not self._api_base.endswith("/v1"):
            self._api_base = f"{self._api_base}/v1"
        self._api_key = api_key or os.getenv(api_key_env, "")
        self._model = model
        self._api_key_env = api_key_env

    @staticmethod
    def name() -> str:
        return "mimo_tts"

    @staticmethod
    def display_name() -> str:
        return "MiMo TTS"

    def is_available(self) -> bool:
        try:
            import httpx  # noqa: F401
        except ImportError:
            return False
        # Check if API key is available (from param, env, or profile)
        if self._api_key:
            return True
        # Try auto-resolving from LLM profile
        try:
            from backend.blueprints.repository import BlueprintRepository

            repo = BlueprintRepository()
            for profile in repo.list_llm_profiles(limit=500):
                if profile.profile_type == "tts" and profile.provider == "xiaomi":
                    return True
        except Exception:
            pass
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
        from backend.services.output.plugins.mimo_tts_renderer import MiMoTTSRenderer

        renderer = MiMoTTSRenderer(
            api_base=self._api_base,
            api_key=self._api_key or None,
            api_key_env=self._api_key_env,
            model=self._model,
        )
        await renderer._render_segment(
            text=text,
            voice=voice or _DEFAULT_VOICE,
            style_hint=style_hint,
            output_path=output_path,
        )

    def list_voices(
        self,
        language: str | None = None,
        gender: str | None = None,
    ) -> list[dict[str, Any]]:
        voices = MIMO_VOICES
        if language:
            voices = [v for v in voices if v["language"].startswith(language)]
        if gender:
            voices = [v for v in voices if v["gender"].lower() == gender.lower()]
        return voices

    @property
    def supports_style_hints(self) -> bool:
        return True

    def license_info(self) -> dict[str, str]:
        return {
            "name": "MiMo TTS API Terms",
            "type": "free",
            "url": "https://platform.xiaomi.com",
            "attribution": "",
        }

    @property
    def default_voice(self) -> str:
        return _DEFAULT_VOICE

    @property
    def valid_voices(self) -> set[str]:
        return {v["voice_id"] for v in MIMO_VOICES}
