"""Tests for Interactive Mode API endpoints (CQRS read models)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.persistence.event_store import EventStore
from backend.services.interactive.projectors import ProjectorManager


@pytest.fixture()
def store(tmp_path) -> EventStore:
    """Isolated EventStore with projector integration."""
    return EventStore(db_path=tmp_path / "test_api_interactive.db")


@pytest.fixture()
def space_id(store) -> str:
    """Create a test space and return its ID."""
    s = store.create_space(title="API Test Space")
    return s.space_id


@pytest.fixture()
def populated_space(store) -> str:
    """Create a space with events for testing read models."""
    s = store.create_space(title="Populated Space")
    space_id = s.space_id

    e1 = store.append_event(
        space_id=space_id,
        event_type="AgentActed",
        actor_type="agent",
        actor_id="strategist-1",
        content="We should optimize costs.",
        role="strategist",
        metadata_json={
            "structured_output": {
                "claims": ["Cost optimization needed"],
                "critiques": [],
                "evidence": ["Market analysis"],
            }
        },
        tokens_input=500,
        tokens_output=200,
    )
    e2 = store.append_event(
        space_id=space_id,
        event_type="AgentActed",
        actor_type="agent",
        actor_id="critic-1",
        content="The budget is too high.",
        role="critic",
        parent_id=e1.event_id,
        metadata_json={
            "structured_output": {
                "claims": [],
                "critiques": ["Budget too high"],
                "evidence": [],
            }
        },
        tokens_input=300,
        tokens_output=150,
    )
    store.append_event(
        space_id=space_id,
        event_type="UserActed",
        actor_type="user",
        actor_id="user-1",
        content="Consider sustainability.",
        parent_id=e2.event_id,
    )
    return space_id


@pytest.fixture()
def client(store, app):
    """TestClient with the interactive router's store mocked."""
    import backend.api.routers.interactive as interactive_module

    interactive_module._store = store
    interactive_module._projector_manager = ProjectorManager(store.conn)
    store.set_projector_manager(interactive_module._projector_manager)

    try:
        with TestClient(app) as c:
            yield c
    finally:
        interactive_module._store = None
        interactive_module._projector_manager = None


class TestSpaceCRUD:
    def test_create_space(self, client):
        response = client.post(
            "/interactive/spaces",
            json={"title": "New Space", "description": "Test"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "New Space"
        assert "space_id" in data

    def test_list_spaces(self, client):
        client.post("/interactive/spaces", json={"title": "Space 1"})
        client.post("/interactive/spaces", json={"title": "Space 2"})
        response = client.get("/interactive/spaces")
        assert response.status_code == 200
        assert len(response.json()) >= 2

    def test_get_space(self, client):
        create_resp = client.post("/interactive/spaces", json={"title": "Get Me"})
        space_id = create_resp.json()["space_id"]
        response = client.get(f"/interactive/spaces/{space_id}")
        assert response.status_code == 200
        assert response.json()["title"] == "Get Me"

    def test_get_space_not_found(self, client):
        response = client.get("/interactive/spaces/nonexistent")
        assert response.status_code == 404


class TestEventAppend:
    def test_append_agent_event(self, client):
        space_resp = client.post("/interactive/spaces", json={"title": "Evt Test"})
        space_id = space_resp.json()["space_id"]
        response = client.post(
            f"/interactive/spaces/{space_id}/events",
            json={
                "space_id": space_id,
                "event_type": "AgentActed",
                "actor_type": "agent",
                "actor_id": "agent-1",
                "content": "Test statement",
                "role": "strategist",
                "metadata_json": {
                    "structured_output": {"claims": ["Test claim"]},
                    "tokens_input": 100,
                },
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["event_type"] == "AgentActed"
        assert data["role"] == "strategist"

    def test_append_legacy_type_rejected_by_schema(self, client):
        """Legacy event types must be rejected at the API schema level."""
        space_resp = client.post("/interactive/spaces", json={"title": "Legacy"})
        space_id = space_resp.json()["space_id"]
        response = client.post(
            f"/interactive/spaces/{space_id}/events",
            json={
                "space_id": space_id,
                "event_type": "agent_speech",  # legacy type
                "actor_type": "agent",
                "actor_id": "agent-1",
                "content": "Legacy event",
            },
        )
        # Pydantic validation rejects legacy event types
        assert response.status_code == 422

    def test_append_with_invalid_parent(self, client):
        space_resp = client.post("/interactive/spaces", json={"title": "No Parent"})
        space_id = space_resp.json()["space_id"]
        response = client.post(
            f"/interactive/spaces/{space_id}/events",
            json={
                "space_id": space_id,
                "event_type": "AgentActed",
                "actor_type": "agent",
                "actor_id": "agent-1",
                "content": "Bad parent",
                "parent_id": "nonexistent-parent",
            },
        )
        assert response.status_code == 400

    def test_append_to_nonexistent_space(self, client):
        response = client.post(
            "/interactive/spaces/nonexistent/events",
            json={
                "space_id": "nonexistent",
                "event_type": "AgentActed",
                "actor_type": "agent",
                "actor_id": "agent-1",
                "content": "No space",
            },
        )
        assert response.status_code == 404


class TestEventListAndTree:
    def test_list_events(self, client, populated_space):
        response = client.get(f"/interactive/spaces/{populated_space}/events")
        assert response.status_code == 200
        # Root events: SpaceCreated + e1 (the other events have parents)
        assert len(response.json()) >= 2

    def test_list_events_by_type(self, client, populated_space):
        response = client.get(
            f"/interactive/spaces/{populated_space}/events",
            params={"event_type": "AgentActed"},
        )
        assert response.status_code == 200
        # All AgentActed events, not just root
        assert len(response.json()) == 2

    def test_get_full_tree(self, client, populated_space):
        response = client.get(f"/interactive/spaces/{populated_space}/tree")
        assert response.status_code == 200
        # All events: SpaceCreated + e1 + e2 + e3 = 4
        assert len(response.json()) >= 4

    def test_get_thread(self, client, populated_space):
        events_resp = client.get(f"/interactive/spaces/{populated_space}/events")
        root_event = events_resp.json()[0]["event_id"]
        response = client.get(
            f"/interactive/spaces/{populated_space}/thread/{root_event}",
        )
        assert response.status_code == 200
        assert len(response.json()) >= 1


class TestTreeGraphReadModel:
    def test_tree_graph(self, client, populated_space):
        response = client.get(f"/interactive/spaces/{populated_space}/tree-graph")
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "edges" in data
        assert len(data["nodes"]) >= 3

    def test_tree_graph_empty_space(self, client):
        space_resp = client.post("/interactive/spaces", json={"title": "Empty"})
        space_id = space_resp.json()["space_id"]
        response = client.get(f"/interactive/spaces/{space_id}/tree-graph")
        assert response.status_code == 200
        # Empty space has no nodes (SpaceCreated is not projected by TreeProjector)
        assert response.json()["nodes"] == []


class TestDebateStateReadModel:
    def test_debate_state_claims(self, client, populated_space):
        response = client.get(
            f"/interactive/spaces/{populated_space}/debate-state",
            params={"fact_type": "claim"},
        )
        assert response.status_code == 200
        facts = response.json()
        assert any(f["fact_content"] == "Cost optimization needed" for f in facts)

    def test_debate_state_critiques(self, client, populated_space):
        response = client.get(
            f"/interactive/spaces/{populated_space}/debate-state",
            params={"fact_type": "critique"},
        )
        assert response.status_code == 200
        facts = response.json()
        assert any(f["fact_content"] == "Budget too high" for f in facts)

    def test_debate_state_all_facts(self, client, populated_space):
        response = client.get(f"/interactive/spaces/{populated_space}/debate-state")
        assert response.status_code == 200
        assert len(response.json()) >= 3


class TestBudgetReadModel:
    def test_budget(self, client, populated_space):
        response = client.get(f"/interactive/spaces/{populated_space}/budget")
        assert response.status_code == 200
        data = response.json()
        assert "budgets" in data
        assert "totals" in data
        assert len(data["budgets"]) >= 1

    def test_budget_totals(self, client, populated_space):
        response = client.get(f"/interactive/spaces/{populated_space}/budget")
        totals = response.json()["totals"]
        assert totals["total_input"] >= 800  # 500 + 300
        assert totals["total_output"] >= 350  # 200 + 150


class TestSynthesisReports:
    def test_reports_empty_initially(self, client):
        space_resp = client.post("/interactive/spaces", json={"title": "No Reports"})
        space_id = space_resp.json()["space_id"]
        response = client.get(f"/interactive/spaces/{space_id}/reports")
        assert response.status_code == 200
        assert response.json() == []


class TestTokenUsage:
    def test_token_usage(self, client, populated_space):
        response = client.get(f"/interactive/spaces/{populated_space}/tokens")
        assert response.status_code == 200
        data = response.json()
        assert data["total_input"] >= 800
        assert data["total_output"] >= 350
