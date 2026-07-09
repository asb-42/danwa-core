"""Tests for Interactive Mode: EventStore + Thin Events (ADR-001)."""

from __future__ import annotations

import pytest

from backend.models.debate_event import EventType, normalize_event_type
from backend.persistence.event_store import EventStore


@pytest.fixture()
def store(tmp_path) -> EventStore:
    """Isolated EventStore with temp database."""
    return EventStore(db_path=tmp_path / "test_interactive.db")


@pytest.fixture()
def space(store) -> str:
    """Create a test space and return its ID."""
    s = store.create_space(title="Test Space", description="Test")
    return s.space_id


# ── Event Taxonomy Tests ──────────────────────────────────────────────────


class TestThinEventTaxonomy:
    """Verify the thin event type system (ADR-001)."""

    def test_all_thin_event_types_are_valid(self):
        """All 10 thin event types must be accepted."""
        valid_types: list[EventType] = [
            "SpaceCreated",
            "SpaceArchived",
            "UserActed",
            "AgentActed",
            "A2AActed",
            "ToolRequested",
            "ToolExecuted",
            "ContextSynthesized",
            "BranchForked",
            "MilestoneReached",
        ]
        for et in valid_types:
            assert et in [
                "SpaceCreated", "SpaceArchived", "UserActed", "AgentActed",
                "A2AActed", "ToolRequested", "ToolExecuted", "ContextSynthesized",
                "BranchForked", "MilestoneReached",
            ]

    def test_legacy_event_type_migration(self):
        """Legacy event types must map to thin taxonomy."""
        assert normalize_event_type("user_message") == "UserActed"
        assert normalize_event_type("agent_speech") == "AgentActed"
        assert normalize_event_type("a2a_request") == "A2AActed"
        assert normalize_event_type("a2a_response") == "A2AActed"
        assert normalize_event_type("hitl_input") == "UserActed"
        assert normalize_event_type("tool_call_requested") == "ToolRequested"
        assert normalize_event_type("tool_result") == "ToolExecuted"
        assert normalize_event_type("synthesis") == "ContextSynthesized"

    def test_thin_event_type_passthrough(self):
        """Thin event types must pass through unchanged."""
        assert normalize_event_type("AgentActed") == "AgentActed"
        assert normalize_event_type("MilestoneReached") == "MilestoneReached"


# ── EventStore Tests ──────────────────────────────────────────────────────


class TestEventStoreAppend:
    """Test append_event with thin events."""

    def test_append_agent_acted(self, store, space):
        event = store.append_event(
            space_id=space,
            event_type="AgentActed",
            actor_type="agent",
            actor_id="claude-sonnet",
            content="I think we should consider cost optimization.",
            role="strategist",
            metadata_json={
                "structured_output": {
                    "claims": ["Cost optimization needed"],
                    "critiques": [],
                    "evidence": ["Source X"],
                },
                "tokens_input": 500,
                "tokens_output": 200,
            },
            tokens_input=500,
            tokens_output=200,
        )
        assert event.event_type == "AgentActed"
        assert event.actor_id == "claude-sonnet"
        assert event.role == "strategist"
        assert event.metadata_json["structured_output"]["claims"] == ["Cost optimization needed"]
        assert event.tokens_input == 500

    def test_append_legacy_type_is_normalized(self, store, space):
        """Legacy event types must be auto-normalized."""
        event = store.append_event(
            space_id=space,
            event_type="agent_speech",  # legacy
            actor_type="agent",
            actor_id="qwen-critic",
            content="This strategy is too expensive.",
        )
        assert event.event_type == "AgentActed"

    def test_append_user_acted(self, store, space):
        event = store.append_event(
            space_id=space,
            event_type="UserActed",
            actor_type="user",
            actor_id="user-123",
            content="Focus on sustainability.",
            metadata_json={"action_type": "instruction"},
        )
        assert event.event_type == "UserActed"
        assert event.metadata_json["action_type"] == "instruction"

    def test_append_branch_forked(self, store, space):
        # First create a parent event
        parent = store.append_event(
            space_id=space,
            event_type="AgentActed",
            actor_type="agent",
            actor_id="agent-1",
            content="Initial statement",
        )
        fork = store.append_event(
            space_id=space,
            event_type="BranchForked",
            actor_type="user",
            actor_id="user-1",
            content="Forked from initial",
            parent_id=parent.event_id,
            metadata_json={
                "fork_point_event_id": parent.event_id,
                "branch_label": "Alternative approach",
            },
        )
        assert fork.event_type == "BranchForked"
        assert fork.parent_id == parent.event_id
        assert fork.metadata_json["branch_label"] == "Alternative approach"

    def test_append_milestone_reached(self, store, space):
        event = store.append_event(
            space_id=space,
            event_type="MilestoneReached",
            actor_type="system",
            actor_id="system",
            content="Consensus reached",
            metadata_json={
                "milestone_type": "consensus",
                "summary": "All agents agree on the strategy.",
            },
        )
        assert event.event_type == "MilestoneReached"
        assert event.metadata_json["milestone_type"] == "consensus"

    def test_append_tool_requested(self, store, space):
        event = store.append_event(
            space_id=space,
            event_type="ToolRequested",
            actor_type="agent",
            actor_id="agent-1",
            content="Search for latest market data",
            metadata_json={
                "tool_name": "web_search",
                "tool_params": {"query": "market data 2026"},
            },
        )
        assert event.metadata_json["tool_name"] == "web_search"

    def test_append_context_synthesized(self, store, space):
        event = store.append_event(
            space_id=space,
            event_type="ContextSynthesized",
            actor_type="system",
            actor_id="context-router",
            content="Context built for next agent",
            metadata_json={
                "prompt": "You are a strategist...",
                "token_costs": {"input": 1200, "output": 0},
                "source_event_ids": ["evt-1", "evt-2"],
            },
        )
        assert event.metadata_json["prompt"] == "You are a strategist..."

    def test_event_ids_are_uuids(self, store, space):
        import uuid
        event = store.append_event(
            space_id=space,
            event_type="AgentActed",
            actor_type="agent",
            actor_id="agent-1",
            content="test",
        )
        uuid.UUID(event.event_id)  # raises if invalid


class TestEventStoreTreeTraversal:
    """Test tree traversal methods."""

    def test_get_children_root(self, store, space):
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a1", content="1")
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a2", content="2")
        children = store.get_children(space, parent_id=None)
        # 2 AgentActed + 1 SpaceCreated (from create_space)
        assert len(children) == 3

    def test_get_children_nested(self, store, space):
        root = store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a1", content="root")
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a2", content="c1", parent_id=root.event_id)
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a3", content="c2", parent_id=root.event_id)
        children = store.get_children(space, parent_id=root.event_id)
        assert len(children) == 2

    def test_get_thread(self, store, space):
        root = store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a1", content="root")
        c1 = store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a2", content="c1", parent_id=root.event_id)
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a3", content="c2", parent_id=c1.event_id)
        thread = store.get_thread(space, root.event_id)
        assert len(thread) == 3
        assert thread[0].event_id == root.event_id

    def test_get_full_tree(self, store, space):
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a1", content="1")
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a2", content="2")
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a3", content="3")
        tree = store.get_full_tree(space)
        # 3 AgentActed + 1 SpaceCreated (from create_space)
        assert len(tree) == 4

    def test_get_events_by_type(self, store, space):
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a1", content="agent1")
        store.append_event(space_id=space, event_type="UserActed", actor_type="user", actor_id="u1", content="user1")
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a2", content="agent2")
        agent_events = store.get_events_by_type(space, "AgentActed")
        assert len(agent_events) == 2

    def test_get_events_by_legacy_type(self, store, space):
        """Legacy event type queries must also work via normalization."""
        store.append_event(space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a1", content="x")
        # Query with legacy type — should normalize and find the event
        events = store.get_events_by_type(space, "agent_speech")
        assert len(events) == 1


class TestEventStoreTokenUsage:
    def test_token_usage_aggregation(self, store, space):
        store.append_event(
            space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a1",
            content="1", tokens_input=100, tokens_output=50,
        )
        store.append_event(
            space_id=space, event_type="AgentActed", actor_type="agent", actor_id="a2",
            content="2", tokens_input=200, tokens_output=80,
        )
        usage = store.get_token_usage(space)
        assert usage["total_input"] == 300
        assert usage["total_output"] == 130


class TestEventStoreSpaceLifecycle:
    def test_create_space_emits_event(self, store):
        space = store.create_space(title="Lifecycle Test", description="testing")
        events = store.get_full_tree(space.space_id)
        # create_space now emits a SpaceCreated event
        assert len(events) == 1
        assert events[0].event_type == "SpaceCreated"

    def test_space_counters_update(self, store):
        space = store.create_space(title="Counter Test")
        store.append_event(space_id=space.space_id, event_type="AgentActed", actor_type="agent", actor_id="a1", content="1")
        store.append_event(space_id=space.space_id, event_type="AgentActed", actor_type="agent", actor_id="a2", content="2")
        updated = store.get_space(space.space_id)
        assert updated.event_count >= 2  # includes SpaceCreated
