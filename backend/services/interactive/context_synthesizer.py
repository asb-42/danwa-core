"""ContextSynthesizer – the heart of the interactive debate engine.

Given a target_event_id (where the user clicked [+]) and an agent_bundle,
it builds the optimal context window for the next agent call:

1. **Direct Thread**: Traces parent_id chain → immediate conversational thread.
2. **Side Branches**: Queries ChromaDB for semantically relevant events
   from parallel forks (prevents token explosion).
3. **Agent Core Prompt**: Injects synthesized context into the prompt template.

This replaces LangGraph's state mutation with Event Sourcing semantics:
we never mutate – we only read events and synthesize context.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.event_embeddings import EventEmbeddingStore

logger = logging.getLogger(__name__)

# Token budget defaults
DEFAULT_MAX_THREAD_TOKENS = 4000
DEFAULT_MAX_SIDE_BRANCH_TOKENS = 2000
DEFAULT_SIDE_BRANCH_RESULTS = 5


class ContextWindow:
    """Synthesized context for an agent call."""

    def __init__(
        self,
        thread_events: list[DebateEvent],
        side_branch_events: list[dict],  # From ChromaDB (with relevance_score)
        target_event: DebateEvent,
        token_budget_used: int = 0,
    ):
        self.thread_events = thread_events
        self.side_branch_events = side_branch_events
        self.target_event = target_event
        self.token_budget_used = token_budget_used

    def to_prompt_context(self) -> str:
        """Render the context window as a structured prompt section."""
        lines: list[str] = []

        # Direct thread (chronological)
        if self.thread_events:
            lines.append("## Direkter Diskussionsverlauf")
            lines.append("")
            for evt in self.thread_events:
                prefix = self._actor_label(evt)
                content = evt.content if isinstance(evt.content, str) else str(evt.content)
                # Truncate very long content
                if len(content) > 1000:
                    content = content[:1000] + " [...]"
                lines.append(f"**{prefix}**: {content}")
                lines.append("")

        # Side branches (ranked by relevance)
        if self.side_branch_events:
            lines.append("## Relevante Nebenzweige")
            lines.append("")
            for sb in self.side_branch_events[:3]:  # Top 3 only
                meta = sb.get("metadata", {})
                actor = meta.get("actor_id", "unknown")
                score = sb.get("relevance_score", 0)
                text = sb.get("text", "")
                if len(text) > 500:
                    text = text[:500] + " [...]"
                lines.append(f"**{actor}** (Relevanz: {score:.2f}): {text}")
                lines.append("")

        return "\n".join(lines)

    def to_metadata(self) -> dict[str, Any]:
        """Return metadata about the synthesized context."""
        return {
            "thread_depth": len(self.thread_events),
            "side_branches_included": len(self.side_branch_events),
            "target_event_id": self.target_event.event_id,
            "token_budget_used": self.token_budget_used,
        }

    @staticmethod
    def _actor_label(evt: DebateEvent) -> str:
        if evt.role:
            return f"{evt.actor_id} ({evt.role})"
        return evt.actor_id


class ContextSynthesizer:
    """Builds context windows for agent calls in the interactive debate mode.

    Usage:
        synth = ContextSynthesizer(event_store, embedding_store)
        window = synth.synthesize(
            space_id="...",
            target_event_id="...",
            agent_bundle={"role": "strategist", "llm_profile_id": "..."},
        )
        prompt_context = window.to_prompt_context()
    """

    def __init__(
        self,
        event_store: EventStore,
        embedding_store: EventEmbeddingStore | None = None,
        max_thread_tokens: int = DEFAULT_MAX_THREAD_TOKENS,
        max_side_branch_tokens: int = DEFAULT_MAX_SIDE_BRANCH_TOKENS,
        max_side_branch_results: int = DEFAULT_SIDE_BRANCH_RESULTS,
    ):
        self.event_store = event_store
        self.embedding_store = embedding_store
        self.max_thread_tokens = max_thread_tokens
        self.max_side_branch_tokens = max_side_branch_tokens
        self.max_side_branch_results = max_side_branch_results

    def synthesize(
        self,
        space_id: str,
        target_event_id: str,
        agent_bundle: dict[str, Any] | None = None,
        include_side_branches: bool = True,
    ) -> ContextWindow:
        """Synthesize the context window for a new agent event.

        Args:
            space_id: The debate space ID.
            target_event_id: The event the user clicked [+] on.
            agent_bundle: Agent configuration from danwa-modules.
            include_side_branches: Whether to search for relevant side branches.

        Returns:
            ContextWindow with thread + side branches.
        """
        target_event = self.event_store.get_event(target_event_id)
        if not target_event or target_event.space_id != space_id:
            raise ValueError(f"Event {target_event_id} not found in space {space_id}")

        # 1. Build direct thread (parent chain)
        thread = self._build_thread(space_id, target_event)

        # 2. Find relevant side branches via embeddings
        side_branches: list[dict] = []
        if include_side_branches and self.embedding_store:
            side_branches = self._find_side_branches(
                space_id, target_event, thread
            )

        # 3. Estimate token usage (rough: 1 token ≈ 4 chars)
        total_chars = sum(
            len(e.content) if isinstance(e.content, str) else len(str(e.content))
            for e in thread
        )
        total_chars += sum(len(sb.get("text", "")) for sb in side_branches)
        estimated_tokens = total_chars // 4

        return ContextWindow(
            thread_events=thread,
            side_branch_events=side_branches,
            target_event=target_event,
            token_budget_used=estimated_tokens,
        )

    def _build_thread(
        self,
        space_id: str,
        target_event: DebateEvent,
        max_depth: int = 20,
    ) -> list[DebateEvent]:
        """Trace the parent chain from target_event to the root.

        Returns events in chronological order (root first).
        """
        thread: list[DebateEvent] = []
        current = target_event
        visited: set[str] = set()

        while current and current.event_id not in visited:
            visited.add(current.event_id)
            thread.append(current)
            if current.parent_id:
                current = self.event_store.get_event(current.parent_id)
            else:
                break

        # Reverse to get chronological order (root first)
        thread.reverse()

        # Trim by token budget
        result: list[DebateEvent] = []
        tokens_used = 0
        for evt in thread:
            content_len = len(evt.content) if isinstance(evt.content, str) else len(str(evt.content))
            evt_tokens = content_len // 4
            if tokens_used + evt_tokens > self.max_thread_tokens:
                break
            result.append(evt)
            tokens_used += evt_tokens

        return result

    def _find_side_branches(
        self,
        space_id: str,
        target_event: DebateEvent,
        thread: list[DebateEvent],
    ) -> list[dict]:
        """Find semantically relevant events from other branches."""
        # Build query from recent thread context
        recent_content = " ".join(
            e.content if isinstance(e.content, str) else str(e.content)
            for e in thread[-3:]  # Last 3 events as query
        )
        if not recent_content.strip():
            return []

        # Exclude events already in the thread
        exclude_ids = [e.event_id for e in thread]

        results = self.embedding_store.search_similar(
            space_id=space_id,
            query=recent_content,
            exclude_event_ids=exclude_ids,
            n_results=self.max_side_branch_results,
        )

        # Filter by token budget
        result: list[dict] = []
        tokens_used = 0
        for r in results:
            text_len = len(r.get("text", ""))
            r_tokens = text_len // 4
            if tokens_used + r_tokens > self.max_side_branch_tokens:
                break
            result.append(r)
            tokens_used += r_tokens

        return result
