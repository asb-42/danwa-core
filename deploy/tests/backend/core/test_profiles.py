"""Tests for backend.core.profiles — Pydantic schemas for LLM profiles."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.core.profiles import (
    LLMProfile,
    LLMProvider,
    ActiveConfiguration,
)


# ---------------------------------------------------------------------------
# LLMProvider enum
# ---------------------------------------------------------------------------


def test_llm_provider_values() -> None:
    assert LLMProvider.OPENROUTER == "openrouter"
    assert LLMProvider.OPENAI == "openai"
    assert LLMProvider.ANTHROPIC == "anthropic"
    assert LLMProvider.OLLAMA == "ollama"
    assert LLMProvider.DEEPSEEK == "deepseek"
    assert LLMProvider.CLOUDFLARE == "cloudflare"


def test_llm_provider_string_alias() -> None:
    assert LLMProvider("openai") == LLMProvider.OPENAI


# ---------------------------------------------------------------------------
# LLMProfile — defaults
# ---------------------------------------------------------------------------


def test_llm_profile_minimal() -> None:
    p = LLMProfile(name="Test", provider=LLMProvider.OPENAI, model="gpt-4o")
    assert p.name == "Test"
    assert p.profile_type == "text"
    assert p.max_tokens == 4096
    assert p.temperature == 0.7
    assert p.timeout == 600
    assert p.protocol == "litellm"
    assert p.service_eligible is True


def test_llm_profile_with_id() -> None:
    p = LLMProfile(id="llm-abc12345", name="X", provider=LLMProvider.OPENAI, model="gpt-4o")
    assert p.id == "llm-abc12345"


def test_llm_profile_invalid_id_pattern() -> None:
    with pytest.raises(ValidationError):
        LLMProfile(id="UPPER", name="X", provider=LLMProvider.OPENAI, model="x")


# ---------------------------------------------------------------------------
# LLMProfile — temperature validator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("temp", [-0.1, 2.1, 5.0, -10.0])
def test_llm_profile_temperature_out_of_range_raises(temp: float) -> None:
    with pytest.raises(ValidationError):
        LLMProfile(name="X", provider=LLMProvider.OPENAI, model="x", temperature=temp)


@pytest.mark.parametrize("temp", [0.0, 0.5, 1.0, 2.0])
def test_llm_profile_temperature_in_range_ok(temp: float) -> None:
    p = LLMProfile(name="X", provider=LLMProvider.OPENAI, model="x", temperature=temp)
    assert p.temperature == temp


# ---------------------------------------------------------------------------
# LLMProfile — max_tokens validator
# ---------------------------------------------------------------------------


def test_llm_profile_max_tokens_zero_raises() -> None:
    with pytest.raises(ValidationError):
        LLMProfile(name="X", provider=LLMProvider.OPENAI, model="x", max_tokens=0)


def test_llm_profile_max_tokens_negative_raises() -> None:
    with pytest.raises(ValidationError):
        LLMProfile(name="X", provider=LLMProvider.OPENAI, model="x", max_tokens=-1)


def test_llm_profile_max_tokens_one_ok() -> None:
    p = LLMProfile(name="X", provider=LLMProvider.OPENAI, model="x", max_tokens=1)
    assert p.max_tokens == 1


# ---------------------------------------------------------------------------
# LLMProfile — profile_type discriminator
# ---------------------------------------------------------------------------


def test_llm_profile_tts_type() -> None:
    p = LLMProfile(name="TTS", provider=LLMProvider.OPENAI, model="tts-1", profile_type="tts")
    assert p.profile_type == "tts"


def test_llm_profile_stt_type() -> None:
    p = LLMProfile(name="STT", provider=LLMProvider.OPENAI, model="whisper-1", profile_type="stt")
    assert p.profile_type == "stt"


def test_llm_profile_invalid_profile_type_raises() -> None:
    with pytest.raises(ValidationError):
        LLMProfile(name="X", provider=LLMProvider.OPENAI, model="x", profile_type="bogus")


# ---------------------------------------------------------------------------
# LLMProfile — A2A protocol fields
# ---------------------------------------------------------------------------


def test_llm_profile_a2a_endpoint() -> None:
    p = LLMProfile(
        name="A2A",
        provider=LLMProvider.OPENAI,
        model="x",
        protocol="a2a",
        a2a_endpoint="http://agent.local",
        a2a_timeout=30,
        fallback_llm_profile_id="llm-fallback",
    )
    assert p.protocol == "a2a"
    assert p.a2a_endpoint == "http://agent.local"
    assert p.a2a_timeout == 30
    assert p.fallback_llm_profile_id == "llm-fallback"


def test_llm_profile_invalid_protocol_raises() -> None:
    with pytest.raises(ValidationError):
        LLMProfile(name="X", provider=LLMProvider.OPENAI, model="x", protocol="soap")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# LLMProfile — service-LLM fields
# ---------------------------------------------------------------------------


def test_llm_profile_service_eligible_default_true() -> None:
    p = LLMProfile(name="X", provider=LLMProvider.OPENAI, model="x")
    assert p.service_eligible is True
    assert p.min_recommended_context == 1024


def test_llm_profile_service_ineligible() -> None:
    p = LLMProfile(name="X", provider=LLMProvider.OPENAI, model="x", service_eligible=False)
    assert p.service_eligible is False


# ---------------------------------------------------------------------------
# LLMProfile — model_dump round-trip
# ---------------------------------------------------------------------------


def test_llm_profile_dump_round_trip() -> None:
    p = LLMProfile(
        id="llm-test01",
        name="X",
        provider=LLMProvider.ANTHROPIC,
        model="claude-3-5-sonnet",
        cost_per_1k_input=0.003,
        cost_per_1k_output=0.015,
    )
    d = p.model_dump()
    p2 = LLMProfile(**d)
    assert p2 == p


# ---------------------------------------------------------------------------
# ActiveConfiguration
# ---------------------------------------------------------------------------


def test_active_configuration_minimal() -> None:
    ac = ActiveConfiguration(
        debate_id="d-1",
        llm_profile_id="llm-x",
        agent_personas={"strategist": "p1"},
        prompt_variant_id="default",
        created_at="2024-01-01T00:00:00",
    )
    assert ac.debate_id == "d-1"
    assert ac.estimated_cost is None
    assert ac.actual_cost is None


def test_active_configuration_with_costs() -> None:
    ac = ActiveConfiguration(
        debate_id="d-1",
        llm_profile_id="llm-x",
        agent_personas={},
        prompt_variant_id="v",
        created_at="2024-01-01T00:00:00",
        estimated_cost=0.5,
        actual_cost=0.42,
    )
    assert ac.estimated_cost == 0.5
    assert ac.actual_cost == 0.42
