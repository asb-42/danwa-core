"""Base projector interface for CQRS read model builders.

Projectors subscribe to the event stream and build materialized views
(Read Models) that the UI and LLMs query. They never read the full event log.

See ADR-001: Thin Events with CQRS Projectors.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from backend.models.debate_event import DebateEvent

logger = logging.getLogger(__name__)


class BaseProjector(ABC):
    """Abstract base class for all projectors.

    Each projector implements ``handle_event`` to process a single event
    and update its read model. The projector framework calls this method
    synchronously after each event is written to the event store.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique projector name for logging and identification."""

    @abstractmethod
    def handle_event(self, event: DebateEvent) -> None:
        """Process a single event and update the read model.

        This method is called synchronously after the event is persisted.
        Implementations should be idempotent — re-processing the same event
        should not corrupt the read model.
        """

    def handles_event_type(self, event_type: str) -> bool:
        """Override to filter which event types this projector handles.

        Default: handle all events. Override for efficiency.
        """
        return True

    def safe_handle(self, event: DebateEvent) -> None:
        """Wrapper that catches exceptions to prevent projector failures
        from blocking the write path.
        """
        try:
            if self.handles_event_type(event.event_type):
                self.handle_event(event)
        except Exception:
            logger.exception("Projector %s failed on event %s", self.name, event.event_id)
