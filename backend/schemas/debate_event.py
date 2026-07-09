"""Pydantic schemas for DebateEvent (request / response / SSE).

Thin Event Taxonomy (ADR-001):
    All event types use the same envelope. The ``metadata`` field carries
    the rich payload. Request schemas include convenience constructors
    for common event patterns (AgentActed, UserActed, etc.).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.models.debate_event import (
    ActorType,
    EventType,
    ForkInfo,
    MilestoneInfo,
    StructuredOutput,
)

# Re-export for convenience
__all__ = [
    "ActorType",
    "EventType",
    "DebateEventCreate",
    "DebateEventResponse",
    "EventStreamMessage",
    "SynthesisRequest",
]


# ── Request schemas ────────────────────────────────────────────────────────


class DebateEventCreate(BaseModel):
    """Payload sent by the frontend when a user clicks [+] or an agent speaks.

    For convenience, the frontend can send event_type directly, or use
    the helper classmethods for common patterns.
    """

    space_id: str
    parent_id: str | None = None
    event_type: EventType
    actor_type: ActorType
    actor_id: str
    role: str | None = None
    content: str | dict[str, Any] = ""
    metadata_json: dict[str, Any] = Field(default_factory=dict)

    # ── Convenience constructors ──────────────────────────────────────────

    @classmethod
    def agent_acted(
        cls,
        space_id: str,
        parent_id: str,
        actor_id: str,
        content: str,
        role: str | None = None,
        structured_output: StructuredOutput | None = None,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
    ) -> DebateEventCreate:
        """Create an AgentActed event with structured metadata."""
        metadata: dict[str, Any] = {}
        if structured_output:
            metadata["structured_output"] = structured_output.model_dump()
        if tokens_input is not None:
            metadata["tokens_input"] = tokens_input
        if tokens_output is not None:
            metadata["tokens_output"] = tokens_output
        return cls(
            space_id=space_id,
            parent_id=parent_id,
            event_type="AgentActed",
            actor_type="agent",
            actor_id=actor_id,
            role=role,
            content=content,
            metadata_json=metadata,
        )

    @classmethod
    def user_acted(
        cls,
        space_id: str,
        parent_id: str,
        actor_id: str,
        content: str,
        action_type: str = "instruction",
    ) -> DebateEventCreate:
        """Create a UserActed event (HITL intervention)."""
        return cls(
            space_id=space_id,
            parent_id=parent_id,
            event_type="UserActed",
            actor_type="user",
            actor_id=actor_id,
            content=content,
            metadata_json={"action_type": action_type},
        )

    @classmethod
    def branch_forked(
        cls,
        space_id: str,
        parent_id: str,
        fork_point_event_id: str,
        branch_label: str | None = None,
    ) -> DebateEventCreate:
        """Create a BranchForked event."""
        return cls(
            space_id=space_id,
            parent_id=parent_id,
            event_type="BranchForked",
            actor_type="user",
            actor_id="system",
            content=f"Branch forked from {fork_point_event_id[:8]}",
            metadata_json=ForkInfo(
                fork_point_event_id=fork_point_event_id,
                branch_label=branch_label,
            ).model_dump(),
        )

    @classmethod
    def milestone_reached(
        cls,
        space_id: str,
        parent_id: str,
        milestone_type: Literal["consensus", "report_generated", "task_created", "deadlock"],
        summary: str | None = None,
    ) -> DebateEventCreate:
        """Create a MilestoneReached event."""
        return cls(
            space_id=space_id,
            parent_id=parent_id,
            event_type="MilestoneReached",
            actor_type="system",
            actor_id="system",
            content=summary or f"Milestone: {milestone_type}",
            metadata_json=MilestoneInfo(
                milestone_type=milestone_type,
                summary=summary,
            ).model_dump(),
        )


class SynthesisRequest(BaseModel):
    """Trigger a final deliverable from a debate space."""

    format: Literal["markdown", "latex", "pdf", "json"] = "markdown"
    max_depth: int | None = None  # None = full tree
    include_side_branches: bool = True


# ── Response schemas ──────────────────────────────────────────────────────


class DebateEventResponse(BaseModel):
    """Serialised event pushed via SSE or returned by REST."""

    model_config = ConfigDict(from_attributes=True)

    event_id: str
    space_id: str
    parent_id: str | None
    event_type: EventType
    actor_type: str
    actor_id: str
    role: str | None
    content: str | dict[str, Any]
    metadata_json: dict[str, Any]
    tokens_input: int | None
    tokens_output: int | None
    created_at: datetime


# ── SSE envelope ──────────────────────────────────────────────────────────


class EventStreamMessage(BaseModel):
    """Wrapper for SSE payloads so the frontend can distinguish event types."""

    kind: Literal["event", "heartbeat", "error"]
    payload: DebateEventResponse | None = None
    message: str | None = None
