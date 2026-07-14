"""EventSyncService – keeps EventStore and EventEmbeddingStore in sync.

When a new event is appended to the EventStore, this service embeds it
in ChromaDB for semantic search. Runs synchronously for now; can be
moved to Redis Streams workers later.
"""

from __future__ import annotations

import logging

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.event_embeddings import EventEmbeddingStore

logger = logging.getLogger(__name__)

# Only embed these event types (others are not useful for semantic search)
_EMBEDDABLE_TYPES = {"UserActed", "AgentActed", "A2AActed", "ContextSynthesized"}


class EventSyncService:
    """Synchronizes events between EventStore and EventEmbeddingStore."""

    def __init__(
        self,
        event_store: EventStore,
        embedding_store: EventEmbeddingStore,
    ):
        self.event_store = event_store
        self.embedding_store = embedding_store

    def sync_event(self, event: DebateEvent) -> None:
        """Embed a single event if it has searchable content."""
        if event.event_type not in _EMBEDDABLE_TYPES:
            return

        content = event.content if isinstance(event.content, str) else str(event.content)
        if not content.strip():
            return

        self.embedding_store.embed_event(
            event_id=event.event_id,
            space_id=event.space_id,
            content=content,
            event_type=event.event_type,
            actor_id=event.actor_id,
            role=event.role,
        )
        logger.debug("Embedded event %s (%s)", event.event_id, event.event_type)

    def sync_space(self, space_id: str, force: bool = False) -> int:
        """Embed all events in a space. Returns count of newly embedded events.

        Args:
            space_id: The debate space to sync.
            force: If True, re-embed all events (e.g., after schema change).
        """
        events = self.event_store.get_full_tree(space_id)
        embedded = 0

        for event in events:
            if event.event_type not in _EMBEDDABLE_TYPES:
                continue

            content = event.content if isinstance(event.content, str) else str(event.content)
            if not content.strip():
                continue

            # Skip if already embedded (unless force)
            if not force:
                existing = self.embedding_store.search_similar(
                    space_id=space_id,
                    query=content[:100],  # Short query to check existence
                    exclude_event_ids=[],
                    n_results=1,
                )
                if any(r["event_id"] == event.event_id for r in existing):
                    continue

            self.embedding_store.embed_event(
                event_id=event.event_id,
                space_id=space_id,
                content=content,
                event_type=event.event_type,
                actor_id=event.actor_id,
                role=event.role,
            )
            embedded += 1

        logger.info("Synced %d events for space %s", embedded, space_id)
        return embedded

    def backfill_all_spaces(self, force: bool = False) -> dict[str, int]:
        """Embed events for all spaces. Used for initial setup or recovery."""
        # Get all unique space_ids
        spaces = self.event_store.list_spaces(limit=10000)
        results: dict[str, int] = {}

        for space in spaces:
            count = self.sync_space(space.space_id, force=force)
            results[space.space_id] = count

        total = sum(results.values())
        logger.info("Backfill complete: %d events embedded across %d spaces", total, len(results))
        return results
