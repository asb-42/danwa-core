"""SynthesisService – generates final deliverables from a debate space.

Takes the event tree of an interactive debate space and produces a
structured output in one of four formats:

- ``json``      — structured event tree (no LLM call, deterministic)
- ``markdown``  — clean narrative report, optionally LLM-compressed
- ``latex``     — LaTeX source ready for ``pdflatex`` (LLM-generated)
- ``pdf``       — LaTeX source intended for PDF rendering (LLM-generated;
                  actual binary compilation requires a LaTeX toolchain on
                  the host, which is not assumed here; the endpoint returns
                  the LaTeX source so the caller can compile or display it)

The service also persists the report via the ``SynthesisProjector``'s
``synthesis_reports`` table and emits a ``ContextSynthesized`` event so the
CQRS read model and SSE subscribers are kept in sync.

See ``docs/2026-07-19_interactive-backend.md`` §4.6 and §6 (Output actions).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.context_synthesizer import ContextSynthesizer
from backend.services.interactive.event_embeddings import EventEmbeddingStore
from backend.services.llm_service import GenerationResult, LLMService

logger = logging.getLogger(__name__)

# ── Format constants ────────────────────────────────────────────────────────

FORMAT_MARKDOWN = "markdown"
FORMAT_LATEX = "latex"
FORMAT_PDF = "pdf"
FORMAT_JSON = "json"

VALID_FORMATS = {FORMAT_MARKDOWN, FORMAT_LATEX, FORMAT_PDF, FORMAT_JSON}

# System prompts for LLM-driven synthesis.  Language-neutral (English SSOT)
# so the output is consistent regardless of the debate's working language.
_SYSTEM_PROMPT_MARKDOWN = (
    "You are a synthesis engine. Given a structured transcript of a debate, "
    "produce a clean, well-organised Markdown report. "
    "Preserve the key arguments, counterarguments, and conclusions. "
    "Do not invent content that is not in the transcript. "
    "Use headings (##, ###) and bullet points for readability."
)

_SYSTEM_PROMPT_LATEX = (
    "You are a synthesis engine. Given a structured transcript of a debate, "
    "produce a complete, self-contained LaTeX document (article class) that "
    "renders the debate as a readable report. "
    "Use \\section, \\subsection, and itemize/enumerate where appropriate. "
    "Do not invent content. Output only the LaTeX source."
)

# Rough token estimate: 1 token ≈ 4 characters.
_CHARS_PER_TOKEN = 4


class SynthesisResult:
    """Container for a synthesis run."""

    def __init__(
        self,
        space_id: str,
        fmt: str,
        content: str,
        event_count: int,
        tokens_input: int = 0,
        tokens_output: int = 0,
        model: str = "",
        source_event_ids: list[str] | None = None,
        report_id: str | None = None,
    ):
        self.space_id = space_id
        self.format = fmt
        self.content = content
        self.event_count = event_count
        self.tokens_input = tokens_input
        self.tokens_output = tokens_output
        self.model = model
        self.source_event_ids = source_event_ids or []
        self.report_id = report_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "space_id": self.space_id,
            "format": self.format,
            "content": self.content,
            "event_count": self.event_count,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "model": self.model,
            "source_event_ids": self.source_event_ids,
            "report_id": self.report_id,
            "generated_at": datetime.now(UTC).isoformat(),
        }


class SynthesisService:
    """Builds final deliverables from the interactive debate event tree.

    Usage::

        svc = SynthesisService(event_store)
        result = await svc.synthesize(
            space_id="...",
            fmt="markdown",
            llm_profile_id="...",
            use_llm=True,
        )
        print(result.content)
    """

    def __init__(
        self,
        event_store: EventStore,
        embedding_store: EventEmbeddingStore | None = None,
        llm_service: LLMService | None = None,
    ):
        self.event_store = event_store
        self.embedding_store = embedding_store
        self._llm = llm_service  # lazily created per profile in synthesize()
        self._context_synth = ContextSynthesizer(event_store, embedding_store)

    async def synthesize(
        self,
        space_id: str,
        fmt: str = FORMAT_MARKDOWN,
        *,
        max_depth: int | None = None,
        include_side_branches: bool = True,
        llm_profile_id: str | None = None,
        use_llm: bool = True,
    ) -> SynthesisResult:
        """Produce a synthesised deliverable for a space.

        Args:
            space_id: The debate space to synthesise.
            fmt: Output format — ``markdown``, ``latex``, ``pdf``, ``json``.
            max_depth: If set, only include events up to this BFS depth.
            include_side_branches: Whether to include events from forked
                branches (not just the main thread).
            llm_profile_id: LLM profile for LLM-driven formats.
            use_llm: If ``False``, produce a raw transcript without LLM
                compression (useful for ``markdown`` when cost matters).
        """
        if fmt not in VALID_FORMATS:
            raise ValueError(
                f"Unsupported format {fmt!r}. Valid: {sorted(VALID_FORMATS)}"
            )

        # 1. Collect events
        events = self._collect_events(
            space_id, max_depth=max_depth, include_side_branches=include_side_branches
        )
        source_ids = [e.event_id for e in events]

        if not events:
            return SynthesisResult(
                space_id=space_id,
                fmt=fmt,
                content="",
                event_count=0,
                source_event_ids=[],
            )

        # 2. Render by format
        tokens_in = 0
        tokens_out = 0
        model = ""

        if fmt == FORMAT_JSON:
            content = self._render_json(events, space_id)
            # JSON is deterministic — no tokens consumed.

        elif fmt == FORMAT_MARKDOWN and not use_llm:
            content = self._render_transcript_markdown(events, space_id)

        else:
            # LLM-driven formats: markdown (compressed), latex, pdf
            transcript = self._render_transcript_markdown(events, space_id)
            system_prompt = (
                _SYSTEM_PROMPT_LATEX if fmt in (FORMAT_LATEX, FORMAT_PDF)
                else _SYSTEM_PROMPT_MARKDOWN
            )
            result = await self._call_llm(
                system_prompt=system_prompt,
                transcript=transcript,
                llm_profile_id=llm_profile_id,
            )
            content = result.content or transcript  # fall back to raw transcript
            tokens_in = result.tokens_in
            tokens_out = result.tokens_out
            model = result.model

        # 3. Persist the report
        report_id = self._persist_report(
            space_id=space_id,
            fmt=fmt,
            content=content,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            source_event_ids=source_ids,
        )

        # 4. Emit a ContextSynthesized event so the read model + SSE update
        self._emit_synthesis_event(
            space_id=space_id,
            fmt=fmt,
            report_id=report_id,
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            model=model,
            source_event_ids=source_ids,
        )

        return SynthesisResult(
            space_id=space_id,
            fmt=fmt,
            content=content,
            event_count=len(events),
            tokens_input=tokens_in,
            tokens_output=tokens_out,
            model=model,
            source_event_ids=source_ids,
            report_id=report_id,
        )

    # ── Event collection ──────────────────────────────────────────────────

    def _collect_events(
        self,
        space_id: str,
        *,
        max_depth: int | None,
        include_side_branches: bool,
    ) -> list[DebateEvent]:
        """Collect events for synthesis.

        Without side branches, this walks the main thread (root → first child
        chain).  With side branches, it returns the full tree.
        """
        if include_side_branches:
            tree = self.event_store.get_full_tree(space_id)
            if max_depth is not None:
                # Build depth map via parent chain
                depth_cache: dict[str | None, int] = {None: -1}
                result: list[DebateEvent] = []
                for evt in tree:
                    d = depth_cache.get(evt.parent_id, -1) + 1
                    depth_cache[evt.event_id] = d
                    if d <= max_depth:
                        result.append(evt)
                return result
            return tree

        # Main thread only: find root, walk first-child chain
        roots = self.event_store.get_children(space_id, parent_id=None)
        if not roots:
            return self.event_store.get_full_tree(space_id)

        result: list[DebateEvent] = []
        current = roots[0]
        depth = 0
        visited: set[str] = set()
        while current and current.event_id not in visited:
            if max_depth is not None and depth > max_depth:
                break
            visited.add(current.event_id)
            result.append(current)
            children = self.event_store.get_children(space_id, parent_id=current.event_id)
            current = children[0] if children else None
            depth += 1
        return result

    # ── Renderers (no LLM) ────────────────────────────────────────────────

    def _render_json(self, events: list[DebateEvent], space_id: str) -> str:
        """Render the event tree as a structured JSON document."""
        space = self.event_store.get_space(space_id)
        title = space.title if space else space_id

        nodes = []
        for evt in events:
            nodes.append({
                "event_id": evt.event_id,
                "parent_id": evt.parent_id,
                "event_type": evt.event_type,
                "actor_type": evt.actor_type,
                "actor_id": evt.actor_id,
                "role": evt.role,
                "content": evt.content if isinstance(evt.content, str) else str(evt.content),
                "created_at": evt.created_at.isoformat() if evt.created_at else None,
                "tokens_input": evt.tokens_input,
                "tokens_output": evt.tokens_output,
            })

        document = {
            "space_id": space_id,
            "title": title,
            "format": FORMAT_JSON,
            "event_count": len(events),
            "generated_at": datetime.now(UTC).isoformat(),
            "events": nodes,
        }
        return json.dumps(document, indent=2, ensure_ascii=False)

    def _render_transcript_markdown(
        self, events: list[DebateEvent], space_id: str
    ) -> str:
        """Render a raw transcript (used as LLM input or fallback output)."""
        space = self.event_store.get_space(space_id)
        title = space.title if space else space_id

        lines: list[str] = [f"# {title}", ""]
        if space and space.description:
            lines.append(f"> {space.description}")
            lines.append("")

        for i, evt in enumerate(events, 1):
            content = evt.content if isinstance(evt.content, str) else json.dumps(evt.content, ensure_ascii=False)
            role_str = f" ({evt.role})" if evt.role else ""
            lines.append(f"## {i}. {evt.actor_id}{role_str} — [{evt.event_type}]")
            lines.append("")
            lines.append(content)
            lines.append("")

        return "\n".join(lines)

    # ── LLM call ──────────────────────────────────────────────────────────

    async def _call_llm(
        self,
        system_prompt: str,
        transcript: str,
        llm_profile_id: str | None,
    ) -> GenerationResult:
        """Call the LLM to synthesise the transcript into a report."""
        try:
            llm = LLMService(profile_id=llm_profile_id)
            result = await llm.generate(
                prompt=transcript,
                system_prompt=system_prompt,
            )
            return result
        except Exception as e:
            logger.error("Synthesis LLM call failed: %s", e)
            return GenerationResult(content=transcript, model="fallback")

    # ── Persistence + event emission ──────────────────────────────────────

    def _persist_report(
        self,
        space_id: str,
        fmt: str,
        content: str,
        tokens_input: int,
        tokens_output: int,
        source_event_ids: list[str],
    ) -> str:
        """Store the report in the synthesis_reports table (shared with projector).

        Creates the table idempotently so the service works even when no
        ``ProjectorManager`` is attached to the ``EventStore`` (e.g. in tests
        or when projectors are disabled).
        """
        report_id = f"synth-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        conn = self.event_store.conn
        # Ensure the table exists (SynthesisProjector normally creates it,
        # but the service may run without projectors attached).
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS synthesis_reports (
                report_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                milestone_event_id TEXT NOT NULL,
                format TEXT NOT NULL DEFAULT 'markdown',
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_synthesis_space ON synthesis_reports(space_id);
        """)
        conn.execute(
            """INSERT OR REPLACE INTO synthesis_reports
               (report_id, space_id, milestone_event_id, format, content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                report_id,
                space_id,
                report_id,  # milestone_event_id — use report_id for direct synth
                fmt,
                content,
                now,
            ),
        )
        conn.commit()
        logger.info(
            "Synthesis report %s persisted (%s, %d chars) for space %s",
            report_id, fmt, len(content), space_id,
        )
        return report_id

    def _emit_synthesis_event(
        self,
        space_id: str,
        fmt: str,
        report_id: str,
        tokens_input: int,
        tokens_output: int,
        model: str,
        source_event_ids: list[str],
    ) -> None:
        """Append a ``ContextSynthesized`` event to the event log."""
        # Find the latest event to attach as parent (or None for root).
        tree = self.event_store.get_full_tree(space_id)
        parent_id = tree[-1].event_id if tree else None

        self.event_store.append_event(
            space_id=space_id,
            event_type="ContextSynthesized",
            actor_type="system",
            actor_id="synthesis-service",
            content=f"Synthesis report generated ({fmt}): {report_id}",
            parent_id=parent_id,
            metadata_json={
                "report_id": report_id,
                "format": fmt,
                "tokens_input": tokens_input,
                "tokens_output": tokens_output,
                "model": model,
                "source_event_ids": source_event_ids[:50],  # cap for metadata size
                "source_event_count": len(source_event_ids),
            },
            tokens_input=tokens_input or None,
            tokens_output=tokens_output or None,
        )
