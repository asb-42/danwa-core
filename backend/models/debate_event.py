"""DebateEvent – append-only event log for the interactive debate mode.

Thin Event Taxonomy (ADR-001):
    Events are categorized into 4 domains. All events write to the same
    debate_events table. The ``metadata`` field carries the rich, structured
    payload — not separate event types.

    A. Space Lifecycle:     SpaceCreated, SpaceArchived
    B. Actor Interactions:  UserActed, AgentActed, A2AActed
    C. System/Infra:        ToolRequested, ToolExecuted, ContextSynthesized, BranchForked
    D. Milestones:          MilestoneReached
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── Thin Event Types (ADR-001) ────────────────────────────────────────────

EventType = Literal[
    # A. Space Lifecycle
    "SpaceCreated",
    "SpaceArchived",
    # B. Actor Interactions (core debate — ~90% of events)
    "UserActed",
    "AgentActed",
    "A2AActed",
    # C. System & Infrastructure
    "ToolRequested",
    "ToolExecuted",
    "ContextSynthesized",
    "BranchForked",
    # D. Milestones
    "MilestoneReached",
]

ActorType = Literal["user", "agent", "system", "a2a"]

# Legacy mapping for backward compatibility during migration
_LEGACY_EVENT_TYPE_MAP: dict[str, EventType] = {
    "user_message": "UserActed",
    "agent_speech": "AgentActed",
    "a2a_request": "A2AActed",
    "a2a_response": "A2AActed",
    "hitl_input": "UserActed",
    "tool_call_requested": "ToolRequested",
    "tool_result": "ToolExecuted",
    "synthesis": "ContextSynthesized",
}


def normalize_event_type(event_type: str) -> EventType:
    """Map legacy event types to the thin taxonomy. Pass through if already valid."""
    return _LEGACY_EVENT_TYPE_MAP.get(event_type, event_type)  # type: ignore[return-value]


# ── Metadata Sub-Structures ───────────────────────────────────────────────


class StructuredOutput(BaseModel):
    """Structured extraction from an agent's response (optional, in metadata)."""

    claims: list[str] = Field(default_factory=list)
    critiques: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)
    sentiment: str | None = None
    argumentation_pattern: str | None = None


class ToolCallInfo(BaseModel):
    """Metadata for ToolRequested / ToolExecuted events."""

    tool_name: str
    tool_params: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    duration_ms: int | None = None
    success: bool = True


class ContextSynthInfo(BaseModel):
    """Metadata for ContextSynthesized events."""

    prompt: str
    token_costs: dict[str, int] = Field(default_factory=dict)
    source_event_ids: list[str] = Field(default_factory=list)


class ForkInfo(BaseModel):
    """Metadata for BranchForked events."""

    fork_point_event_id: str
    branch_label: str | None = None


class MilestoneInfo(BaseModel):
    """Metadata for MilestoneReached events."""

    milestone_type: Literal["consensus", "report_generated", "task_created", "deadlock"]
    summary: str | None = None


# ── Main Event Model ──────────────────────────────────────────────────────


class DebateEvent(BaseModel):
    """A single immutable fact in the debate tree.

    The tree structure is formed via ``parent_id``:
    - ``parent_id=None``  → root event (space creation)
    - ``parent_id=X``     → direct reply to event X
    - Forking: multiple children share the same ``parent_id``

    Thin Event Design (ADR-001):
        All event types use the same envelope. The ``metadata`` field carries
        the rich payload (structured_output, tool_calls, etc.). This avoids
        event type explosion when adding new agent roles.
    """

    event_id: str
    space_id: str
    parent_id: str | None = None
    event_type: EventType
    actor_type: ActorType
    actor_id: str
    role: str | None = None
    content: str | dict[str, Any] = ""
    metadata_json: dict[str, Any] = Field(default_factory=dict)
    tokens_input: int | None = None
    tokens_output: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
