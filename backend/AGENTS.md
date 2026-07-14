# DOX: backend/

## Purpose

Python FastAPI backend providing the core application: API layer, business logic, persistence, workflow engine, agent-to-agent protocol, blueprint system, and module management.

## Ownership

- **Entry Point**: `backend/main.py` — FastAPI app factory, lifespan, router registration
- **API Layer**: `backend/api/` — 44 routers, dependency injection, error handling
- **Core Logic**: `backend/core/` — config, security, logging, debate engine, LLM router
- **Services**: `backend/services/` — business logic (LLM, debate, DMS, I/O pipelines, interactive mode)
- **Models**: `backend/models/` — Pydantic data models
- **Schemas**: `backend/schemas/` — request/response schemas
- **Persistence**: `backend/persistence/` — SQLite stores (11 stores)
- **Workflow Engine**: `backend/workflow/` — LangGraph-based execution with HITL
- **A2A Protocol**: `backend/a2a/` — Agent-to-Agent communication
- **Blueprint System**: `backend/blueprints/` — visual workflow builder
- **Module System**: `backend/modules/` — install, validate, resolve modules
- **LLM Catalog**: `backend/llm_catalog/` — LLM model catalog management
- **Tasks**: `backend/tasks/` — Celery async tasks
- **State**: `backend/state/` — pub/sub, workflow state
- **Migrations**: `backend/migrations/` — database schema migrations

## Local Contracts

- All routers must be registered in `backend/main.py`
- Services depend on persistence stores, not direct DB access
- Models define the contract between API and persistence layers
- Workflow nodes are pure functions with typed state

## Work Guidance

- Follow existing patterns when adding new routers or services
- Use dependency injection for service dependencies
- Keep business logic in services, not routers
- Write tests for new services and routers

## Verification

- Run `pytest tests/backend/ -v` before committing
- Ensure type checking passes with `pyright`

## Child DOX Index

| Child | Purpose |
|-------|---------|
| `backend/api/` | HTTP API layer with 44 routers |
| `backend/services/` | Business logic services (LLM, debate, DMS, I/O, interactive mode) |
| `backend/workflow/` | LangGraph-based workflow engine with HITL support |
| `backend/a2a/` | Agent-to-Agent protocol implementation |
| `backend/blueprints/` | Visual blueprint/workflow builder system |
| `backend/models/` | Pydantic data models |
| `backend/persistence/` | SQLite data stores |
| `backend/modules/` | Module system (install, validate, resolve) |
| `backend/core/` | Core infrastructure (config, security, logging, debate engine) |
| `backend/migrations/` | Database migration scripts |
| `backend/llm_catalog/` | LLM model catalog management |
| `backend/tasks/` | Celery async tasks |
| `backend/state/` | Application state management |
