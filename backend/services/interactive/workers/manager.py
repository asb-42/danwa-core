"""WorkerManager – orchestrates event-driven workers for Interactive Mode.

Listens to Redis Streams and dispatches events to the appropriate workers:
- AgentActed → AgentWorker (LLM calls)
- A2AActed → A2AWorker (external agents)
- UserActed → HITLWorker (human interaction)
- ToolRequested → ToolWorker (future)

Runs as a background task in the FastAPI app lifecycle.

Thin Event Taxonomy (ADR-001):
    All event types use the same envelope. Workers dispatch based on
    event_type and role from metadata, not separate event types.
"""

from __future__ import annotations

import asyncio
import logging

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.event_bus import EventBus, get_event_bus
from backend.services.interactive.event_embeddings import EventEmbeddingStore
from backend.services.interactive.event_sync import EventSyncService
from backend.services.interactive.workers.a2a_worker import A2AWorker
from backend.services.interactive.workers.agent_worker import AgentWorker
from backend.services.interactive.workers.hitl_worker import HITLWorker

logger = logging.getLogger(__name__)

# Event types that workers can process (Thin Event Taxonomy)
_WORKER_EVENT_TYPES = {"AgentActed", "A2AActed", "UserActed", "ToolRequested"}


class WorkerManager:
    """Manages and dispatches events to workers."""

    def __init__(
        self,
        event_store: EventStore,
        event_bus: EventBus | None = None,
        embedding_store: EventEmbeddingStore | None = None,
    ):
        self.event_store = event_store
        self.event_bus = event_bus or get_event_bus()
        self.embedding_store = embedding_store or EventEmbeddingStore()
        self.sync_service = EventSyncService(event_store, self.embedding_store)

        # Initialize workers
        self.agent_worker = AgentWorker(event_store, self.embedding_store)
        self.a2a_worker = A2AWorker(event_store, self.embedding_store)
        self.hitl_worker = HITLWorker(event_store, self.event_bus)

        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self, space_ids: list[str] | None = None) -> None:
        """Start listening to events for the given spaces.

        Args:
            space_ids: List of space IDs to listen to.
                       If None, listens to all spaces.
        """
        if self._running:
            logger.warning("WorkerManager already running")
            return

        self._running = True
        logger.info("WorkerManager started for spaces: %s", space_ids or "all")

        # Start worker tasks for each space
        if space_ids:
            for space_id in space_ids:
                task = asyncio.create_task(self._listen_space(space_id))
                self._tasks.append(task)
        else:
            # Listen to a wildcard stream (for discovery)
            task = asyncio.create_task(self._listen_all())
            self._tasks.append(task)

    async def stop(self) -> None:
        """Stop all worker tasks."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("WorkerManager stopped")

    async def _listen_space(self, space_id: str) -> None:
        """Listen to events for a specific space and dispatch to workers."""
        stream_name = f"interactive:space:{space_id}"

        logger.info("WorkerManager: listening to %s", stream_name)

        async for event_data in self.event_bus.subscribe(stream_name):
            if not self._running:
                break

            event_id = event_data.get("event_id")
            if not event_id:
                continue

            event = self.event_store.get_event(event_id)
            if not event:
                continue

            await self._dispatch(event)

    async def _listen_all(self) -> None:
        """Listen to all spaces (for dynamic space discovery)."""
        # This is a fallback - in production, spaces are registered explicitly
        while self._running:
            await asyncio.sleep(10)
            # Periodically check for new spaces
            spaces = self.event_store.list_spaces(limit=100)
            for space in spaces:
                stream_name = f"interactive:space:{space.space_id}"
                # Check if stream exists
                try:
                    info = await self.event_bus.redis.xinfo_stream(stream_name)
                    if info and space.space_id not in [
                        t.get_name() for t in self._tasks
                    ]:
                        task = asyncio.create_task(self._listen_space(space.space_id))
                        self._tasks.append(task)
                except Exception:
                    pass

    async def _dispatch(self, event: DebateEvent) -> None:
        """Dispatch an event to the appropriate worker."""
        try:
            if event.event_type == "AgentActed":
                await self.agent_worker.process(event)
            elif event.event_type == "A2AActed":
                await self.a2a_worker.process(event)
            elif event.event_type == "UserActed":
                # UserActed can be HITL response or direct user input
                action_type = event.metadata_json.get("action_type", "")
                if action_type == "hitl_response":
                    await self.hitl_worker.process(event)
                else:
                    logger.debug("UserActed with action_type=%s, no worker needed", action_type)
            elif event.event_type == "ToolRequested":
                # TODO: Implement ToolWorker
                logger.debug("Tool worker not yet implemented")
            else:
                logger.debug("No worker for event type: %s", event.event_type)

            # Sync to embeddings (for semantic search)
            self.sync_service.sync_event(event)

        except Exception as e:
            logger.error(
                "WorkerManager: error processing event %s: %s",
                event.event_id,
                e,
                exc_info=True,
            )

    async def trigger_agent(
        self,
        space_id: str,
        parent_event_id: str,
        agent_config: dict,
        user_message: str,
    ) -> DebateEvent:
        """Manually trigger an agent response (for [+] button).

        This creates an AgentActed event that will be processed by the AgentWorker.
        """
        return self.event_store.append_event(
            space_id=space_id,
            event_type="AgentActed",
            actor_type="agent",
            actor_id=agent_config.get("actor_id", "agent"),
            content=user_message,
            parent_id=parent_event_id,
            role=agent_config.get("role"),
            metadata_json=agent_config,
        )

    async def trigger_a2a(
        self,
        space_id: str,
        parent_event_id: str,
        agent_url: str,
        message: str,
        agent_config: dict | None = None,
    ) -> DebateEvent:
        """Manually trigger an A2A request."""
        return self.event_store.append_event(
            space_id=space_id,
            event_type="A2AActed",
            actor_type="a2a",
            actor_id=agent_url,
            content=message,
            parent_id=parent_event_id,
            metadata_json={
                "agent_url": agent_url,
                **(agent_config or {}),
            },
        )

    async def trigger_hitl(
        self,
        space_id: str,
        parent_event_id: str,
        query: str,
        actor_id: str = "system",
    ) -> DebateEvent:
        """Manually trigger a HITL request."""
        return self.event_store.append_event(
            space_id=space_id,
            event_type="UserActed",
            actor_type="user",
            actor_id=actor_id,
            content=query,
            parent_id=parent_event_id,
            metadata_json={"action_type": "hitl_request", "requires_response": True},
        )
