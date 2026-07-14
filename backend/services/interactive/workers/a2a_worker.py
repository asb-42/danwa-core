"""A2AWorker – calls external A2A agents when A2AActed events arrive.

Listens for events of type A2AActed, sends the message to the
configured external agent, and appends the response as an A2AResponse event.
"""

from __future__ import annotations

import logging

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.context_synthesizer import ContextSynthesizer
from backend.services.interactive.event_embeddings import EventEmbeddingStore

logger = logging.getLogger(__name__)


class A2AWorker:
    """Processes A2AActed events by calling external A2A agents."""

    def __init__(
        self,
        event_store: EventStore,
        embedding_store: EventEmbeddingStore | None = None,
    ):
        self.event_store = event_store
        self.embedding_store = embedding_store
        self.synthesizer = ContextSynthesizer(event_store, embedding_store)

    async def process(self, event: DebateEvent) -> DebateEvent | None:
        """Process an A2AActed event.

        This worker:
        1. Extracts the target agent URL from metadata
        2. Synthesizes context from the parent thread
        3. Calls the external A2A agent
        4. Appends the response as an A2AResponse event
        """
        if event.event_type != "A2AActed":
            return None

        meta = event.metadata_json
        agent_url = meta.get("agent_url")
        if not agent_url:
            logger.error("A2AWorker: no agent_url in event %s", event.event_id)
            return None

        # Synthesize context
        try:
            window = self.synthesizer.synthesize(
                space_id=event.space_id,
                target_event_id=event.parent_id or event.event_id,
            )
            prompt_context = window.to_prompt_context()
        except Exception as e:
            logger.warning("Context synthesis failed: %s", e)
            prompt_context = ""

        # Build message for the external agent
        message = f"{prompt_context}\n\n{event.content}" if prompt_context else str(event.content)

        # Call the external A2A agent
        try:
            from backend.a2a.client import A2AClient

            client = A2AClient(agent_url, timeout=meta.get("timeout", 120.0))

            # Discover capabilities
            await client.discover()

            # Send task
            result = await client.send_task(
                message=message,
                task_id=meta.get("task_id"),
                metadata={
                    "space_id": event.space_id,
                    "event_id": event.event_id,
                    "actor_id": event.actor_id,
                },
            )

            # Extract response content
            response_content = result.get("message", {}).get("parts", [{}])[0].get("text", "")

            if not response_content:
                logger.warning("A2AWorker: empty response from %s", agent_url)
                return None

        except Exception as e:
            logger.error("A2AWorker: failed to call %s: %s", agent_url, e)
            response_content = f"[A2A Error] {e}"

        # Append the response event
        response_event = self.event_store.append_event(
            space_id=event.space_id,
            event_type="A2AResponse",
            actor_type="a2a",
            actor_id=meta.get("agent_id", agent_url),
            content=response_content,
            parent_id=event.event_id,
            role=meta.get("role"),
            metadata_json={
                "agent_url": agent_url,
                "request_event_id": event.event_id,
                "status": result.get("status", "unknown") if result else "error",
            },
        )

        logger.info(
            "A2AWorker: response from %s → event %s",
            agent_url,
            response_event.event_id,
        )
        return response_event
