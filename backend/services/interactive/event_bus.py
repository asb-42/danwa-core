"""EventBus – Redis Streams-based event broadcasting for Interactive Mode.

Uses Redis Streams for durable, replayable event distribution to SSE clients.
Falls back to in-memory when Redis is not configured.

Features:
- Durable event log (Redis Streams XADD)
- Consumer groups for multi-client support
- Replay from specific event ID (XREAD)
- Automatic trimming (MAXLEN)
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)

# Redis Stream settings
DEFAULT_STREAM_MAXLEN = 10000  # Keep last 10k events per space
DEFAULT_BLOCK_MS = 1000  # Block for 1 second waiting for new events


class EventBus:
    """Event broadcasting interface for the interactive debate mode."""

    async def publish(self, stream_name: str, event_data: dict[str, Any]) -> str:
        """Publish an event to a stream. Returns the event ID."""
        ...

    async def subscribe(
        self,
        stream_name: str,
        last_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to a stream, yielding new events."""
        ...

    async def close(self) -> None:
        """Close the event bus."""
        ...


class RedisEventBus(EventBus):
    """Redis Streams-backed event bus."""

    def __init__(
        self,
        redis_url: str,
        maxlen: int = DEFAULT_STREAM_MAXLEN,
        block_ms: int = DEFAULT_BLOCK_MS,
    ):
        import redis.asyncio as aioredis

        self.redis = aioredis.from_url(redis_url, decode_responses=True)
        self.maxlen = maxlen
        self.block_ms = block_ms
        self._consumer_name = f"interactive-{id(self)}"
        logger.info("RedisEventBus connected to %s", redis_url)

    async def publish(self, stream_name: str, event_data: dict[str, Any]) -> str:
        """Publish an event to a Redis Stream."""
        # Convert non-string values to JSON
        serialized = {}
        for k, v in event_data.items():
            if isinstance(v, (dict, list)):
                serialized[k] = json.dumps(v)
            elif v is None:
                serialized[k] = ""
            else:
                serialized[k] = str(v)

        event_id = await self.redis.xadd(
            stream_name,
            serialized,
            maxlen=self.maxlen,
            approximate=True,
        )
        logger.debug("Published to %s: %s", stream_name, event_id)
        return event_id

    async def subscribe(
        self,
        stream_name: str,
        last_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Subscribe to a Redis Stream, yielding new events.

        If last_event_id is provided, replay events after that ID first,
        then stream new events.
        """
        start_id = last_event_id if last_event_id else "0"
        last_seen = start_id

        while True:
            try:
                # Read new events (blocking)
                results = await self.redis.xread(
                    streams={stream_name: last_seen},
                    count=10,
                    block=self.block_ms,
                )

                if results:
                    for _stream_name, messages in results:
                        for msg_id, fields in messages:
                            last_seen = msg_id
                            # Deserialize JSON fields
                            deserialized = {}
                            for k, v in fields.items():
                                try:
                                    deserialized[k] = json.loads(v)
                                except (json.JSONDecodeError, TypeError):
                                    deserialized[k] = v
                            deserialized["_stream_id"] = msg_id
                            yield deserialized

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("RedisEventBus subscribe error: %s", e)
                await asyncio.sleep(1)

    async def get_history(
        self,
        stream_name: str,
        count: int = 100,
    ) -> list[dict[str, Any]]:
        """Get recent events from a stream (for replay)."""
        results = await self.redis.xrevrange(
            stream_name,
            count=count,
        )
        events = []
        for msg_id, fields in reversed(results):  # Reverse for chronological order
            deserialized = {}
            for k, v in fields.items():
                try:
                    deserialized[k] = json.loads(v)
                except (json.JSONDecodeError, TypeError):
                    deserialized[k] = v
            deserialized["_stream_id"] = msg_id
            events.append(deserialized)
        return events

    async def close(self) -> None:
        await self.redis.close()


class InMemoryEventBus(EventBus):
    """In-memory fallback for development/testing."""

    def __init__(self):
        self._streams: dict[str, list[dict[str, Any]]] = {}
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._counter = 0

    async def publish(self, stream_name: str, event_data: dict[str, Any]) -> str:
        self._counter += 1
        event_id = f"mem-{self._counter}"
        event_data["_stream_id"] = event_id

        if stream_name not in self._streams:
            self._streams[stream_name] = []
        self._streams[stream_name].append(event_data)

        # Notify subscribers
        if stream_name in self._subscribers:
            for queue in self._subscribers[stream_name]:
                await queue.put(event_data)

        return event_id

    async def subscribe(
        self,
        stream_name: str,
        last_event_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue()
        if stream_name not in self._subscribers:
            self._subscribers[stream_name] = []
        self._subscribers[stream_name].append(queue)

        # Replay existing events
        if stream_name in self._streams:
            for event in self._streams[stream_name]:
                if last_event_id and event.get("_stream_id") == last_event_id:
                    break
                yield event

        # Stream new events
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=30)
                yield event
        except TimeoutError:
            pass
        finally:
            self._subscribers[stream_name].remove(queue)

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get the global event bus instance."""
    global _event_bus
    if _event_bus is None:
        from backend.api.deps import get_settings

        settings = get_settings()
        if settings.redis_url:
            try:
                _event_bus = RedisEventBus(settings.redis_url)
            except Exception as e:
                logger.warning("Redis unavailable, falling back to in-memory: %s", e)
                _event_bus = InMemoryEventBus()
        else:
            _event_bus = InMemoryEventBus()
    return _event_bus
