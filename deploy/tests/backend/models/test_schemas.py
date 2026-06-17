"""Tests for backend.models.schemas — DebateRequest, DebateResponse, enums."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.schemas import (
    A2AAgentConfig,
    AgentConfig,
    AgentOutput,
    AgentRole,
    CaseInput,
    DebateRequest,
    DebateResponse,
    DebateStatus,
    DebateStatusResponse,
    RoundData,
    SearchMode,
)

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_debate_status_values() -> None:
    assert DebateStatus.PENDING == "pending"
    assert DebateStatus.RUNNING == "running"
    assert DebateStatus.COMPLETED == "completed"
    assert DebateStatus.FAILED == "failed"
    assert DebateStatus.CANCELLED == "cancelled"


def test_agent_role_values() -> None:
    assert AgentRole.STRATEGIST == "strategist"
    assert AgentRole.CRITIC == "critic"
    assert AgentRole.OPTIMIZER == "optimizer"
    assert AgentRole.MODERATOR == "moderator"


def test_search_mode_values() -> None:
    assert SearchMode.OFF == "off"
    assert SearchMode.OPTIONAL == "optional"
    assert SearchMode.REQUIRED == "required"


# ---------------------------------------------------------------------------
# AgentConfig
# ---------------------------------------------------------------------------


def test_agent_config_defaults() -> None:
    a = AgentConfig(role="strategist")
    assert a.llm_profile == "default"
    assert a.temperature == 0.7


def test_agent_config_custom_temperature() -> None:
    a = AgentConfig(role="critic", temperature=1.2)
    assert a.temperature == 1.2


# ---------------------------------------------------------------------------
# CaseInput
# ---------------------------------------------------------------------------


def test_case_input_empty_text_rejected() -> None:
    with pytest.raises(ValidationError):
        CaseInput(text="")


def test_case_input_too_long_rejected() -> None:
    with pytest.raises(ValidationError):
        CaseInput(text="x" * 50_001)


def test_case_input_with_project() -> None:
    c = CaseInput(text="hello", project_id="p1")
    assert c.project_id == "p1"


# ---------------------------------------------------------------------------
# DebateRequest
# ---------------------------------------------------------------------------


def test_debate_request_defaults() -> None:
    r = DebateRequest(case=CaseInput(text="hi"))
    assert r.max_rounds == 3
    assert r.consensus_threshold == 0.8
    assert r.enable_fact_check is False
    assert r.search_mode == SearchMode.OFF
    assert r.llm_profile_id == ""
    assert r.prompt_variant == "default"
    assert r.agent_persona_ids == {}
    assert r.document_ids == []
    assert r.a2a_agents == []


def test_debate_request_default_agent_profile() -> None:
    """By default, agent_profile has 4 roles (legacy 4-role setup)."""
    r = DebateRequest(case=CaseInput(text="hi"))
    roles = {a.role for a in r.agent_profile}
    assert {"strategist", "critic", "optimizer", "moderator"} == roles


def test_debate_request_max_rounds_bounds() -> None:
    with pytest.raises(ValidationError):
        DebateRequest(case=CaseInput(text="hi"), max_rounds=0)
    with pytest.raises(ValidationError):
        DebateRequest(case=CaseInput(text="hi"), max_rounds=21)


def test_debate_request_consensus_bounds() -> None:
    with pytest.raises(ValidationError):
        DebateRequest(case=CaseInput(text="hi"), consensus_threshold=-0.1)
    with pytest.raises(ValidationError):
        DebateRequest(case=CaseInput(text="hi"), consensus_threshold=1.1)


def test_debate_request_with_a2a_agents() -> None:
    a2a = A2AAgentConfig(url="http://x", role="a2a", position="after:critic")
    r = DebateRequest(case=CaseInput(text="hi"), a2a_agents=[a2a])
    assert len(r.a2a_agents) == 1
    assert r.a2a_agents[0].position == "after:critic"


# ---------------------------------------------------------------------------
# A2AAgentConfig
# ---------------------------------------------------------------------------


def test_a2a_agent_config_defaults() -> None:
    a = A2AAgentConfig(url="http://x")
    assert a.role == "a2a_agent"
    assert a.position == "after_all"


# ---------------------------------------------------------------------------
# DebateResponse
# ---------------------------------------------------------------------------


def test_debate_response_defaults() -> None:
    r = DebateResponse(debate_id="d1")
    assert r.status == DebateStatus.PENDING
    assert r.title == ""


# ---------------------------------------------------------------------------
# RoundData + DebateStatusResponse
# ---------------------------------------------------------------------------


def test_round_data() -> None:
    rd = RoundData(round=1, consensus=0.7, agent_outputs=[])
    assert rd.round == 1
    assert rd.consensus == 0.7


def test_debate_status_response_defaults() -> None:
    r = DebateStatusResponse(
        debate_id="d1",
        status=DebateStatus.RUNNING,
        created_at="2024-01-01T00:00:00",
        updated_at="2024-01-01T00:00:00",
    )
    assert r.current_round == 0
    assert r.max_rounds == 3
    assert r.consensus_score is None


# ---------------------------------------------------------------------------
# AgentOutput
# ---------------------------------------------------------------------------


def test_agent_output() -> None:
    o = AgentOutput(role="critic", content="x", tokens_used=100)
    assert o.tokens_used == 100
