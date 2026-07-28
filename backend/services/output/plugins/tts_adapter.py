"""TTSAdapter — abstract base class and registry for TTS engine adapters.

All TTS adapters must subclass ``TTSAdapter`` and are registered via the
``@register_adapter`` decorator.  The registry provides lookup by engine ID
and lists all available engines with license metadata.

License: AGPL-3.0 — part of Danwa core.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract Base Class
# ---------------------------------------------------------------------------


class TTSAdapter(ABC):
    """Abstract base for TTS engine adapters.

    Each adapter wraps a specific TTS engine (edge-tts, MiMo, pyttsx3,
    Fish Speech, etc.) and provides a uniform interface for segment
    synthesis, voice listing, and license metadata.

    Adapters are **stateless** — a fresh instance is created per render call.
    """

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @staticmethod
    @abstractmethod
    def name() -> str:
        """Unique engine identifier (e.g. ``"edge_tts"``, ``"mimo_tts"``)."""

    @staticmethod
    @abstractmethod
    def display_name() -> str:
        """Human-readable name for UI (e.g. ``"Edge TTS"``)."""

    # ------------------------------------------------------------------
    # Availability
    # ------------------------------------------------------------------

    @abstractmethod
    def is_available(self) -> bool:
        """Check if adapter can be used.

        Returns ``True`` if all dependencies are installed and, where
        applicable, the remote endpoint is reachable.
        """

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    @abstractmethod
    async def synthesize_segment(
        self,
        text: str,
        voice: str,
        output_path: Path,
        *,
        style_hint: str = "",
        **kwargs: Any,
    ) -> None:
        """Synthesize a single text segment to an audio file.

        Args:
            text: The text to synthesize.
            voice: Voice identifier (engine-specific).
            output_path: Target audio file path.
            style_hint: Optional natural language style description.
            **kwargs: Engine-specific extra parameters.
        """

    # ------------------------------------------------------------------
    # Voices
    # ------------------------------------------------------------------

    @abstractmethod
    def list_voices(
        self,
        language: str | None = None,
        gender: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return available voices, optionally filtered.

        Args:
            language: Filter by language prefix (e.g. ``"de"``).
            gender: Filter by gender (``"Male"`` / ``"Female"``).

        Returns:
            List of voice dicts with at least keys:
            ``voice_id``, ``name``, ``language``, ``gender``.
        """

    # ------------------------------------------------------------------
    # Capabilities
    # ------------------------------------------------------------------

    @property
    def supports_style_hints(self) -> bool:
        """Whether this adapter supports natural language style hints."""
        return False

    # ------------------------------------------------------------------
    # License
    # ------------------------------------------------------------------

    @abstractmethod
    def license_info(self) -> dict[str, str]:
        """Return license metadata.

        Returns:
            Dict with keys:
            - ``name``: License name (e.g. ``"AGPL-3.0"``)
            - ``type``: One of ``"free"``, ``"non-commercial"``, ``"proprietary"``
            - ``url``: License URL
            - ``attribution``: Required attribution text (if any)
        """


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TTSAdapterRegistry:
    """Singleton registry for TTS adapters.

    Adapters register themselves via the ``@register_adapter`` decorator
    at import time.
    """

    _instance: TTSAdapterRegistry | None = None
    _adapter_classes: dict[str, type[TTSAdapter]] = {}

    def __new__(cls) -> TTSAdapterRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def register(cls, adapter_cls: type[TTSAdapter]) -> type[TTSAdapter]:
        """Register an adapter class.  Used as a decorator."""
        engine_id = adapter_cls.name()
        cls._adapter_classes[engine_id] = adapter_cls
        logger.debug("Registered TTS adapter: %s (%s)", engine_id, adapter_cls.display_name())
        return adapter_cls

    @classmethod
    def get(cls, engine: str) -> TTSAdapter:
        """Instantiate and return adapter for engine ID.

        Raises:
            ValueError: If no adapter is registered for the engine.
        """
        if engine not in cls._adapter_classes:
            available = ", ".join(sorted(cls._adapter_classes))
            raise ValueError(
                f"No TTS adapter registered for engine '{engine}'. "
                f"Available engines: {available}"
            )
        return cls._adapter_classes[engine]()

    @classmethod
    def available_engines(cls) -> list[dict[str, Any]]:
        """List all registered engines with availability and license info.

        Returns:
            List of dicts with keys: ``engine_id``, ``display_name``,
            ``available``, ``license``.
        """
        engines: list[dict[str, Any]] = []
        for engine_id, adapter_cls in sorted(cls._adapter_classes.items()):
            adapter = adapter_cls()
            engines.append(
                {
                    "engine_id": engine_id,
                    "display_name": adapter_cls.display_name(),
                    "available": adapter.is_available(),
                    "license": adapter.license_info(),
                }
            )
        return engines

    @classmethod
    def is_registered(cls, engine: str) -> bool:
        """Check if an adapter is registered for the given engine ID."""
        return engine in cls._adapter_classes


def register_adapter(cls: type[TTSAdapter]) -> type[TTSAdapter]:
    """Decorator to register a TTS adapter with the global registry."""
    return TTSAdapterRegistry.register(cls)
