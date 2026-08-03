"""Pydantic schemas for DebateSpace (request / response)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DebateSpaceCreate(BaseModel):
    """Payload to create a new interactive debate space."""

    title: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    case_id: str | None = None
    tenant_id: str | None = None


class DebateSpaceResponse(BaseModel):
    """Serialised debate space returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    space_id: str
    title: str
    description: str | None
    case_id: str | None
    tenant_id: str | None
    created_by: str | None
    status: str
    event_count: int
    fork_count: int
    created_at: datetime
    updated_at: datetime
