# Danwa Interactive — Implementation Report

**Date:** 2026-07-09  
**Branch:** `contect-builder`  
**Author:** Danwa Dev  

---

## Executive Summary

This report documents the implementation of **Danwa Interactive**, a new non-linear, event-sourced debate mode for the Danwa platform. Unlike the existing linear LangGraph-based workflows, Interactive Mode enables dynamic, forkable debate trees driven by human users (HITL), AI agents, and external A2A agents.

The feature spans all four Danwa repositories and introduces a fundamentally new architectural paradigm: **Event Sourcing** — where nothing is mutated, only facts (events) are appended. This makes time-travel, forking, and parallel "listening" trivial.

---

## Architecture Overview

### Core Principle: Event Sourcing

```
Traditional (LangGraph)          Interactive (Event Sourcing)
┌─────────────────────┐          ┌─────────────────────┐
│ State Mutation      │          │ Append-Only Events   │
│ state = update(state)│         │ events.append(event) │
│ Linear flow         │          │ DAG (tree)           │
│ Single path         │          │ Multiple forks       │
└─────────────────────┘          └─────────────────────┘
```

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        Frontend (Svelte 5)                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ SvelteFlow   │  │ ForkModal    │  │ SSE Event Listener   │  │
│  │ (Tree Graph) │  │ ([+] Button) │  │ (Real-time Updates)  │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     API Layer (FastAPI)                           │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │  /api/v1/interactive/spaces/{id}/events                    │ │
│  │  /api/v1/interactive/spaces/{id}/stream (SSE)              │ │
│  │  /api/v1/interactive/spaces/{id}/context/{event_id}        │ │
│  │  /api/v1/interactive/spaces/{id}/trigger/{agent|a2a|hitl}  │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                    Core Services                                  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ EventStore   │  │ EventBus     │  │ ContextSynthesizer   │  │
│  │ (SQLite)     │  │ (Redis)      │  │ (ChromaDB)           │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ AgentWorker  │  │ A2AWorker    │  │ HITLWorker           │  │
│  │ (LLM calls)  │  │ (ext. agents)│  │ (human input)        │  │
│  └──────────────┘  └──────────────┘  └──────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

---

## Repository Changes

### 1. danwa-core (Backend)

**Branch:** `contect-builder`  
**Commit:** `f6c2a99`  
**Files:** 19 created, 2307 lines added  

#### New Files

| File | Description |
|------|-------------|
| `backend/models/debate_space.py` | Pydantic model for DebateSpace (Aggregate Root) |
| `backend/models/debate_event.py` | Pydantic model for DebateEvent (append-only) |
| `backend/schemas/__init__.py` | Package init with exports |
| `backend/schemas/common.py` | Shared Pydantic mixins |
| `backend/schemas/debate_space.py` | Request/Response DTOs for spaces |
| `backend/schemas/debate_event.py` | Request/Response DTOs + SSE envelope |
| `backend/persistence/event_store.py` | SQLite store with thread traversal (BFS) |
| `backend/migrations/v025_interactive_debate.py` | Migration for new tables |
| `backend/api/routers/interactive.py` | FastAPI router (13 endpoints) |
| `backend/services/interactive/__init__.py` | Package init |
| `backend/services/interactive/event_embeddings.py` | ChromaDB store for event content |
| `backend/services/interactive/context_synthesizer.py` | **Core engine** — builds context windows |
| `backend/services/interactive/event_sync.py` | Syncs EventStore ↔ ChromaDB |
| `backend/services/interactive/event_bus.py` | Redis Streams + In-Memory fallback |
| `backend/services/interactive/workers/__init__.py` | Package init |
| `backend/services/interactive/workers/agent_worker.py` | LLM calls for agent_speech |
| `backend/services/interactive/workers/a2a_worker.py` | External A2A agent calls |
| `backend/services/interactive/workers/hitl_worker.py` | Human-in-the-loop interactions |
| `backend/services/interactive/workers/manager.py` | Worker orchestration |

#### Modified Files

| File | Change |
|------|--------|
| `backend/main.py` | Added interactive router import and registration |
| `backend/models/__init__.py` | Added DebateEvent, DebateSpace exports |

#### API Endpoints

```
POST   /api/v1/interactive/spaces                          # Create space
GET    /api/v1/interactive/spaces                          # List spaces
GET    /api/v1/interactive/spaces/{space_id}               # Get space
POST   /api/v1/interactive/spaces/{space_id}/events        # Append event
GET    /api/v1/interactive/spaces/{space_id}/events        # List events
GET    /api/v1/interactive/spaces/{space_id}/thread/{id}   # Get thread
GET    /api/v1/interactive/spaces/{space_id}/tree          # Full tree
GET    /api/v1/interactive/spaces/{space_id}/tokens        # Token usage
POST   /api/v1/interactive/spaces/{space_id}/context/{id}  # Synthesize context
GET    /api/v1/interactive/spaces/{space_id}/stream        # SSE stream
POST   /api/v1/interactive/spaces/{space_id}/synthesize    # Generate output
POST   /api/v1/interactive/spaces/{space_id}/trigger/agent # Trigger agent
POST   /api/v1/interactive/spaces/{space_id}/trigger/a2a   # Trigger A2A
POST   /api/v1/interactive/spaces/{space_id}/trigger/hitl  # Trigger HITL
```

#### Database Schema

```sql
-- DebateSpaces: root aggregate
CREATE TABLE debate_spaces (
    space_id    TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT,
    project_id  TEXT,
    tenant_id   TEXT,
    created_by  TEXT,
    status      TEXT NOT NULL DEFAULT 'open',
    event_count INTEGER NOT NULL DEFAULT 0,
    fork_count  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- DebateEvents: append-only event log
CREATE TABLE debate_events (
    event_id      TEXT PRIMARY KEY,
    space_id      TEXT NOT NULL REFERENCES debate_spaces(space_id),
    parent_id     TEXT REFERENCES debate_events(event_id),
    event_type    TEXT NOT NULL,
    actor_type    TEXT NOT NULL,
    actor_id      TEXT NOT NULL,
    role          TEXT,
    content       TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    tokens_input  INTEGER,
    tokens_output INTEGER,
    created_at    TEXT NOT NULL
);

-- Indexes for performance
CREATE INDEX ix_events_space_parent ON debate_events(space_id, parent_id);
CREATE INDEX ix_events_space_created ON debate_events(space_id, created_at);
CREATE INDEX ix_events_type ON debate_events(event_type);
```

#### Context Synthesizer

The Context Synthesizer is the heart of the interactive engine. When a user clicks `[+]` on an event, it:

1. **Traces the parent chain** → builds the direct conversational thread
2. **Queries ChromaDB** → finds semantically relevant side-branches
3. **Applies token budget** → prevents context explosion
4. **Renders prompt context** → structured section for the agent

```python
# Usage example
synth = ContextSynthesizer(event_store, embedding_store)
window = synth.synthesize(
    space_id="...",
    target_event_id="...",
    agent_bundle={"role": "strategist"},
)
prompt_context = window.to_prompt_context()
```

**Output format:**
```markdown
## Direkter Diskussionsverlauf

**user-1**: Ist Künstliche Intelligenz eine Bedrohung?

**claude-strategist (strategist)**: KI bietet enorme Chancen...

**gpt-critic (critic)**: Aber die Arbeitsplatzverlagerung ist real...

## Relevante Nebenzweige

**claude-optimist** (Relevanz: 0.53): Historisch gesehen...
```

#### Event Bus (Redis Streams)

```python
# Publish
bus = get_event_bus()
await bus.publish("interactive:space:123", {"event_id": "evt-456"})

# Subscribe
async for event in bus.subscribe("interactive:space:123"):
    process(event)
```

Features:
- **Durable**: Redis Streams stores events persistently
- **Replay**: Clients can replay from a specific event ID
- **Multi-Client**: Every SSE client receives all events
- **Auto-Trim**: Max 10,000 events per space (configurable)
- **Fallback**: In-Memory when Redis is not available

#### Workers

| Worker | Event Type | Function |
|--------|------------|----------|
| **AgentWorker** | `agent_speech` | Synthesizes context, calls LLM, appends response |
| **A2AWorker** | `a2a_request` | Sends requests to external A2A agents via JSON-RPC |
| **HITLWorker** | `hitl_input` | Publishes queries to user via SSE, waits for response |
| **WorkerManager** | all | Orchestrates event dispatch, dynamic space discovery |

---

### 2. danwa (Frontend)

**Branch:** `contect-builder`  
**Commit:** `fc66644`  
**Files:** 8 created, 1322 lines added  

#### New Files

| File | Description |
|------|-------------|
| `frontend/src/lib/interactive/api.ts` | API client for all interactive endpoints |
| `frontend/src/lib/interactive/stores.ts` | Svelte 5 stores for state management |
| `frontend/src/components/interactive/DebateEventNode.svelte` | SvelteFlow node for events |
| `frontend/src/components/interactive/DebateGraph.svelte` | Main graph component |
| `frontend/src/components/interactive/ForkModal.svelte` | Modal for [+] fork actions |
| `frontend/src/views/InteractiveDebateView.svelte` | Main view |

#### Modified Files

| File | Change |
|------|--------|
| `frontend/src/App.svelte` | Added route for interactive mode |
| `frontend/src/components/Sidebar.svelte` | Added navigation entry |

#### Features

| Feature | Description |
|---------|-------------|
| 🌳 **Live Debate Graph** | SvelteFlow-based tree with real-time updates |
| [+] **Fork Button** | Hover over node → [+] button appears |
| 🔄 **SSE Streaming** | Real-time updates via Redis Streams |
| 🎭 **Actor-specific Colors** | User (green), Agent (purple), A2A (orange) |
| 📊 **MiniMap** | Navigation in debate tree |
| 🎯 **Context Synthesis** | Agent gets relevant context |

#### Navigation

- Route: `#/interactive`
- Sidebar: 🌳 Interactive (under "Work")

#### Build Output

```
✓ built in 30.32s
dist/assets/InteractiveDebateView-BX2R1sTe.js  195.15 kB │ gzip: 63.00 kB
```

---

### 3. danwa-studio (Admin)

**Branch:** `contect-builder`  
**Commit:** `58f2077`  
**Files:** 3 created, 228 lines added  

#### New Files

| File | Description |
|------|-------------|
| `src/views/ActionTemplatesView.svelte` | Action Template Manager UI |

#### Modified Files

| File | Change |
|------|--------|
| `src/App.svelte` | Added route and import |
| `src/components/Sidebar.svelte` | Added navigation entry |

#### Features

- View available action templates
- Enable/disable actions
- Customize roles and prompts
- Map actions to agent bundles

#### Navigation

- Route: `#/action-templates`
- Sidebar: 🌳 Action Templates (under "CONFIGURE")

---

### 4. danwa-modules (Config)

**Branch:** `contect-builder`  
**Commit:** `30359b9`  
**Files:** 6 created, 310 lines added  

#### New Files

| File | Description |
|------|-------------|
| `schemas/action-template.json` | JSON Schema for action templates |
| `interactive-action-templates/default-actions/manifest.json` | Module manifest |
| `interactive-action-templates/default-actions/templates.json` | 8 default actions |
| `synthesizer-patterns/markdown/manifest.json` | Synthesizer manifest |
| `synthesizer-patterns/markdown/pattern.json` | Markdown export template |

#### Modified Files

| File | Change |
|------|--------|
| `schemas/module-manifest.json` | Added `action-template` and `synthesizer-pattern types` |

#### Default Action Templates

| ID | Type | Label | Icon |
|----|------|-------|------|
| `agent-strategist` | agent | Strategist Analysis | 🧠 |
| `agent-critic` | agent | Critical Review | 🔍 |
| `agent-optimist` | agent | Positive Perspective | 🌱 |
| `agent-devils-advocate` | agent | Devil's Advocate | 😈 |
| `agent-mediator` | agent | Mediator Synthesis | 🤝 |
| `agent-creative` | agent | Creative Brainstorm | 💡 |
| `hitl-question` | hitl | Ask User | 👤 |
| `a2a-external` | a2a | External A2A Agent | 🔗 |

---

## Event Types

| Event Type | Actor | Description |
|------------|-------|-------------|
| `user_message` | user | Human user sends a message |
| `agent_speech` | agent | AI agent responds |
| `tool_call_requested` | system | Tool call requested |
| `tool_result` | system | Tool call result |
| `a2a_request` | a2a | Request to external agent |
| `a2a_response` | a2a | Response from external agent |
| `hitl_input` | user/system | Human-in-the-loop input |
| `synthesis` | system | Final output synthesis |

---

## Technical Decisions

### 1. Event Sourcing over LangGraph

**Decision:** Use append-only event log instead of state mutation.

**Rationale:**
- LangGraph assumes linear, predefined workflows
- Interactive Mode requires dynamic forking (DAG)
- Event Sourcing makes time-trivial trivial
- No state corruption possible (append-only)

### 2. SQLite for Event Store

**Decision:** Use SQLite with raw SQL (not SQLAlchemy ORM).

**Rationale:**
- Matches existing codebase patterns
- Append-only table is ideal for SQLite
- WAL mode for concurrent reads
- No ORM overhead for simple schema

### 3. Redis Streams for Event Bus

**Decision:** Use Redis Streams (not Pub/Sub) for event distribution.

**Rationale:**
- Durable (survives restarts)
- Consumer groups for multi-client
- Replay from specific offset
- Auto-trimming for storage management

### 4. ChromaDB for Semantic Search

**Decision:** Embed event content for semantic context retrieval.

**Rationale:**
- Prevents token explosion from full tree traversal
- Finds relevant side-branches automatically
- cosine similarity for ranking
- Already used in DMS module

### 5. SvelteFlow for Tree Visualization

**Decision:** Use SvelteFlow (not Cytoscape) for debate graph.

**Rationale:**
- Already in package.json dependencies
- Better Svelte 5 integration
- Built-in node/edge handling
- MiniMap and Controls included

---

## Testing

### Unit Tests

```bash
# EventStore
cd /media/data/coding/danwa-core
uv run python -c "
from backend.persistence.event_store import EventStore
import tempfile, os
db = tempfile.mktemp(suffix='.db')
store = EventStore(db)
space = store.create_space(title='Test')
e1 = store.append_event(space_id=space.space_id, event_type='user_message', ...)
e2 = store.append_event(space_id=space.space_id, event_type='agent_speech', parent_id=e1.event_id, ...)
e3 = store.append_event(space_id=space.space_id, event_type='agent_speech', parent_id=e1.event_id, ...)  # Fork!
tree = store.get_full_tree(space.space_id)
assert len(tree) == 3
store.close()
os.unlink(db)
"
```

### API Tests

```bash
# FastAPI endpoints
cd /media/data/coding/danwa-core
uv run python -c "
from fastapi import FastAPI
from fastapi.testclient import TestClient
from backend.api.routers.interactive import router
app = FastAPI()
app.include_router(router)
client = TestClient(app)
resp = client.post('/interactive/spaces', json={'title': 'Test'})
assert resp.status_code == 200
space_id = resp.json()['space_id']
resp = client.post(f'/interactive/spaces/{space_id}/events', json={...})
assert resp.status_code == 200
"
```

### Context Synthesizer Tests

```bash
# Context building
cd /media/data/coding/danwa-core
uv run python -c "
from backend.services.interactive.context_synthesizer import ContextSynthesizer
# ... test context synthesis with mock events
window = synth.synthesize(space_id, target_event_id)
assert 'Direkter Diskussionsverlauf' in window.to_prompt_context()
"
```

### Frontend Build

```bash
cd /media/data/coding/danwa/frontend
npm run build  # ✓ built in 30.32s
```

### Studio Build

```bash
cd /media/data/coding/danwa-studio
npm run build  # ✓ built in 9.58s
```

---

## Configuration

### Environment Variables

```bash
# Redis (optional, falls back to in-memory)
DANWA_REDIS_URL=redis://localhost:6379/0

# Database (auto-created)
# data/interactive.db — Event Store
# data/interactive_embeddings/ — ChromaDB vectors
```

### Dependencies (already present)

- `fastapi` — API framework
- `sse-starlette` — SSE support
- `chromadb` — Vector store
- `redis` — Event bus (optional)
- `@xyflow/svelte` — SvelteFlow (frontend)

---

## Migration

The migration `v025_interactive_debate` creates the required tables:

```bash
# Auto-run on startup
python -m backend.migrations.v025_interactive_debate data/interactive.db
```

---

## Future Work

### Phase 2 (Planned)

| Task | Priority | Description |
|------|----------|-------------|
| Redis Streams workers | High | Background workers for event processing |
| A2A protocol compliance | High | Full JSON-RPC 2.0 implementation |
| HITL timeout handling | Medium | Auto-expire unanswered queries |
| Synthesis pipeline | Medium | LLM-based compression to Markdown/LaTeX |
| Token budget enforcement | Medium | Hard limits on context window |
| Event replay UI | Low | Visual time-travel in SvelteFlow |

### Phase 3 (Planned)

| Task | Priority | Description |
|------|----------|-------------|
| Multi-space collaboration | High | Multiple users in same space |
| Role-based access control | High | Per-space permissions |
| Event versioning | Medium | Schema evolution support |
| Export formats | Medium | PDF, LaTeX, DOCX generation |
| Analytics dashboard | Low | Usage statistics, token costs |

---

## Summary

| Metric | Value |
|--------|-------|
| **Total Files Created** | 36 |
| **Total Lines Added** | ~4,200 |
| **API Endpoints** | 14 |
| **New Services** | 6 |
| **New Workers** | 4 |
| **Frontend Components** | 4 |
| **Database Tables** | 2 |
| **Module Types Added** | 2 |
| **Build Status** | ✅ All passing |

---

*Report generated: 2026-07-09*  
*Branch: contect-builder*  
*Status: Ready for review*
