"""AgentWorker – calls LLM agents when AgentActed events are requested.

Listens for events of type ``AgentActed`` (thin event taxonomy, ADR-001)
with agent configuration in ``metadata``, calls the LLM, and appends the
result as a new ``AgentActed`` event that is a child of the trigger event.

The trigger event's ``content`` carries the user's message / instruction;
the response event's ``content`` carries the LLM output.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.composer_service import Composition, ComposerService
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
        3. Optionally performs web search
        4. Calls the configured LLM
        5. Appends the response as a new ``AgentActed`` event

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
        search_mode = meta.get("search_mode", "off")

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
        system_prompt = self._build_system_prompt(role, meta, search_mode)
        user_prompt = f"{prompt_context}\n\n{event.content}" if prompt_context else str(event.content)

        # Perform web search if required mode
        web_results = []
        if search_mode == "required":
            web_results = await self._perform_search(user_prompt)
            if web_results:
                user_prompt = self._append_search_results(user_prompt, web_results)

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

        # Handle optional search mode: check for [SEARCH: query] markers
        final_content = result.content
        if search_mode == "optional":
            search_results = await self._handle_optional_search(result.content)
            if search_results:
                final_content = f"{result.content}\n\n---\n\n## Web Research\n\n{search_results}"

        # Append the response event (thin taxonomy: AgentActed)
        response_event = self.event_store.append_event(
            space_id=event.space_id,
            event_type="AgentActed",
            actor_type="agent",
            actor_id=meta.get("actor_id", f"llm-{llm_profile_id or 'default'}"),
            content=final_content,
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
                "web_search_used": len(web_results) > 0,
                "search_mode": search_mode,
            },
            tokens_input=result.tokens_in,
            tokens_output=result.tokens_out,
        )

        logger.info(
            "AgentWorker: generated %d tokens for event %s (docs: %d, search: %d)",
            result.tokens_out,
            response_event.event_id,
            len(document_chunks),
            len(web_results),
        )
        return response_event

    async def _perform_search(self, query: str) -> list[dict]:
        """Perform web search and return formatted results."""
        try:
            from backend.services.web_search import WebSearchTool
            from backend.core.config import get_settings

            settings = get_settings()
            search_tool = WebSearchTool(
                url=getattr(settings, "searxng_url", "http://localhost:8080"),
                max_results=getattr(settings, "searxng_max_results", 5),
                region=getattr(settings, "searxng_region", "de-de"),
            )

            # Extract search queries from the text
            queries = self._extract_search_queries(query)
            if not queries:
                return []

            all_results = []
            for q in queries[:3]:  # Max 3 queries
                results = await search_tool.search(q)
                all_results.extend(results)

            logger.info("Web search: %d results for %d queries", len(all_results), len(queries))
            return all_results

        except Exception as e:
            logger.warning("Web search failed: %s", e)
            return []

    def _extract_search_queries(self, text: str) -> list[str]:
        """Extract search queries from text using simple heuristics."""
        queries = []

        # Look for explicit [SEARCH: query] markers
        explicit = re.findall(r"\[SEARCH:\s*(.+?)\]", text, re.IGNORECASE)
        if explicit:
            return explicit

        # Extract key phrases (simple heuristic: look for legal citations, case numbers, etc.)
        # Legal citation patterns: § 123 SGB V, § 123 Abs. 1 SGB V, etc.
        legal_patterns = re.findall(r"§\s*\d+\s*(?:Abs\.\s*\d+\s*)?(?:Satz\s*\d+\s*)?(?:SGB\s+[IVX]+|[A-Z]+)", text)
        if legal_patterns:
            queries.extend(legal_patterns[:2])

        # Case number patterns: S 31 KR 842/26, Az. 12345/26, etc.
        case_patterns = re.findall(r"(?:Az\.|Aktenzeichen|Akz\.?)\s*[:=]?\s*([A-Z]\s*\d+\s*[A-Z]+\s*\d+/\d+)", text)
        if case_patterns:
            queries.extend(case_patterns[:2])

        # If no specific patterns found, use the first sentence as query
        if not queries:
            sentences = re.split(r"[.!?]+", text)
            for s in sentences:
                s = s.strip()
                if len(s) > 20:
                    queries.append(s[:200])
                    break

        return queries[:3]

    def _append_search_results(self, prompt: str, results: list[dict]) -> str:
        """Append web search results to the user prompt."""
        if not results:
            return prompt

        lines = [prompt, "\n\n## Web Research\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"**{i}. {r.get('title', 'Unknown')}**")
            lines.append(f"   {r.get('url', '')}")
            lines.append(f"   {r.get('snippet', '')}")
            lines.append("")

        return "\n".join(lines)

    async def _handle_optional_search(self, content: str) -> str:
        """Handle optional search mode: extract [SEARCH: query] markers and fulfill them."""
        queries = re.findall(r"\[SEARCH:\s*(.+?)\]", content, re.IGNORECASE)
        if not queries:
            return ""

        results = []
        for q in queries[:3]:
            search_results = await self._perform_search(q)
            if search_results:
                results.append(f"### Results for: {q}\n")
                for r in search_results:
                    results.append(f"- **{r.get('title', 'Unknown')}**: {r.get('snippet', '')}\n")

        return "\n".join(results) if results else ""

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

            # Resolve tenant_id: use space.tenant_id if set, otherwise extract from case_dir path
            tenant_id = space.tenant_id
            if not tenant_id:
                # Extract from path: data/tenants/{tenant_id}/cases/{case_id}
                try:
                    # case_dir is like /path/to/data/tenants/{tenant_id}/cases/{case_id}
                    parts = case_dir.parts
                    tenants_idx = None
                    for i, part in enumerate(parts):
                        if part == "tenants":
                            tenants_idx = i
                            break
                    if tenants_idx is not None and tenants_idx + 1 < len(parts):
                        tenant_id = parts[tenants_idx + 1]
                except (AttributeError, IndexError):
                    pass

            # Get or create DMS instance (use case_id as scope_id, matching _get_dms_for_case pattern)
            cache_key = ("case", tenant_id, space.case_id)
            with _dms_cache_lock:
                if cache_key in _dms_cache:
                    dms = _dms_cache[cache_key]
                elif space.case_id in _dms_cache and isinstance(_dms_cache[space.case_id], DMS):
                    # Alias entry created by get_dms_for_project(case_id) or
                    # _get_dms_for_case — reuse it: one case, one DMS (§2.8).
                    dms = _dms_cache[space.case_id]
                    _dms_cache[cache_key] = dms
                else:
                    try:
                        dms_config = load_dms_config()
                    except Exception:
                        dms_config = {}

                    # Canonical case scope (bare case_id) — must match
                    # ``_get_dms_for_case`` so agent retrieval and case-scoped
                    # uploads share one ChromaDB project_id namespace.
                    from backend.api.routers.case_scoped import _case_scope_id

                    scope_id = _case_scope_id(tenant_id, space.case_id)
                    dms = DMS(
                        db_path=str(dms_dir / "dms.db"),
                        chroma_path=str(dms_dir / "chroma_db"),
                        config=dms_config,
                        project_id=scope_id,
                    )
                    _dms_cache[cache_key] = dms
                    _dms_cache.setdefault(scope_id, dms)

            # Use the event content as a query for hybrid retrieval
            query = event.content if isinstance(event.content, str) else str(event.content)
            if not query.strip():
                return []

            # Retrieve relevant chunks
            chunks = dms.auto_retrieve_for_topic(query, project_id=dms._project_id, k=8)
            logger.info(
                "AgentWorker: retrieved %d document chunks for space %s (case: %s, tenant: %s)",
                len(chunks),
                event.space_id,
                space.case_id,
                tenant_id,
            )
            return chunks

        except Exception as e:
            logger.debug("Could not retrieve document chunks for space %s: %s", event.space_id, e)
            return []

    def _build_system_prompt(self, role: str, meta: dict[str, Any], search_mode: str = "off") -> str:
        """Build the system prompt for the agent.

        Uses ComposerService if composition IDs are provided in metadata.
        Otherwise, falls back to explicit system_prompt/system_prompt_addon
        or a neutral default.
        """
        # Check if composition IDs are provided (4-layer prompt system)
        composition = meta.get("composition", {})
        if composition and any(composition.values()):
            try:
                composer = ComposerService()
                comp = Composition(
                    agent_core_id=composition.get("agent_core_id", ""),
                    argumentation_pattern_id=composition.get("argumentation_pattern_id", ""),
                    tone_profile_id=composition.get("tone_profile_id", ""),
                    prompt_modifier_id=composition.get("prompt_modifier_id", ""),
                )
                base_prompt = composer.compose(comp)
                if base_prompt:
                    # Add search instruction if optional mode
                    if search_mode == "optional":
                        base_prompt += (
                            "\n\nYou have access to web search. To search, include [SEARCH: query] in your response. "
                            "The search results will be appended to your response. "
                            "Use this for factual claims, legal citations, or current information."
                        )
                    return base_prompt
            except Exception as e:
                logger.warning("ComposerService failed: %s, falling back to default", e)

        # Fallback to explicit system_prompt / system_prompt_addon
        addon = meta.get("system_prompt_addon") or meta.get("system_prompt")
        if addon:
            base_prompt = addon
        else:
            # Neutral default (no hardcoded language)
            base_prompt = (
                f"You are a debate agent acting in the role '{role}'. "
                "Respond precisely and argumentatively, addressing the discussion context."
            )

        # Add search instruction if optional mode
        if search_mode == "optional":
            base_prompt += (
                "\n\nYou have access to web search. To search, include [SEARCH: query] in your response. "
                "The search results will be appended to your response. "
                "Use this for factual claims, legal citations, or current information."
            )

        return base_prompt
