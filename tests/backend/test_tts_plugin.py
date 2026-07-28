"""Tests for TTSOutputPlugin, TTSPluginConfig, TTSScriptEngine, and TTSAdapterRegistry."""

from __future__ import annotations

import pytest

from backend.models.artifact import (
    DebateArtifact,
    Injection,
    Turn,
    UserQuery,
)
from backend.services.output.plugins.tts_adapter import TTSAdapterRegistry
from backend.services.output.plugins.tts_plugin import (
    AudioFormat,
    TTSOutputPlugin,
    TTSPluginConfig,
)
from backend.services.output.plugins.tts_script_engine import TTSScriptEngine


class TestTTSPluginConfig:
    def test_defaults(self) -> None:
        c = TTSPluginConfig()
        assert c.engine == "edge_tts"
        assert c.default_voice == "de-DE-KatjaNeural"
        assert c.segment_pause_ms == 800
        assert c.turn_pause_ms == 300
        assert c.output_format == AudioFormat.MP3
        assert c.bitrate == "128k"

    def test_custom(self) -> None:
        c = TTSPluginConfig(
            voice_mapping={"Alice": "de-DE-ConradNeural"},
            intro_text="Welcome",
            outro_text="Goodbye",
        )
        assert c.voice_mapping["Alice"] == "de-DE-ConradNeural"
        assert c.intro_text == "Welcome"

    def test_engine_string_id(self) -> None:
        """Engine accepts string IDs."""
        c = TTSPluginConfig(engine="pyttsx3")
        assert c.engine == "pyttsx3"

    def test_engine_default(self) -> None:
        """Engine defaults to edge_tts."""
        c = TTSPluginConfig()
        assert c.engine == "edge_tts"


class TestTTSScriptEngine:
    def _make_artifact(self) -> DebateArtifact:
        return DebateArtifact(
            session_id="s1",
            workflow_id="w1",
            topic="Test",
            transcript=[
                Turn(
                    id="t1",
                    round=1,
                    node_id="n1",
                    agent_name="Alice",
                    role_type="strategist",
                    content="Hello world",
                ),
                Turn(
                    id="t2",
                    round=1,
                    node_id="n2",
                    agent_name="Bob",
                    role_type="critic",
                    content="I disagree",
                ),
            ],
            interjections=[
                Injection(
                    id="ij1",
                    source="user",
                    target_node_id="n1",
                    content="Extra info",
                ),
            ],
            user_queries=[
                UserQuery(id="q1", content="Why?", response_turn_id="t1"),
            ],
        )

    def test_basic_segments(self) -> None:
        engine = TTSScriptEngine()
        script = engine.transform(
            self._make_artifact(),
            voice_mapping={},
            default_voice="de-DE-KatjaNeural",
        )
        # 2 turns + 1 injection + 1 query = 4 segments
        assert len(script.segments) == 4

    def test_intro_outro(self) -> None:
        engine = TTSScriptEngine()
        script = engine.transform(
            self._make_artifact(),
            voice_mapping={},
            default_voice="de-DE-KatjaNeural",
            intro_text="Welcome to the debate",
            outro_text="Thank you for listening",
        )
        # intro + 4 + outro = 6
        assert len(script.segments) == 6
        assert script.segments[0].is_intro is True
        assert script.segments[-1].is_outro is True

    def test_voice_resolution(self) -> None:
        engine = TTSScriptEngine()
        script = engine.transform(
            self._make_artifact(),
            voice_mapping={"Alice": "de-DE-ConradNeural"},
            default_voice="de-DE-KatjaNeural",
        )
        # Find Alice's turn
        alice_turns = [s for s in script.segments if s.speaker_name == "Alice"]
        assert len(alice_turns) == 1
        assert alice_turns[0].voice_id == "de-DE-ConradNeural"

        # Bob should get default
        bob_turns = [s for s in script.segments if s.speaker_name == "Bob"]
        assert len(bob_turns) == 1
        assert bob_turns[0].voice_id == "de-DE-KatjaNeural"

    def test_injection_hint(self) -> None:
        engine = TTSScriptEngine()
        script = engine.transform(
            self._make_artifact(),
            voice_mapping={},
            default_voice="de-DE-KatjaNeural",
        )
        inj_segments = [s for s in script.segments if s.injection_reference]
        assert len(inj_segments) == 1
        assert "Zwischenfrage" in inj_segments[0].text

    def test_metadata(self) -> None:
        engine = TTSScriptEngine()
        script = engine.transform(
            self._make_artifact(),
            voice_mapping={},
            default_voice="de-DE-KatjaNeural",
        )
        assert script.metadata["topic"] == "Test"
        assert script.metadata["total_segments"] == 4

    def test_style_hints_with_mimo(self) -> None:
        """Style hints are applied when engine supports them."""
        engine = TTSScriptEngine()
        script = engine.transform(
            self._make_artifact(),
            voice_mapping={},
            default_voice="Mia",
            engine="mimo_tts",
        )
        # MiMo adapter supports style hints
        for seg in script.segments:
            if seg.speaker_role in ("strategist", "critic"):
                assert seg.style_hint != ""

    def test_no_style_hints_with_edge(self) -> None:
        """Style hints are empty when engine does not support them."""
        engine = TTSScriptEngine()
        script = engine.transform(
            self._make_artifact(),
            voice_mapping={},
            default_voice="de-DE-KatjaNeural",
            engine="edge_tts",
        )
        for seg in script.segments:
            assert seg.style_hint == ""


class TestTTSOutputPlugin:
    def test_plugin_properties(self) -> None:
        assert TTSOutputPlugin.plugin_key == "tts"
        assert "mp3" in TTSOutputPlugin.supported_formats
        assert "wav" in TTSOutputPlugin.supported_formats

    def test_pyttsx3_engine_config(self) -> None:
        """TTSPluginConfig accepts pyttsx3 as engine string."""
        c = TTSPluginConfig(engine="pyttsx3")
        assert c.engine == "pyttsx3"

    def test_pyttsx3_renderer_import(self) -> None:
        """Pyttsx3Renderer can be imported without pyttsx3 installed."""
        from backend.services.output.plugins.pyttsx3_renderer import Pyttsx3Renderer

        renderer = Pyttsx3Renderer()
        assert renderer is not None
        assert hasattr(renderer, "render")
        assert hasattr(renderer, "_render_segment")


class TestTTSAdapterRegistry:
    def test_builtin_engines_registered(self) -> None:
        """All built-in engines are registered."""
        # Ensure adapters are imported
        import backend.services.output.plugins.tts_adapters  # noqa: F401

        assert TTSAdapterRegistry.is_registered("edge_tts")
        assert TTSAdapterRegistry.is_registered("mimo_tts")
        assert TTSAdapterRegistry.is_registered("pyttsx3")

    def test_get_adapter(self) -> None:
        """Can retrieve adapter by engine ID."""
        import backend.services.output.plugins.tts_adapters  # noqa: F401

        adapter = TTSAdapterRegistry.get("edge_tts")
        assert adapter is not None
        assert adapter.name() == "edge_tts"

    def test_get_unknown_engine_raises(self) -> None:
        """Unknown engine raises ValueError."""
        with pytest.raises(ValueError, match="No TTS adapter registered"):
            TTSAdapterRegistry.get("unknown_engine")

    def test_available_engines(self) -> None:
        """available_engines returns all registered engines."""
        import backend.services.output.plugins.tts_adapters  # noqa: F401

        engines = TTSAdapterRegistry.available_engines()
        engine_ids = [e["engine_id"] for e in engines]
        assert "edge_tts" in engine_ids
        assert "mimo_tts" in engine_ids
        assert "pyttsx3" in engine_ids

    def test_engine_has_license_info(self) -> None:
        """Each engine has license metadata."""
        import backend.services.output.plugins.tts_adapters  # noqa: F401

        engines = TTSAdapterRegistry.available_engines()
        for engine in engines:
            license_info = engine["license"]
            assert "name" in license_info
            assert "type" in license_info
            assert license_info["type"] in ("free", "non-commercial", "proprietary")

    def test_adapter_supports_style_hints(self) -> None:
        """MiMo adapter reports style hint support."""
        import backend.services.output.plugins.tts_adapters  # noqa: F401

        adapter = TTSAdapterRegistry.get("mimo_tts")
        assert adapter.supports_style_hints is True

    def test_edge_adapter_no_style_hints(self) -> None:
        """Edge adapter does not support style hints."""
        import backend.services.output.plugins.tts_adapters  # noqa: F401

        adapter = TTSAdapterRegistry.get("edge_tts")
        assert adapter.supports_style_hints is False
