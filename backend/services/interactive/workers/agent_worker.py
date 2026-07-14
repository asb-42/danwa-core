"""AgentWorker – calls LLM agents when AgentActed events are requested.

Listens for events of type AgentActed with metadata containing
the agent configuration, calls the LLM, and appends the result as
an AgentActed event.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.context_synthesizer import ContextSynthesizer
from backend.services.interactive.event_embeddings import EventEmbeddingStore
from backend.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class AgentWorker:
    """Processes AgentActed events by calling LLMs."""

    def __init__(
        self,
        event_store: EventStore,
        embedding_store: EventEmbeddingStore | None = None,
    ):
        self.event_store = event_store
        self.embedding_store = embedding_store
        self.synthesizer = ContextSynthesizer(event_store, embedding_store)

    async def process(self, event: DebateEvent) -> DebateEvent | None:
        """Process an AgentActed event.

        This worker:
        1. Synthesizes context from the parent thread
        2. Calls the configured LLM
        3. Appends the response as a new event
        """
        if event.event_type != "AgentActed":
            return None

        # Extract agent config from metadata
        meta = event.metadata_json
        llm_profile_id = meta.get("llm_profile_id")
        role = event.role or meta.get("role", "assistant")

        # Synthesize context for the LLM
        try:
            window = self.synthesizer.synthesize(
                space_id=event.space_id,
                target_event_id=event.parent_id or event.event_id,
                agent_bundle={"role": role, "llm_profile_id": llm_profile_id},
            )
            prompt_context = window.to_prompt_context()
        except Exception as e:
            logger.warning("Context synthesis failed: %s", e)
            prompt_context = ""

        # Build the prompt
        system_prompt = self._build_system_prompt(role, meta)
        user_prompt = f"{prompt_context}\n\n{event.content}" if prompt_context else str(event.content)

        # Call LLM
        try:
            llm = LLMService(profile_id=llm_profile_id)
            result = await llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            result = None

        if not result or not result.content:
            return None

        # Append the response event
        response_event = self.event_store.append_event(
            space_id=event.space_id,
            event_type="AgentActed",
            actor_type="agent",
            actor_id=meta.get("actor_id", f"llm-{llm_profile_id or 'default'}"),
            content=result.content,
            parent_id=event.event_id,
            role=role,
            metadata_json={
                "llm_profile_id": llm_profile_id,
                "tokens_input": result.tokens_in,
                "tokens_output": result.tokens_out,
                "model": result.model,
                "triggered_by": event.event_id,
            },
            tokens_input=result.tokens_in,
            tokens_output=result.tokens_out,
        )

        logger.info(
            "AgentWorker: generated %d tokens for event %s",
            result.tokens_out,
            response_event.event_id,
        )
        return response_event

    def _build_system_prompt(self, role: str, meta: dict[str, Any]) -> str:
        """Build the system prompt for the agent."""
        base = f"Du bist ein Debatten-Agent mit der Rolle '{role}'. "
        base += "Antworte präzise und argumentativ. "
        if meta.get("system_prompt"):
            base += meta["system_prompt"]
        return base
