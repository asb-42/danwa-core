"""Tests for backend.models.transactional — Transactional Drafting models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models.transactional import (
    AngelsAdvocateOutput,
    BuilderOutput,
    BuildResponse,
    CriticItem,
    PragmatistEvaluation,
    PragmatistOutput,
    PreservedElement,
    Provenance,
)

# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


def test_provenance_minimal() -> None:
    p = Provenance(draft_version=1, critic_item_id="c-1", revision_type="conservative")
    assert p.pragmatist_verdict is None
    assert p.pragmatist_score is None


def test_provenance_invalid_revision_type_rejected() -> None:
    with pytest.raises(ValidationError):
        Provenance(draft_version=1, critic_item_id="c-1", revision_type="d")  # type: ignore[arg-type]


def test_provenance_score_range() -> None:
    with pytest.raises(ValidationError):
        Provenance(
            draft_version=1,
            critic_item_id="c-1",
            revision_type="conservative",
            pragmatist_score=1.5,
        )


# ---------------------------------------------------------------------------
# CriticItem
# ---------------------------------------------------------------------------


def test_critic_item_valid_id_pattern() -> None:
    c = CriticItem(
        critic_id="c-critic_1-003",
        severity="blocking",
        target="§3.2",
        flaw="Lückenhaft",
        principle="Vertragsrecht",
    )
    assert c.critic_id == "c-critic_1-003"


def test_critic_item_invalid_id_pattern_rejected() -> None:
    with pytest.raises(ValidationError):
        CriticItem(
            critic_id="invalid-id",
            severity="blocking",
            target="x",
            flaw="x",
            principle="x",
        )


def test_critic_item_invalid_severity_rejected() -> None:
    with pytest.raises(ValidationError):
        CriticItem(
            critic_id="c-critic_1-003",
            severity="catastrophic",  # type: ignore[arg-type]
            target="x",
            flaw="x",
            principle="x",
        )


# ---------------------------------------------------------------------------
# BuildResponse
# ---------------------------------------------------------------------------


def test_build_response_minimal() -> None:
    b = BuildResponse(
        response_to="c-1",
        option_a="a",
        option_b="b",
        recommendation="option_a",
        rationale="r",
        risk_assessment="low",
        implementable=True,
    )
    assert b.option_c is None
    assert b.provenance is None


def test_build_response_invalid_recommendation_rejected() -> None:
    with pytest.raises(ValidationError):
        BuildResponse(
            response_to="c-1",
            option_a="a",
            option_b="b",
            recommendation="option_z",  # type: ignore[arg-type]
            rationale="r",
            risk_assessment="low",
            implementable=True,
        )


def test_build_response_invalid_risk_rejected() -> None:
    with pytest.raises(ValidationError):
        BuildResponse(
            response_to="c-1",
            option_a="a",
            option_b="b",
            recommendation="option_a",
            rationale="r",
            risk_assessment="extreme",  # type: ignore[arg-type]
            implementable=True,
        )


# ---------------------------------------------------------------------------
# BuilderOutput
# ---------------------------------------------------------------------------


def test_builder_output_constructivity_score_range() -> None:
    with pytest.raises(ValidationError):
        BuilderOutput(build_responses=[], constructivity_score=1.5)


def test_builder_output_default_constructivity() -> None:
    b = BuilderOutput(build_responses=[])
    assert b.constructivity_score == 0.0
    assert b.global_revision is None


# ---------------------------------------------------------------------------
# PragmatistEvaluation + PragmatistOutput
# ---------------------------------------------------------------------------


def test_pragmatist_evaluation_minimal() -> None:
    p = PragmatistEvaluation(
        response_to="r1",
        feasibility=0.5,
        process_risk="medium",
        cost_time_estimate="2 Wochen",
        verdict="accept",
    )
    assert p.revision_note is None


def test_pragmatist_evaluation_feasibility_range() -> None:
    with pytest.raises(ValidationError):
        PragmatistEvaluation(
            response_to="r1",
            feasibility=1.5,
            process_risk="low",
            cost_time_estimate="x",
            verdict="accept",
        )


def test_pragmatist_output_minimal() -> None:
    p = PragmatistOutput(evaluations=[], reality_score=0.0)
    assert p.blocking_concerns == []


# ---------------------------------------------------------------------------
# PreservedElement + AngelsAdvocateOutput
# ---------------------------------------------------------------------------


def test_preserved_element_minimal() -> None:
    pe = PreservedElement(
        element_id="aa-001",
        source_location="§3.2",
        preserved_text="x",
        rationale="y",
        priority="essential",
    )
    assert pe.priority == "essential"


def test_preserved_element_invalid_priority_rejected() -> None:
    with pytest.raises(ValidationError):
        PreservedElement(
            element_id="x",
            source_location="y",
            preserved_text="z",
            rationale="w",
            priority="critical",  # type: ignore[arg-type]
        )


def test_angels_advocate_min_elements() -> None:
    with pytest.raises(ValidationError):
        AngelsAdvocateOutput(preserved_elements=[], overall_stability_score=0.5)


def test_angels_advocate_valid() -> None:
    a = AngelsAdvocateOutput(
        preserved_elements=[
            PreservedElement(
                element_id="aa-001",
                source_location="§1",
                preserved_text="x",
                rationale="y",
                priority="essential",
            )
        ],
        overall_stability_score=0.8,
    )
    assert a.overall_stability_score == 0.8
