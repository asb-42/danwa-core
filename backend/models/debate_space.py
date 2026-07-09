"""DebateSpace Aggregate – the root entity for interactive debates."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, Field


class DebateSpace(BaseModel):
    """An interactive debate space that grows via event-sourced forking."""

    space_id: str
    title: str
    description: str | None = None
    project_id: str | None = None
    tenant_id: str | None = None
    created_by: str | None = None
    status: str = "open"  # open | closed | archived
    event_count: int = 0
    fork_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
