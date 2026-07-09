"""Pydantic schemas for DebateEvent (request / response / SSE)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

EventType = Literal[
    "user_message",
    "agent_speech",
    "tool_call_requested",
    "tool_result",
    "a2a_request",
    "a2a_response",
    "hitl_input",
    "synthesis",
]

ActorType = Literal["user", "agent", "system", "a2a"]


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class DebateEventCreate(BaseModel):
    """Payload sent by the frontend when a user clicks [+] or an agent speaks."""

    space_id: str
    parent_id: str | None = None
    event_type: EventType
    actor_type: ActorType
    actor_id: str
    role: str | None = None
    content: str | dict[str, Any] = ""
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class SynthesisRequest(BaseModel):
    """Trigger a final deliverable from a debate space."""

    format: Literal["markdown", "latex", "pdf", "json"] = "markdown"
    max_depth: int | None = None  # None = full tree
    include_side_branches: bool = True


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# SSE envelope
# ---------------------------------------------------------------------------


class EventStreamMessage(BaseModel):
    """Wrapper for SSE payloads so the frontend can distinguish event types."""

    kind: Literal["event", "heartbeat", "error"]
    payload: DebateEventResponse | None = None
    message: str | None = None
