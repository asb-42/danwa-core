"""Extra tests for backend.llm_catalog.id_strategy — edge cases."""

from __future__ import annotations

from backend.llm_catalog.id_strategy import (
    _strip_provider_prefix,
    display_name,
    module_id_for,
    module_id_for_provider_model,
    normalize_model_name,
)


# ---------------------------------------------------------------------------
# _strip_provider_prefix
# ---------------------------------------------------------------------------


def test_strip_prefix_empty() -> None:
    assert _strip_provider_prefix("") == ""


def test_strip_prefix_whitespace_only() -> None:
    # Whitespace-only input has no provider prefix to strip.
    assert _strip_provider_prefix("   ").strip() == ""


def test_strip_prefix_openai() -> None:
    assert _strip_provider_prefix("openai/gpt-4o") == "gpt-4o"


def test_strip_prefix_anthropic() -> None:
    assert _strip_provider_prefix("anthropic/claude-3") == "claude-3"


def test_strip_prefix_google() -> None:
    assert _strip_provider_prefix("google/gemini-1.5-pro") == "gemini-1.5-pro"


def test_strip_prefix_meta_llama() -> None:
    assert _strip_provider_prefix("meta-llama/llama-3-70b") == "llama-3-70b"


def test_strip_prefix_mistralai() -> None:
    assert _strip_provider_prefix("mistralai/mistral-large") == "mistral-large"


def test_strip_prefix_models_prefix() -> None:
    assert _strip_provider_prefix("models/gemini-1.5-pro") == "gemini-1.5-pro"


def test_strip_prefix_dot_separator() -> None:
    """Anthropic style: ``anthropic.claude-3-5-sonnet-20241022``."""
    assert _strip_provider_prefix("anthropic.claude-3-5-sonnet-20241022") == "claude-3-5-sonnet-20241022"


def test_strip_prefix_no_separator() -> None:
    assert _strip_provider_prefix("gpt-4o") == "gpt-4o"


# ---------------------------------------------------------------------------
# normalize_model_name
# ---------------------------------------------------------------------------


def test_normalize_collapses_whitespace() -> None:
    assert normalize_model_name("  GPT 4o  ") == "gpt-4o"


def test_normalize_uppercase_to_lowercase() -> None:
    assert normalize_model_name("GPT-4O") == "gpt-4o"


def test_normalize_strips_trailing_whitespace() -> None:
    assert normalize_model_name("gpt-4o\n") == "gpt-4o"


# ---------------------------------------------------------------------------
# module_id_for
# ---------------------------------------------------------------------------


def test_module_id_for_different_sources_differ() -> None:
    """Same (provider, model) under different sources → different ids."""
    a = module_id_for("catwalk", "openai", "gpt-4o")
    b = module_id_for("llm_db", "openai", "gpt-4o")
    assert a != b


def test_module_id_for_starts_with_prefix() -> None:
    a = module_id_for("catwalk", "openai", "gpt-4o")
    assert a.startswith("llm-")


def test_module_id_for_provider_model_case_insensitive() -> None:
    a = module_id_for_provider_model("OpenAI", "GPT-4O")
    b = module_id_for_provider_model("openai", "gpt-4o")
    assert a == b


def test_module_id_for_provider_model_8_hex_chars() -> None:
    a = module_id_for_provider_model("openai", "gpt-4o")
    suffix = a.removeprefix("llm-")
    assert len(suffix) == 8
    int(suffix, 16)  # must be valid hex


# ---------------------------------------------------------------------------
# display_name
# ---------------------------------------------------------------------------


def test_display_name_uses_upstream_name() -> None:
    assert display_name("catwalk", "openai", "gpt-4o", "GPT-4o (2024)") == "GPT-4o (2024)"


def test_display_name_falls_back() -> None:
    assert display_name("catwalk", "openai", "gpt-4o", None) == "gpt-4o (openai)"


def test_display_name_empty_upstream_uses_fallback() -> None:
    assert display_name("catwalk", "openai", "gpt-4o", "") == "gpt-4o (openai)"


def test_display_name_whitespace_upstream_uses_fallback() -> None:
    assert display_name("catwalk", "openai", "gpt-4o", "   ") == "gpt-4o (openai)"
