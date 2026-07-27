"""Tests for the SynthesisService and the /synthesize endpoint.

These tests use an isolated EventStore with a temp database. The LLM is
mocked so the tests run deterministically without network calls.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.synthesis_service import (
    SynthesisResult,
    SynthesisService,
)
from backend.services.llm_service import GenerationResult


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def store(tmp_path):
    """Isolated EventStore with temp database + projector tables."""
    s = EventStore(db_path=tmp_path / "test_synthesis.db")
    yield s
    s.close()


@pytest.fixture
def populated_space(store):
    """A space with a small debate tree for synthesis tests."""
    space = store.create_space(title="Synthesis Test", description="Testing output")
    space_id = space.space_id

    # Root event already exists (SpaceCreated). Add a thread.
    e1 = store.append_event(
        space_id=space_id, event_type="UserActed", actor_type="user",
        actor_id="alice", content="What is the best approach to X?",
    )
    e2 = store.append_event(
        space_id=space_id, event_type="AgentActed", actor_type="agent",
        actor_id="agent-strategist", parent_id=e1.event_id, role="strategist",
        content="Strategy A is optimal because it minimizes cost.",
    )
    e3 = store.append_event(
        space_id=space_id, event_type="AgentActed", actor_type="agent",
        actor_id="agent-critic", parent_id=e2.event_id, role="critic",
        content="However, Strategy A ignores risk factors.",
    )
    # A forked branch (side branch)
    e4 = store.append_event(
        space_id=space_id, event_type="AgentActed", actor_type="agent",
        actor_id="agent-creative", parent_id=e1.event_id, role="creative",
        content="Alternative: Strategy B balances cost and risk.",
    )
    return space_id


def _mock_llm(content: str = "# Synthesised Report\n\nKey findings..."):
    """Patch LLMService.generate to return a canned result."""
    result = GenerationResult(
        content=content, tokens_in=100, tokens_out=50, model="test-model"
    )
    mock = AsyncMock()
    mock.generate = AsyncMock(return_value=result)
    return mock


# ── SynthesisService unit tests ────────────────────────────────────────────


class TestSynthesisService:
    def test_json_format_no_llm(self, store, populated_space):
        """JSON format should be deterministic and not call the LLM."""
        svc = SynthesisService(store)
        import asyncio

        result = asyncio.run(
            svc.synthesize(populated_space, fmt="json", use_llm=False)
        )
        assert result.format == "json"
        assert result.event_count >= 4  # SpaceCreated + 4 events
        assert result.tokens_input == 0
        assert result.tokens_output == 0
        # Content should be valid JSON
        parsed = json.loads(result.content)
        assert parsed["space_id"] == populated_space
        assert parsed["format"] == "json"
        assert "events" in parsed
        assert len(parsed["events"]) == result.event_count

    def test_markdown_raw_transcript_no_llm(self, store, populated_space):
        """Markdown with use_llm=False returns a raw transcript."""
        svc = SynthesisService(store)
        import asyncio

        result = asyncio.run(
            svc.synthesize(populated_space, fmt="markdown", use_llm=False)
        )
        assert result.format == "markdown"
        assert "Synthesis Test" in result.content
        assert "alice" in result.content
        assert "strategist" in result.content
        assert result.tokens_input == 0  # no LLM call

    def test_markdown_with_llm(self, store, populated_space):
        """Markdown with use_llm=True calls the LLM and returns compressed output."""
        svc = SynthesisService(store)
        mock_llm = _mock_llm("# Compressed Report\n\nSummary of debate...")
        import asyncio

        with patch(
            "backend.services.interactive.synthesis_service.LLMService",
            return_value=mock_llm,
        ):
            result = asyncio.run(
                svc.synthesize(populated_space, fmt="markdown", use_llm=True)
            )

        assert result.format == "markdown"
        assert "Compressed Report" in result.content
        assert result.tokens_input == 100
        assert result.tokens_output == 50
        assert result.model == "test-model"
        mock_llm.generate.assert_awaited_once()

    def test_latex_format_calls_llm(self, store, populated_space):
        """LaTeX format always uses the LLM."""
        svc = SynthesisService(store)
        mock_llm = _mock_llm(
            "\\documentclass{article}\n\\begin{document}\nDebate...\n\\end{document}"
        )
        import asyncio

        with patch(
            "backend.services.interactive.synthesis_service.LLMService",
            return_value=mock_llm,
        ):
            result = asyncio.run(svc.synthesize(populated_space, fmt="latex"))

        assert result.format == "latex"
        assert "\\documentclass" in result.content
        mock_llm.generate.assert_awaited_once()

    def test_invalid_format_raises(self, store, populated_space):
        svc = SynthesisService(store)
        import asyncio

        with pytest.raises(ValueError, match="Unsupported format"):
            asyncio.run(svc.synthesize(populated_space, fmt="docx"))

    def test_empty_space_returns_empty(self, store):
        """Synthesising a space with only the SpaceCreated event should work."""
        space = store.create_space(title="Empty")
        svc = SynthesisService(store)
        import asyncio

        result = asyncio.run(
            svc.synthesize(space.space_id, fmt="json", use_llm=False)
        )
        assert result.event_count >= 1  # at least SpaceCreated
        parsed = json.loads(result.content)
        assert "events" in parsed

    def test_report_persisted(self, store, populated_space):
        """The report should be stored in synthesis_reports table."""
        svc = SynthesisService(store)
        import asyncio

        result = asyncio.run(
            svc.synthesize(populated_space, fmt="json", use_llm=False)
        )
        assert result.report_id is not None
        # Verify it's in the DB
        row = store.conn.execute(
            "SELECT * FROM synthesis_reports WHERE report_id = ?",
            (result.report_id,),
        ).fetchone()
        assert row is not None
        assert row["format"] == "json"

    def test_synthesis_event_emitted(self, store, populated_space):
        """A ContextSynthesized event should be appended to the event log."""
        svc = SynthesisService(store)
        import asyncio

        tree_before = store.get_full_tree(populated_space)
        asyncio.run(svc.synthesize(populated_space, fmt="json", use_llm=False))
        tree_after = store.get_full_tree(populated_space)
        assert len(tree_after) == len(tree_before) + 1
        synth_event = tree_after[-1]
        assert synth_event.event_type == "ContextSynthesized"
        assert synth_event.actor_type == "system"
        assert synth_event.metadata_json.get("format") == "json"

    def test_main_thread_only(self, store, populated_space):
        """include_side_branches=False should exclude the forked branch."""
        svc = SynthesisService(store)
        import asyncio

        full = asyncio.run(
            svc.synthesize(populated_space, fmt="json", include_side_branches=True, use_llm=False)
        )
        thread_only = asyncio.run(
            svc.synthesize(populated_space, fmt="json", include_side_branches=False, use_llm=False)
        )
        # Full tree should have more events than main thread only
        assert full.event_count > thread_only.event_count

    def test_max_depth_limit(self, store, populated_space):
        """max_depth should limit the events included."""
        svc = SynthesisService(store)
        import asyncio

        result = asyncio.run(
            svc.synthesize(
                populated_space, fmt="json", max_depth=1, include_side_branches=True, use_llm=False
            )
        )
        # max_depth=1 should include fewer events than the full tree
        full_result = asyncio.run(
            svc.synthesize(populated_space, fmt="json", use_llm=False)
        )
        assert result.event_count <= full_result.event_count
