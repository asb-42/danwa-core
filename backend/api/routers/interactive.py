"""Interactive Debate Mode API router.

Non-linear, event-sourced debate spaces where human users (via HITL) or A2A
agents drive the conversation dynamically using a [+] button to fork the
debate tree.

Routes:
    POST   /api/v1/interactive/spaces              – Create a new debate space
    GET    /api/v1/interactive/spaces              – List debate spaces
    GET    /api/v1/interactive/spaces/{space_id}   – Get space details
    POST   /api/v1/interactive/spaces/{space_id}/events – Append a new event
    GET    /api/v1/interactive/spaces/{space_id}/events – List events (tree)
    GET    /api/v1/interactive/spaces/{space_id}/thread/{event_id} – Get thread
    GET    /api/v1/interactive/spaces/{space_id}/stream – SSE event stream
    POST   /api/v1/interactive/spaces/{space_id}/synthesize – Generate output
    POST   /api/v1/interactive/spaces/{space_id}/trigger/agent – Trigger agent
    POST   /api/v1/interactive/spaces/{space_id}/trigger/a2a – Trigger A2A
    POST   /api/v1/interactive/spaces/{space_id}/trigger/hitl – Trigger HITL
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, HTTPException, Query
from sse_starlette.sse import EventSourceResponse

from backend.persistence.event_store import EventStore
from backend.schemas.debate_event import (
    DebateEventCreate,
    DebateEventResponse,
    EventStreamMessage,
    SynthesisRequest,
)
from backend.schemas.debate_space import DebateSpaceCreate, DebateSpaceResponse
from backend.services.interactive.context_synthesizer import ContextSynthesizer
from backend.services.interactive.event_bus import get_event_bus
from backend.services.interactive.event_embeddings import EventEmbeddingStore
from backend.services.interactive.event_sync import EventSyncService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Interactive Mode"])

# Singleton EventStore (in production, inject via dependency)
_store: EventStore | None = None


def _get_store() -> EventStore:
    global _store
    if _store is None:
        _store = EventStore()
    return _store


# ── Space Endpoints ──────────────────────────────────────────────────────


@router.post("/interactive/spaces", response_model=DebateSpaceResponse)
def create_space(body: DebateSpaceCreate):
    """Create a new interactive debate space."""
    store = _get_store()
    space = store.create_space(
        title=body.title,
        description=body.description,
        project_id=body.project_id,
        tenant_id=body.tenant_id,
    )
    return space


@router.get("/interactive/spaces", response_model=list[DebateSpaceResponse])
def list_spaces(
    tenant_id: str | None = Query(None),
    project_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List debate spaces with optional filters."""
    store = _get_store()
    return store.list_spaces(
        tenant_id=tenant_id,
        project_id=project_id,
        limit=limit,
        offset=offset,
    )


@router.get("/interactive/spaces/{space_id}", response_model=DebateSpaceResponse)
def get_space(space_id: str):
    """Get details of a single debate space."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")
    return space


# ── Event Endpoints ──────────────────────────────────────────────────────


@router.post("/interactive/spaces/{space_id}/events", response_model=DebateEventResponse)
async def append_event(space_id: str, body: DebateEventCreate):
    """Append a new event to the debate tree (user message, agent speech, etc.)."""
    store = _get_store()

    # Verify space exists
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    # Validate parent exists if provided
    if body.parent_id:
        parent = store.get_event(body.parent_id)
        if not parent or parent.space_id != space_id:
            raise HTTPException(status_code=400, detail="Invalid parent_id")

    event = store.append_event(
        space_id=space_id,
        event_type=body.event_type,
        actor_type=body.actor_type,
        actor_id=body.actor_id,
        content=body.content,
        parent_id=body.parent_id,
        role=body.role,
        metadata_json=body.metadata_json,
    )

    # Publish to event bus for SSE streaming (fire-and-forget)
    bus = get_event_bus()
    stream_name = f"interactive:space:{space_id}"
    asyncio.create_task(bus.publish(stream_name, {"event_id": event.event_id}))

    # Embed event for semantic search (best-effort)
    try:
        embedding_store = EventEmbeddingStore()
        sync = EventSyncService(store, embedding_store)
        sync.sync_event(event)
    except Exception as e:
        logger.warning("Failed to embed event %s: %s", event.event_id, e)

    return event


@router.get("/interactive/spaces/{space_id}/events", response_model=list[DebateEventResponse])
def list_events(
    space_id: str,
    parent_id: str | None = Query(None, description="Filter by parent (None=root)"),
    event_type: str | None = Query(None),
    limit: int = Query(100, ge=1, le=1000),
):
    """List events in a space. Use parent_id=None for root events."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    if event_type:
        return store.get_events_by_type(space_id, event_type, limit=limit)
    return store.get_children(space_id, parent_id=parent_id)


@router.get("/interactive/spaces/{space_id}/thread/{event_id}", response_model=list[DebateEventResponse])
def get_thread(
    space_id: str,
    event_id: str,
    max_depth: int | None = Query(None, ge=0, le=50),
):
    """Get the full thread starting from event_id (BFS traversal)."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    root_event = store.get_event(event_id)
    if not root_event or root_event.space_id != space_id:
        raise HTTPException(status_code=404, detail="Event not found in this space")

    return store.get_thread(space_id, event_id, max_depth=max_depth)


@router.get("/interactive/spaces/{space_id}/tree", response_model=list[DebateEventResponse])
def get_full_tree(space_id: str):
    """Get the complete event tree for a space (for SvelteFlow rendering)."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")
    return store.get_full_tree(space_id)


@router.get("/interactive/spaces/{space_id}/tokens")
def get_token_usage(space_id: str):
    """Get aggregated token usage for a space."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")
    return store.get_token_usage(space_id)


# ── Context Synthesizer ──────────────────────────────────────────────────


@router.post("/interactive/spaces/{space_id}/context/{event_id}")
def synthesize_context(
    space_id: str,
    event_id: str,
    include_side_branches: bool = Query(True),
    agent_role: str | None = Query(None, description="Agent role for context tuning"),
):
    """Synthesize the context window for a new agent event.

    This is the core endpoint that builds the prompt context by:
    1. Tracing the parent_id chain (direct thread)
    2. Querying ChromaDB for semantically relevant side branches
    3. Rendering a structured prompt section

    Use this endpoint when the user clicks [+] to start a new agent action.
    """
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    target_event = store.get_event(event_id)
    if not target_event or target_event.space_id != space_id:
        raise HTTPException(status_code=404, detail="Event not found in this space")

    embedding_store = EventEmbeddingStore()
    synthesizer = ContextSynthesizer(store, embedding_store)

    window = synthesizer.synthesize(
        space_id=space_id,
        target_event_id=event_id,
        agent_bundle={"role": agent_role} if agent_role else None,
        include_side_branches=include_side_branches,
    )

    return {
        "target_event_id": event_id,
        "prompt_context": window.to_prompt_context(),
        "metadata": window.to_metadata(),
    }


# ── SSE Stream ───────────────────────────────────────────────────────────


async def _event_generator(
    space_id: str,
    store: EventStore,
    last_event_id: str | None = None,
) -> AsyncGenerator[dict, None]:
    """Yield new events as SSE messages via Redis Streams."""
    bus = get_event_bus()
    stream_name = f"interactive:space:{space_id}"

    # Replay existing events from DB if needed
    if last_event_id:
        all_events = store.get_full_tree(space_id)
        for evt in all_events:
            if evt.event_id == last_event_id:
                break
            yield {
                "event": "message",
                "data": EventStreamMessage(
                    kind="event",
                    payload=DebateEventResponse.model_validate(evt),
                ).model_dump_json(),
            }

    # Stream new events from Redis
    async for event_data in bus.subscribe(stream_name, last_event_id):
        # Convert stream data to response
        if "event_id" in event_data:
            evt = store.get_event(event_data["event_id"])
            if evt:
                yield {
                    "event": "message",
                    "data": EventStreamMessage(
                        kind="event",
                        payload=DebateEventResponse.model_validate(evt),
                    ).model_dump_json(),
                }


@router.get("/interactive/spaces/{space_id}/stream")
async def stream_events(
    space_id: str,
    last_event_id: str | None = Query(None, description="Resume from this event ID"),
):
    """SSE endpoint for real-time event streaming to the frontend."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    return EventSourceResponse(
        _event_generator(space_id, store, last_event_id),
        media_type="text/event-stream",
    )


# ── Synthesis (final output generation) ─────────────────────────────────


@router.post("/interactive/spaces/{space_id}/synthesize")
def synthesize_output(space_id: str, body: SynthesisRequest):
    """Trigger synthesis of a final deliverable (Markdown, LaTeX, PDF, JSON).

    This is a placeholder that returns the full tree. The actual synthesis
    pipeline (LLM compression, template rendering) will be implemented in
    a dedicated service.
    """
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    events = store.get_full_tree(space_id)

    # Placeholder: return raw tree as JSON
    # TODO: Implement actual synthesis pipeline
    return {
        "space_id": space_id,
        "format": body.format,
        "event_count": len(events),
        "events": [DebateEventResponse.model_validate(e) for e in events],
        "message": "Synthesis pipeline not yet implemented. Returning raw events.",
    }


# ── Worker Triggers ──────────────────────────────────────────────────────


@router.post("/interactive/spaces/{space_id}/trigger/agent", response_model=DebateEventResponse)
async def trigger_agent(
    space_id: str,
    parent_event_id: str = Query(...),
    role: str = Query("assistant"),
    llm_profile_id: str | None = Query(None),
    message: str = Query(...),
):
    """Manually trigger an agent response (for [+] button).

    Creates an agent_speech event that will be processed by the AgentWorker.
    """
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    parent = store.get_event(parent_event_id)
    if not parent or parent.space_id != space_id:
        raise HTTPException(status_code=400, detail="Invalid parent_event_id")

    from backend.services.interactive.workers import WorkerManager

    bus = get_event_bus()
    manager = WorkerManager(store, bus)

    event = await manager.trigger_agent(
        space_id=space_id,
        parent_event_id=parent_event_id,
        agent_config={
            "role": role,
            "llm_profile_id": llm_profile_id,
            "actor_id": f"agent-{role}",
        },
        user_message=message,
    )
    return event


@router.post("/interactive/spaces/{space_id}/trigger/a2a", response_model=DebateEventResponse)
async def trigger_a2a(
    space_id: str,
    parent_event_id: str = Query(...),
    agent_url: str = Query(...),
    message: str = Query(...),
):
    """Manually trigger an A2A request to an external agent."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    parent = store.get_event(parent_event_id)
    if not parent or parent.space_id != space_id:
        raise HTTPException(status_code=400, detail="Invalid parent_event_id")

    from backend.services.interactive.workers import WorkerManager

    bus = get_event_bus()
    manager = WorkerManager(store, bus)

    event = await manager.trigger_a2a(
        space_id=space_id,
        parent_event_id=parent_event_id,
        agent_url=agent_url,
        message=message,
    )
    return event


@router.post("/interactive/spaces/{space_id}/trigger/hitl", response_model=DebateEventResponse)
async def trigger_hitl(
    space_id: str,
    parent_event_id: str = Query(...),
    query: str = Query(...),
):
    """Manually trigger a HITL request (ask the user a question)."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    parent = store.get_event(parent_event_id)
    if not parent or parent.space_id != space_id:
        raise HTTPException(status_code=400, detail="Invalid parent_event_id")

    from backend.services.interactive.workers import WorkerManager

    bus = get_event_bus()
    manager = WorkerManager(store, bus)

    event = await manager.trigger_hitl(
        space_id=space_id,
        parent_event_id=parent_event_id,
        query=query,
    )
    return event
