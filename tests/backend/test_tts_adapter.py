"""Tests for TTSAdapterRegistry and built-in adapters."""

from __future__ import annotations

import pytest

from backend.services.output.plugins.tts_adapter import (
    TTSAdapter,
    TTSAdapterRegistry,
    register_adapter,
)


class TestTTSAdapterABC:
    """Test the abstract base class contract."""

    def test_cannot_instantiate_abstract(self) -> None:
        """Cannot instantiate TTSAdapter directly."""
        with pytest.raises(TypeError):
            TTSAdapter()  # type: ignore[abstract]

    def test_subclass_must_implement_all(self) -> None:
        """Subclass missing methods cannot be instantiated."""

        class IncompleteAdapter(TTSAdapter):
            @staticmethod
            def name() -> str:
                return "incomplete"

            # Missing: display_name, is_available, synthesize_segment, etc.

        with pytest.raises(TypeError):
            IncompleteAdapter()  # type: ignore[abstract]


class TestTTSAdapterRegistry:
    def test_singleton(self) -> None:
        """Registry is a singleton."""
        r1 = TTSAdapterRegistry()
        r2 = TTSAdapterRegistry()
        assert r1 is r2

    def test_register_and_lookup(self) -> None:
        """Register an adapter and look it up."""

        @register_adapter
        class DummyAdapter(TTSAdapter):
            @staticmethod
            def name() -> str:
                return "dummy_test"

            @staticmethod
            def display_name() -> str:
                return "Dummy Test"

            def is_available(self) -> bool:
                return True

            async def synthesize_segment(self, text, voice, output_path, **kwargs):
                pass

            def list_voices(self, language=None, gender=None):
                return []

            def license_info(self):
                return {"name": "Test", "type": "free", "url": "", "attribution": ""}

        assert TTSAdapterRegistry.is_registered("dummy_test")
        adapter = TTSAdapterRegistry.get("dummy_test")
        assert adapter.name() == "dummy_test"

        # Clean up
        del TTSAdapterRegistry._adapter_classes["dummy_test"]

    def test_is_registered(self) -> None:
        """is_registered returns correct boolean."""
        assert TTSAdapterRegistry.is_registered("edge_tts") is True
        assert TTSAdapterRegistry.is_registered("nonexistent") is False

    def test_builtin_adapters_are_available(self) -> None:
        """Built-in adapters register on import."""
        import backend.services.output.plugins.tts_adapters  # noqa: F401

        engines = TTSAdapterRegistry.available_engines()
        assert len(engines) >= 3  # edge, mimo, pyttsx3

    def test_engine_license_types(self) -> None:
        """All built-in engines have valid license types."""
        import backend.services.output.plugins.tts_adapters  # noqa: F401

        valid_types = {"free", "non-commercial", "proprietary"}
        for engine in TTSAdapterRegistry.available_engines():
            assert engine["license"]["type"] in valid_types
