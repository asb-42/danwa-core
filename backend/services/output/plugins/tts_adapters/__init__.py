"""TTS Adapters — built-in adapter implementations.

Importing this package registers all built-in adapters with the
:class:`TTSAdapterRegistry`.
"""

from backend.services.output.plugins.tts_adapters.edge_adapter import EdgeTTSAdapter  # noqa: F401
from backend.services.output.plugins.tts_adapters.mimo_adapter import MiMoTTSAdapter  # noqa: F401
from backend.services.output.plugins.tts_adapters.pyttsx3_adapter import Pyttsx3Adapter  # noqa: F401

__all__ = ["EdgeTTSAdapter", "MiMoTTSAdapter", "Pyttsx3Adapter"]
