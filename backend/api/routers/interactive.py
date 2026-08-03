"""Interactive Debate Mode API router.

Non-linear, event-sourced debate spaces where human users (via HITL) or A2A
agents drive the conversation dynamically using a [+] button to fork the
debate tree.

Thin Event Taxonomy (ADR-001):
    All event types use the same envelope. The ``metadata`` field carries
    the rich payload. The API integrates with CQRS projectors to serve
    read models to the frontend.

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
    GET    /api/v1/interactive/spaces/{space_id}/tree-graph – Tree graph (read model)
    GET    /api/v1/interactive/spaces/{space_id}/debate-state – Debate state (read model)
    GET    /api/v1/interactive/spaces/{space_id}/budget – Token budget (read model)
    GET    /api/v1/interactive/spaces/{space_id}/reports – Synthesis reports (read model)
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
from backend.services.interactive.projectors import ProjectorManager

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Interactive Mode"])

# Singletons: EventStore, ProjectorManager, WorkerManager.
# In production these would be injected via FastAPI dependencies, but the
# current codebase uses module-level singletons (see backend/main.py lifespan).
_store: EventStore | None = None
_projector_manager: ProjectorManager | None = None
_worker_manager = None  # WorkerManager — lazily set by main.py lifespan
_embedding_store: EventEmbeddingStore | None = None


def _get_embedding_store() -> EventEmbeddingStore:
    """Return the process-cached EventEmbeddingStore singleton.

    ChromaDB client + collection init is heavyweight (10-50ms). Creating
    a new instance on every event append was a significant performance
    bottleneck. This singleton reuses the same client/collection.
    """
    global _embedding_store
    if _embedding_store is None:
        _embedding_store = EventEmbeddingStore()
    return _embedding_store

# Track which spaces the worker manager is already listening to, so we only
# start a listener once per space.
_started_spaces: set[str] = set()


def _get_store() -> EventStore:
    global _store, _projector_manager
    if _store is None:
        _store = EventStore()
        # Initialize projector manager with the same DB connection
        _projector_manager = ProjectorManager(_store.conn)
        _store.set_projector_manager(_projector_manager)
    return _store


def _get_projector_manager() -> ProjectorManager:
    global _projector_manager
    if _projector_manager is None:
        _get_store()  # This initializes both
    return _projector_manager  # type: ignore[return-value]


def set_worker_manager(manager) -> None:
    """Inject the WorkerManager created by the application lifespan.

    Called from ``backend/main.py`` so the router endpoints share the same
    started instance instead of creating throwaway copies.
    """
    global _worker_manager
    _worker_manager = manager


def _get_worker_manager():
    """Return the shared WorkerManager, creating one if the lifespan didn't.

    The manager is started for a given space on first use (see
    ``_ensure_space_listening``). This keeps the trigger endpoints working
    even when the lifespan startup is skipped (e.g. in tests).
    """
    global _worker_manager
    if _worker_manager is None:
        from backend.services.interactive.workers import WorkerManager

        _worker_manager = WorkerManager(_get_store())
    return _worker_manager


def _ensure_space_listening(space_id: str) -> None:
    """Start a worker listener for a space if not already running.

    Workers only process events they are subscribed to. Without this call,
    appended AgentActed/A2AActed/UserActed trigger events would never be
    picked up and no LLM/external response would be generated.
    """
    manager = _get_worker_manager()
    if space_id not in _started_spaces:
        import asyncio

        try:
            # start() is async; schedule it on the running loop if present.
            loop = asyncio.get_running_loop()
            loop.create_task(manager.start([space_id]))
        except RuntimeError:
            # No running loop (e.g. sync test context) — start synchronously.
            import asyncio

            asyncio.run(manager.start([space_id]))
        _started_spaces.add(space_id)


# ── Space Endpoints ──────────────────────────────────────────────────────


@router.post("/interactive/spaces", response_model=DebateSpaceResponse)
def create_space(body: DebateSpaceCreate):
    """Create a new interactive debate space.

    After creation, starts a worker listener for this space so that
    subsequent trigger events (AgentActed, A2AActed, HITL) are processed.
    """
    store = _get_store()
    space = store.create_space(
        title=body.title,
        description=body.description,
        case_id=body.case_id,
        tenant_id=body.tenant_id,
    )
    # Start workers for this new space (idempotent).
    _ensure_space_listening(space.space_id)
    return space


@router.get("/interactive/spaces", response_model=list[DebateSpaceResponse])
def list_spaces(
    tenant_id: str | None = Query(None),
    case_id: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List debate spaces with optional filters."""
    store = _get_store()
    return store.list_spaces(
        tenant_id=tenant_id,
        case_id=case_id,
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

    # Publish to event bus for SSE streaming (fire-and-forget with error logging)
    bus = get_event_bus()
    stream_name = f"interactive:space:{space_id}"
    _publish_task = asyncio.create_task(bus.publish(stream_name, {"event_id": event.event_id}))
    _publish_task.add_done_callback(
        lambda t: t.exception() and logger.error("Event bus publish failed for %s: %s", event.event_id, t.exception())
    )

    # Embed event for semantic search (best-effort, singleton store)
    try:
        embedding_store = _get_embedding_store()
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

    embedding_store = _get_embedding_store()
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
    """Yield events as SSE messages, replaying missed events then streaming live.

    When ``last_event_id`` is provided, the client already has every event up to
    and including that one. We therefore replay only the events that were
    appended **after** it (skip until we pass the cursor, then yield the rest),
    before subscribing to the live stream for new events.
    """
    bus = get_event_bus()
    stream_name = f"interactive:space:{space_id}"

    def _format(evt) -> dict:
        return {
            "event": "message",
            "data": EventStreamMessage(
                kind="event",
                payload=DebateEventResponse.model_validate(evt),
            ).model_dump_json(),
        }

    # Replay existing events from DB — only those AFTER last_event_id.
    if last_event_id:
        all_events = store.get_full_tree(space_id)
        passed_cursor = False
        for evt in all_events:
            if not passed_cursor:
                if evt.event_id == last_event_id:
                    passed_cursor = True
                continue  # skip events at or before the cursor
            yield _format(evt)
    else:
        # No cursor — replay the full tree so a fresh client sees history.
        for evt in store.get_full_tree(space_id):
            yield _format(evt)

    # Stream new events from the event bus (live).
    async for event_data in bus.subscribe(stream_name, last_event_id):
        if "event_id" in event_data:
            evt = store.get_event(event_data["event_id"])
            if evt:
                yield _format(evt)


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
async def synthesize_output(
    space_id: str,
    body: SynthesisRequest,
    llm_profile_id: str | None = Query(None, description="LLM profile for compression"),
    use_llm: bool = Query(True, description="If False, return a raw transcript without LLM compression"),
):
    """Trigger synthesis of a final deliverable (Markdown, LaTeX, PDF, JSON).

    Produces a structured document from the debate event tree:

    - **json**     — deterministic structured event tree (no LLM call)
    - **markdown** — clean narrative report (LLM-compressed unless ``use_llm=False``)
    - **latex**    — self-contained LaTeX document source (LLM-generated)
    - **pdf**      — LaTeX source intended for PDF rendering (LLM-generated)

    The result is persisted in the ``synthesis_reports`` table (read model)
    and a ``ContextSynthesized`` event is appended to the event log so the
    SSE stream and CQRS projectors update.
    """
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    from backend.services.interactive.synthesis_service import SynthesisService

    service = SynthesisService(store)
    try:
        result = await service.synthesize(
            space_id=space_id,
            fmt=body.format,
            max_depth=body.max_depth,
            include_side_branches=body.include_side_branches,
            llm_profile_id=llm_profile_id,
            use_llm=use_llm,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    return result.to_dict()


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

    Creates an ``AgentActed`` event that will be processed by the
    ``AgentWorker`` (which calls the LLM and appends the response).
    """
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    parent = store.get_event(parent_event_id)
    if not parent or parent.space_id != space_id:
        raise HTTPException(status_code=400, detail="Invalid parent_event_id")

    # Use the shared, started WorkerManager — not a throwaway copy.
    _ensure_space_listening(space_id)
    manager = _get_worker_manager()

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

    # Use the shared, started WorkerManager — not a throwaway copy.
    _ensure_space_listening(space_id)
    manager = _get_worker_manager()

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

    # Use the shared, started WorkerManager — not a throwaway copy.
    _ensure_space_listening(space_id)
    manager = _get_worker_manager()

    event = await manager.trigger_hitl(
        space_id=space_id,
        parent_event_id=parent_event_id,
        query=query,
    )
    return event


# ── CQRS Read Model Endpoints (ADR-001) ─────────────────────────────────


@router.get("/interactive/spaces/{space_id}/tree-graph")
def get_tree_graph(space_id: str):
    """Get the lightweight tree graph for SvelteFlow (read model from TreeProjector)."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    manager = _get_projector_manager()
    return manager.get_tree_projector().get_tree_graph(space_id)


@router.get("/interactive/spaces/{space_id}/debate-state")
def get_debate_state(
    space_id: str,
    fact_type: str | None = Query(None, description="Filter: claim, critique, evidence, question"),
    limit: int = Query(50, ge=1, le=200),
):
    """Get structured facts from the debate state (read model from ContextProjector)."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    manager = _get_projector_manager()
    return manager.get_context_projector().get_facts(space_id, fact_type=fact_type, limit=limit)


@router.get("/interactive/spaces/{space_id}/budget")
def get_budget(space_id: str):
    """Get token budget per actor (read model from BudgetProjector)."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    manager = _get_projector_manager()
    projector = manager.get_budget_projector()
    return {
        "budgets": projector.get_budget(space_id),
        "totals": projector.get_total_cost(space_id),
    }


@router.get("/interactive/spaces/{space_id}/reports")
def get_reports(space_id: str):
    """Get synthesis reports (read model from SynthesisProjector)."""
    store = _get_store()
    space = store.get_space(space_id)
    if not space:
        raise HTTPException(status_code=404, detail="Debate space not found")

    manager = _get_projector_manager()
    return manager.get_synthesis_projector().get_reports(space_id)
