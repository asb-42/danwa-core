"""Shared Pydantic models used across the interactive mode schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TimestampMixin(BaseModel):
    """Mixin that serialises datetime fields consistently."""

    created_at: datetime
    model_config = ConfigDict(from_attributes=True)
