"""Tests for CQRS Projectors (ADR-001).

Tests the 4 projectors:
- TreeProjector (SvelteFlow graph)
- ContextProjector (structured facts)
- BudgetProjector (token tracking)
- SynthesisProjector (Markdown report)
"""

from __future__ import annotations

import pytest

from backend.models.debate_event import DebateEvent
from backend.persistence.event_store import EventStore
from backend.services.interactive.projectors import ProjectorManager


@pytest.fixture()
def store(tmp_path) -> EventStore:
    """Isolated EventStore with projector integration."""
    return EventStore(db_path=tmp_path / "test_projectors.db")


@pytest.fixture()
def projector_manager(store) -> ProjectorManager:
    """ProjectorManager connected to the store's DB."""
    manager = ProjectorManager(store.conn)
    store.set_projector_manager(manager)
    return manager


@pytest.fixture()
def space(store) -> str:
    """Create a test space and return its ID."""
    s = store.create_space(title="Projector Test Space")
    return s.space_id


def _make_event(
    space_id: str,
    event_type: str = "AgentActed",
    actor_type: str = "agent",
    actor_id: str = "agent-1",
    content: str = "Test content",
    role: str | None = None,
    parent_id: str | None = None,
    metadata_json: dict | None = None,
    tokens_input: int | None = None,
    tokens_output: int | None = None,
) -> DebateEvent:
    return DebateEvent(
        event_id="test-evt-001",
        space_id=space_id,
        parent_id=parent_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        role=role,
        content=content,
        metadata_json=metadata_json or {},
        tokens_input=tokens_input,
        tokens_output=tokens_output,
    )


# ── TreeProjector Tests ──────────────────────────────────────────────────


class TestTreeProjector:
    def test_node_created_for_agent_acted(self, projector_manager, space):
        event = _make_event(space, event_type="AgentActed", role="strategist")
        projector_manager.get_tree_projector().safe_handle(event)
        nodes = projector_manager.get_tree_projector().get_nodes(space)
        assert len(nodes) == 1
        assert nodes[0]["event_type"] == "AgentActed"
        assert "strategist" in nodes[0]["label"].lower()

    def test_node_created_for_user_acted(self, projector_manager, space):
        event = _make_event(space, event_type="UserActed", actor_type="user", content="Focus on costs")
        projector_manager.get_tree_projector().safe_handle(event)
        nodes = projector_manager.get_tree_projector().get_nodes(space)
        assert len(nodes) == 1
        assert "Focus on costs" in nodes[0]["label"]

    def test_edge_created_with_parent(self, projector_manager, space):
        parent = _make_event(space, event_type="AgentActed", actor_id="a1", content="parent")
        parent.event_id = "parent-001"
        projector_manager.get_tree_projector().safe_handle(parent)

        child = _make_event(space, event_type="AgentActed", actor_id="a2", content="child", parent_id="parent-001")
        child.event_id = "child-001"
        projector_manager.get_tree_projector().safe_handle(child)

        edges = projector_manager.get_tree_projector().get_edges(space)
        assert len(edges) == 1
        assert edges[0]["source_id"] == "parent-001"
        assert edges[0]["target_id"] == "child-001"

    def test_tree_graph_structure(self, projector_manager, space):
        e1 = _make_event(space, event_type="AgentActed", content="root")
        e1.event_id = "root-1"
        projector_manager.get_tree_projector().safe_handle(e1)

        e2 = _make_event(space, event_type="UserActed", content="user input", parent_id="root-1")
        e2.event_id = "user-1"
        projector_manager.get_tree_projector().safe_handle(e2)

        graph = projector_manager.get_tree_projector().get_tree_graph(space)
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1

    def test_handles_correct_event_types(self, projector_manager):
        p = projector_manager.get_tree_projector()
        assert p.handles_event_type("AgentActed")
        assert p.handles_event_type("BranchForked")
        assert p.handles_event_type("MilestoneReached")
        assert not p.handles_event_type("SpaceCreated")

    def test_label_with_role(self, projector_manager, space):
        event = _make_event(space, event_type="AgentActed", role="critic", content="Too expensive")
        projector_manager.get_tree_projector().safe_handle(event)
        nodes = projector_manager.get_tree_projector().get_nodes(space)
        assert "critic" in nodes[0]["label"].lower()

    def test_label_truncates_long_content(self, projector_manager, space):
        long_content = "x" * 100
        event = _make_event(space, event_type="UserActed", content=long_content)
        projector_manager.get_tree_projector().safe_handle(event)
        nodes = projector_manager.get_tree_projector().get_nodes(space)
        assert len(nodes[0]["label"]) < 70  # truncated


# ── ContextProjector Tests ───────────────────────────────────────────────


class TestContextProjector:
    def test_claims_extracted(self, projector_manager, space):
        event = _make_event(
            space,
            event_type="AgentActed",
            metadata_json={
                "structured_output": {
                    "claims": ["Cost optimization needed", "Sustainability is key"],
                    "critiques": [],
                    "evidence": [],
                }
            },
        )
        projector_manager.get_context_projector().safe_handle(event)
        claims = projector_manager.get_context_projector().get_claims(space)
        assert len(claims) == 2
        assert claims[0]["fact_content"] == "Cost optimization needed"

    def test_critiques_extracted(self, projector_manager, space):
        event = _make_event(
            space,
            event_type="AgentActed",
            metadata_json={
                "structured_output": {
                    "claims": [],
                    "critiques": ["Budget too high"],
                    "evidence": [],
                }
            },
        )
        projector_manager.get_context_projector().safe_handle(event)
        critiques = projector_manager.get_context_projector().get_critiques(space)
        assert len(critiques) == 1
        assert critiques[0]["fact_content"] == "Budget too high"

    def test_evidence_extracted(self, projector_manager, space):
        event = _make_event(
            space,
            event_type="AgentActed",
            metadata_json={
                "structured_output": {
                    "claims": [],
                    "critiques": [],
                    "evidence": ["Source X from RAG doc"],
                }
            },
        )
        projector_manager.get_context_projector().safe_handle(event)
        evidence = projector_manager.get_context_projector().get_evidence(space)
        assert len(evidence) == 1

    def test_questions_extracted(self, projector_manager, space):
        event = _make_event(
            space,
            event_type="AgentActed",
            metadata_json={
                "structured_output": {
                    "questions": ["What is the budget constraint?"],
                }
            },
        )
        projector_manager.get_context_projector().safe_handle(event)
        questions = projector_manager.get_context_projector().get_open_questions(space)
        assert len(questions) == 1

    def test_content_stored_as_fact(self, projector_manager, space):
        event = _make_event(space, event_type="UserActed", content="Focus on sustainability")
        projector_manager.get_context_projector().safe_handle(event)
        facts = projector_manager.get_context_projector().get_facts(space, fact_type="content")
        assert len(facts) == 1
        assert facts[0]["fact_content"] == "Focus on sustainability"

    def test_handles_correct_event_types(self, projector_manager):
        p = projector_manager.get_context_projector()
        assert p.handles_event_type("AgentActed")
        assert p.handles_event_type("UserActed")
        assert p.handles_event_type("ToolExecuted")
        assert not p.handles_event_type("BranchForked")


# ── BudgetProjector Tests ────────────────────────────────────────────────


class TestBudgetProjector:
    def test_tokens_tracked(self, projector_manager, space):
        event = _make_event(space, tokens_input=500, tokens_output=200)
        projector_manager.get_budget_projector().safe_handle(event)
        budget = projector_manager.get_budget_projector().get_budget(space)
        assert len(budget) == 1
        assert budget[0]["tokens_input"] == 500
        assert budget[0]["tokens_output"] == 200

    def test_multiple_events_accumulate(self, projector_manager, space):
        e1 = _make_event(space, actor_id="agent-1", tokens_input=100, tokens_output=50)
        e2 = _make_event(space, actor_id="agent-1", tokens_input=200, tokens_output=80)
        projector_manager.get_budget_projector().safe_handle(e1)
        projector_manager.get_budget_projector().safe_handle(e2)
        budget = projector_manager.get_budget_projector().get_budget(space)
        assert budget[0]["tokens_input"] == 300
        assert budget[0]["tokens_output"] == 130
        assert budget[0]["event_count"] == 2

    def test_cost_calculated(self, projector_manager, space):
        event = _make_event(space, tokens_input=1000, tokens_output=1000)
        projector_manager.get_budget_projector().safe_handle(event)
        totals = projector_manager.get_budget_projector().get_total_cost(space)
        # 1000/1000 * 0.003 + 1000/1000 * 0.015 = 0.003 + 0.015 = 0.018
        assert abs(totals["total_cost"] - 0.018) < 0.001

    def test_no_tokens_no_record(self, projector_manager, space):
        event = _make_event(space, tokens_input=None, tokens_output=None)
        projector_manager.get_budget_projector().safe_handle(event)
        budget = projector_manager.get_budget_projector().get_budget(space)
        assert len(budget) == 0

    def test_total_cost_aggregation(self, projector_manager, space):
        e1 = _make_event(space, actor_id="a1", tokens_input=500, tokens_output=200)
        e2 = _make_event(space, actor_id="a2", tokens_input=300, tokens_output=100)
        projector_manager.get_budget_projector().safe_handle(e1)
        projector_manager.get_budget_projector().safe_handle(e2)
        totals = projector_manager.get_budget_projector().get_total_cost(space)
        assert totals["total_input"] == 800
        assert totals["total_output"] == 300
        assert totals["total_events"] == 2


# ── SynthesisProjector Tests ─────────────────────────────────────────────


class TestSynthesisProjector:
    def test_report_generated_on_consensus(self, projector_manager, store, space):
        # Find the SpaceCreated event (root)
        root_events = store.get_children(space, parent_id=None)
        space_created = [e for e in root_events if e.event_type == "SpaceCreated"][0]

        # Create events as children of SpaceCreated
        e1 = store.append_event(
            space_id=space, event_type="AgentActed", actor_type="agent",
            actor_id="a1", content="Strategy A is best",
            parent_id=space_created.event_id,
        )
        e2 = store.append_event(
            space_id=space, event_type="AgentActed", actor_type="agent",
            actor_id="a2", content="Agreed, let's proceed",
            parent_id=e1.event_id,
        )

        # Create consensus milestone
        milestone = _make_event(
            space,
            event_type="MilestoneReached",
            parent_id=e2.event_id,
            metadata_json={"milestone_type": "consensus", "summary": "All agree"},
        )
        milestone.event_id = "milestone-001"
        projector_manager.get_synthesis_projector().safe_handle(milestone)

        report = projector_manager.get_synthesis_projector().get_latest_report(space)
        assert report is not None
        assert "Strategy A" in report["content"]
        assert report["format"] == "markdown"

    def test_no_report_on_deadlock(self, projector_manager, store, space):
        e1 = store.append_event(
            space_id=space, event_type="AgentActed", actor_type="agent",
            actor_id="a1", content="I disagree",
        )
        milestone = _make_event(
            space,
            event_type="MilestoneReached",
            parent_id=e1.event_id,
            metadata_json={"milestone_type": "deadlock"},
        )
        projector_manager.get_synthesis_projector().safe_handle(milestone)
        report = projector_manager.get_synthesis_projector().get_latest_report(space)
        assert report is None

    def test_handles_only_milestone_events(self, projector_manager):
        p = projector_manager.get_synthesis_projector()
        assert p.handles_event_type("MilestoneReached")
        assert not p.handles_event_type("AgentActed")

    def test_reports_list(self, projector_manager, store, space):
        e1 = store.append_event(
            space_id=space, event_type="AgentActed", actor_type="agent",
            actor_id="a1", content="test",
        )
        m1 = _make_event(
            space, event_type="MilestoneReached", parent_id=e1.event_id,
            metadata_json={"milestone_type": "consensus"},
        )
        projector_manager.get_synthesis_projector().safe_handle(m1)
        reports = projector_manager.get_synthesis_projector().get_reports(space)
        assert len(reports) == 1


# ── ProjectorManager Integration Tests ───────────────────────────────────


class TestProjectorManager:
    def test_all_projectors_registered(self, projector_manager):
        assert len(projector_manager.projectors) == 4

    def test_event_dispatched_to_all_projectors(self, projector_manager, space):
        event = _make_event(
            space,
            event_type="AgentActed",
            content="Test dispatch",
            metadata_json={
                "structured_output": {"claims": ["Test claim"]},
            },
            tokens_input=100,
            tokens_output=50,
        )
        projector_manager.handle_event(event)

        # Tree projector should have a node
        nodes = projector_manager.get_tree_projector().get_nodes(space)
        assert len(nodes) == 1

        # Context projector should have facts
        claims = projector_manager.get_context_projector().get_claims(space)
        assert len(claims) == 1

        # Budget projector should have tokens
        budget = projector_manager.get_budget_projector().get_budget(space)
        assert len(budget) == 1

    def test_safe_handle_catches_exceptions(self, projector_manager, space):
        """Projector failures must not crash the write path."""
        bad_event = _make_event(space, event_type="AgentActed")
        # This should not raise
        for p in projector_manager.projectors:
            p.safe_handle(bad_event)
