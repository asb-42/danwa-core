"""DebateEvent – append-only event log for the interactive debate mode."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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


class DebateEvent(BaseModel):
    """A single immutable fact in the debate tree.

    The tree structure is formed via ``parent_id``:
    - ``parent_id=None``  → root event (space creation)
    - ``parent_id=X``     → direct reply to event X
    - Forking: multiple children share the same ``parent_id``
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
