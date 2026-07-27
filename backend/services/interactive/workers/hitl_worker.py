"""HITLWorker – manages human-in-the-loop interactions.

Listens for ``UserActed`` events with ``metadata.action_type == 'hitl_request'``
(thin event taxonomy, ADR-001) and manages the interaction flow:
1. Delivers agent queries to the user via SSE
2. Waits for user responses (responses arrive as separate ``UserActed``
   events with ``metadata.is_response == True``)
3. Appends timeout events when no response arrives

Also handles timeouts and escalation.
"""

from __future__ import annotations

import logging

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.event_bus import EventBus, get_event_bus

logger = logging.getLogger(__name__)

# Default timeout for HITL interactions (5 minutes)
DEFAULT_HITL_TIMEOUT_SECONDS = 300


class HITLWorker:
    """Processes hitl_input events by managing human interaction."""

    def __init__(
        self,
        event_store: EventStore,
        event_bus: EventBus | None = None,
    ):
        self.event_store = event_store
        self.event_bus = event_bus or get_event_bus()

    async def process(self, event: DebateEvent) -> None:
        """Process a HITL request event.

        A HITL request is a ``UserActed`` event with
        ``metadata.action_type == 'hitl_request'``. This worker publishes
        the query to the SSE stream so the frontend can render it and
        prompt the user.

        The user's response arrives later via ``POST /events`` as a
        separate ``UserActed`` event with ``metadata.is_response == True``.
        """
        if event.event_type != "UserActed":
            return
        if event.metadata_json.get("action_type") != "hitl_request":
            return

        meta = event.metadata_json
        timeout = meta.get("timeout_seconds", DEFAULT_HITL_TIMEOUT_SECONDS)

        # Publish the HITL request to the user
        stream_name = f"interactive:space:{event.space_id}"
        await self.event_bus.publish(
            stream_name,
            {
                "event_id": event.event_id,
                "type": "hitl_request",
                "query": event.content,
                "timeout": timeout,
                "actor_id": event.actor_id,
            },
        )

        logger.info(
            "HITLWorker: published HITL request %s (timeout: %ds)",
            event.event_id,
            timeout,
        )

    async def handle_response(
        self,
        space_id: str,
        request_event_id: str,
        response_content: str,
        user_id: str,
    ) -> DebateEvent:
        """Handle a user's response to a HITL request.

        Called when the frontend sends the user's response via POST /events.
        Appends a ``UserActed`` event with ``is_response`` metadata so the
        worker does not re-process it as a new HITL request.
        """
        return self.event_store.append_event(
            space_id=space_id,
            event_type="UserActed",
            actor_type="user",
            actor_id=user_id,
            content=response_content,
            parent_id=request_event_id,
            metadata_json={
                "response_to": request_event_id,
                "is_response": True,
                "action_type": "hitl_response",
            },
        )

    async def handle_timeout(
        self,
        space_id: str,
        request_event_id: str,
    ) -> DebateEvent:
        """Handle a HITL timeout by appending a system event."""
        return self.event_store.append_event(
            space_id=space_id,
            event_type="UserActed",
            actor_type="system",
            actor_id="hitl-timeout",
            content="[HITL Timeout] No response received within the time limit.",
            parent_id=request_event_id,
            metadata_json={
                "response_to": request_event_id,
                "is_timeout": True,
                "action_type": "hitl_timeout",
            },
        )
