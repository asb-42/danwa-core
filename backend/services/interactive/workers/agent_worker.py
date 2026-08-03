"""AgentWorker – calls LLM agents when AgentActed events are requested.

Listens for events of type ``AgentActed`` (thin event taxonomy, ADR-001)
with agent configuration in ``metadata``, calls the LLM, and appends the
result as a new ``AgentActed`` event that is a child of the trigger event.

The trigger event's ``content`` carries the user's message / instruction;
the response event's ``content`` carries the LLM output.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.context_synthesizer import ContextSynthesizer
from backend.services.interactive.event_embeddings import EventEmbeddingStore
from backend.services.interactive.event_bus import EventBus, get_event_bus
from backend.services.llm_service import LLMService

logger = logging.getLogger(__name__)


class AgentWorker:
    """Processes agent_speech events by calling LLMs."""

    def __init__(
        self,
        event_store: EventStore,
        embedding_store: EventEmbeddingStore | None = None,
        event_bus: EventBus | None = None,
    ):
        self.event_store = event_store
        self.embedding_store = embedding_store
        self.event_bus = event_bus
        self.synthesizer = ContextSynthesizer(event_store, embedding_store)

    async def process(self, event: DebateEvent) -> DebateEvent | None:
        """Process an ``AgentActed`` trigger event.

        This worker:
        1. Synthesizes context from the parent thread
        2. Retrieves relevant document chunks from DMS (if space has a case)
        3. Calls the configured LLM
        4. Appends the response as a new ``AgentActed`` event

        Events with ``metadata.is_response == True`` are LLM outputs already
        produced by a previous run and are skipped to avoid infinite loops.
        """
        if event.event_type != "AgentActed":
            return None
        # Skip events that are already LLM responses (avoid re-processing)
        if event.metadata_json.get("is_response"):
            return None

        # Extract agent config from metadata
        meta = event.metadata_json
        llm_profile_id = meta.get("llm_profile_id")
        role = event.role or meta.get("role", "assistant")

        # Retrieve document chunks from DMS if space is linked to a case
        document_chunks = self._get_document_chunks(event)

        # Synthesize context for the LLM
        try:
            window = self.synthesizer.synthesize(
                space_id=event.space_id,
                target_event_id=event.parent_id or event.event_id,
                agent_bundle={"role": role, "llm_profile_id": llm_profile_id},
                document_chunks=document_chunks,
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
                prompt=user_prompt,
                system_prompt=system_prompt,
            )
        except Exception as e:
            logger.error("LLM call failed: %s", e)
            result = None

        if not result or not result.content:
            return None

        # Append the response event (thin taxonomy: AgentActed)
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
                "is_response": True,
                "document_chunks_used": len(document_chunks),
            },
            tokens_input=result.tokens_in,
            tokens_output=result.tokens_out,
        )

        logger.info(
            "AgentWorker: generated %d tokens for event %s (docs: %d)",
            result.tokens_out,
            response_event.event_id,
            len(document_chunks),
        )
        return response_event

    def _get_document_chunks(self, event: DebateEvent) -> list[dict]:
        """Retrieve relevant document chunks from DMS if space has a case.

        Uses the event content as a query to find relevant chunks via
        hybrid retrieval (BM25 + vector search).
        """
        # Look up the space to get case_id and tenant_id
        space = self.event_store.get_space(event.space_id)
        if not space or not space.case_id:
            return []

        try:
            from backend.api.deps import get_case_dir
            from backend.services.dms.config import load_dms_config
            from backend.services.dms.service import DMS, _dms_cache, _dms_cache_lock

            # Get case directory
            case_dir = get_case_dir(space.case_id)
            dms_dir = case_dir / "dms"

            # Check if DMS directory exists
            if not dms_dir.exists():
                return []

            # Get or create DMS instance (use case_id as scope_id, matching _get_dms_for_case pattern)
            cache_key = ("case", space.tenant_id, space.case_id)
            with _dms_cache_lock:
                if cache_key in _dms_cache:
                    dms = _dms_cache[cache_key]
                else:
                    try:
                        dms_config = load_dms_config()
                    except Exception:
                        dms_config = {}

                    scope_id = f"case:{space.tenant_id}:{space.case_id}"
                    dms = DMS(
                        db_path=str(dms_dir / "dms.db"),
                        chroma_path=str(dms_dir / "chroma_db"),
                        config=dms_config,
                        project_id=scope_id,
                    )
                    _dms_cache[cache_key] = dms

            # Use the event content as a query for hybrid retrieval
            query = event.content if isinstance(event.content, str) else str(event.content)
            if not query.strip():
                return []

            # Retrieve relevant chunks
            chunks = dms.auto_retrieve_for_topic(query, project_id=dms._project_id, k=8)
            logger.info(
                "AgentWorker: retrieved %d document chunks for space %s (case: %s)",
                len(chunks),
                event.space_id,
                space.case_id,
            )
            return chunks

        except Exception as e:
            logger.debug("Could not retrieve document chunks for space %s: %s", event.space_id, e)
            return []

    def _build_system_prompt(self, role: str, meta: dict[str, Any]) -> str:
        """Build the system prompt for the agent.

        Prefers an explicit ``system_prompt`` / ``system_prompt_addon`` from
        the event metadata (set by action templates). Falls back to a neutral,
        language-agnostic default so we do not hardcode German.
        """
        addon = meta.get("system_prompt_addon") or meta.get("system_prompt")
        if addon:
            return addon
        # Neutral default (no hardcoded language)
        return (
            f"You are a debate agent acting in the role '{role}'. "
            "Respond precisely and argumentatively, addressing the discussion context."
        )
