"""Tests for the deterministic LLM-catalog id strategy."""

from __future__ import annotations

from backend.llm_catalog.id_strategy import (
    display_name,
    module_id_for,
    module_id_for_provider_model,
    normalize_model_name,
)


def test_normalize_model_name_lowercases():
    assert normalize_model_name("GPT-4o") == "gpt-4o"
    assert normalize_model_name("OpenAI/GPt-4O") == "gpt-4o"


def test_normalize_model_name_strips_provider_prefix():
    # Common vendor prefixes used by OpenRouter and friends
    for raw in (
        "openai/gpt-4o",
        "anthropic/claude-3-5-sonnet",
        "google/gemini-1.5-pro",
        "mistralai/mistral-large",
    ):
        assert "/" not in normalize_model_name(raw), raw


def test_normalize_model_name_strips_models_prefix():
    assert normalize_model_name("models/gemini-1.5-pro") == "gemini-1.5-pro"


def test_normalize_model_name_handles_dots():
    # Gemini / Anthropic use dot separators — take the tail
    assert (
        normalize_model_name("anthropic.claude-3-5-sonnet-20241022")
        == "claude-3-5-sonnet-20241022"
    )


def test_module_id_for_is_deterministic():
    a = module_id_for("catwalk", "openai", "gpt-4o")
    b = module_id_for("catwalk", "openai", "gpt-4o")
    assert a == b
    assert a.startswith("llm-")
    # 8 hex chars after prefix
    assert len(a) == len("llm-") + 8


def test_module_id_for_includes_source():
    """Source-aware id: same model in catwalk vs llm_db gets different ids."""
    a = module_id_for("catwalk", "openai", "gpt-4o")
    b = module_id_for("llm_db", "openai", "gpt-4o")
    assert a != b


def test_module_id_for_provider_model_collapses_sources():
    """Source-agnostic id: same (provider, model) → same id regardless of source."""
    a = module_id_for_provider_model("openai", "gpt-4o")
    b = module_id_for_provider_model("openai", "GPT-4O")  # case-insensitive
    assert a == b
    assert a.startswith("llm-")
    # 8 hex chars
    assert len(a) == len("llm-") + 8


def test_module_id_handles_case_insensitive():
    """GPT-4o == gpt-4o == Gpt-4O (case + whitespace + dash insensitivity)."""
    variants = ["gpt-4o", "GPT-4O", "Gpt-4O", "gpt-4O"]
    ids = {module_id_for_provider_model("openai", v) for v in variants}
    assert len(ids) == 1, f"non-deterministic ids: {ids}"


def test_display_name_falls_back_to_provider_model():
    assert display_name("catwalk", "openai", "gpt-4o-mini", None) == "gpt-4o-mini (openai)"


def test_display_name_prefers_upstream_name():
    assert display_name("catwalk", "openai", "gpt-4o", "GPT-4o") == "GPT-4o"
