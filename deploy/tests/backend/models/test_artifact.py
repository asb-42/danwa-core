"""Tests for backend.models.artifact — DebateArtifact + inner transcript."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.artifact import (
    DebateArtifact,
    Injection,
    MinorityVote,
    Turn,
    UserQuery,
)


def test_turn_defaults() -> None:
    t = Turn(round=1, node_id="n1", agent_name="A", role_type="strategist", content="x")
    assert t.role_definition_id == ""
    assert t.llm_profile_id == ""
    assert t.latency_ms == 0
    assert t.token_usage == {}


def test_turn_id_auto_generated() -> None:
    t = Turn(round=1, node_id="n", agent_name="a", role_type="r", content="c")
    assert t.id


def test_injection_source_must_be_user_or_system() -> None:
    with pytest.raises(ValidationError):
        Injection(source="other", target_node_id="n", content="c")  # type: ignore[arg-type]


def test_injection_defaults() -> None:
    inj = Injection(target_node_id="n", content="c")
    assert inj.source == "user"
    assert inj.injected_at_round == 0


def test_user_query_defaults() -> None:
    q = UserQuery(content="?")
    assert q.response_turn_id is None


def test_minority_vote_requires_target_turn() -> None:
    m = MinorityVote(agent_name="A", dissent_content="d", target_turn_id="t1")
    assert m.target_turn_id == "t1"


def test_artifact_minimal() -> None:
    a = DebateArtifact(session_id="s1", workflow_id="w1")
    assert a.title == ""
    assert a.transcript == []
    assert a.usability_score is None


def test_artifact_artifact_hash() -> None:
    a = DebateArtifact(session_id="s1", workflow_id="w1", title="T")
    h = a.artifact_hash()
    assert len(h) == 64  # SHA-256


def test_artifact_artifact_hash_deterministic() -> None:
    a = DebateArtifact(session_id="s1", workflow_id="w1", title="T")
    assert a.artifact_hash() == a.artifact_hash()


def test_artifact_artifact_hash_differs_for_different_content() -> None:
    a = DebateArtifact(session_id="s1", workflow_id="w1", title="A")
    b = DebateArtifact(session_id="s1", workflow_id="w1", title="B")
    assert a.artifact_hash() != b.artifact_hash()


def test_artifact_usability_score_range() -> None:
    with pytest.raises(ValidationError):
        DebateArtifact(session_id="s", workflow_id="w", usability_score=1.5)
    with pytest.raises(ValidationError):
        DebateArtifact(session_id="s", workflow_id="w", usability_score=-0.1)


@pytest.mark.parametrize("score", [0.0, 0.5, 1.0])
def test_artifact_usability_score_valid(score: float) -> None:
    a = DebateArtifact(session_id="s", workflow_id="w", usability_score=score)
    assert a.usability_score == score


def test_artifact_transactional_scores() -> None:
    a = DebateArtifact(
        session_id="s",
        workflow_id="w",
        constructivity_score=0.75,
        draft_versions=2,
        critic_item_count=4,
        build_response_count=3,
        pragmatist_reality_score=0.6,
    )
    assert a.constructivity_score == 0.75
    assert a.draft_versions == 2
    assert a.critic_item_count == 4


def test_artifact_consensus_result_dict() -> None:
    a = DebateArtifact(session_id="s", workflow_id="w", consensus_result={"score": 0.9, "reached": True})
    assert a.consensus_result["reached"] is True
