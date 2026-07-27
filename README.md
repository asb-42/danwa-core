# danwa-core

FastAPI backend for Danwa (Multi-Agent Debate Platform). Shared backend for `danwa` (end-user frontend) and `danwa-studio` (admin/developer frontend).

## Structure

```
danwa-core/
├── backend/                 # FastAPI Application
│   ├── main.py             # App factory (uvicorn entry point)
│   ├── api/                # API routes, dependencies, rate limiting
│   ├── core/               # Config, security, logging
│   ├── services/           # Business logic (debate, LLM, DMS, interactive, render)
│   ├── persistence/        # Database layer
│   ├── workflow/           # LangGraph workflow engine
│   ├── a2a/                # Agent-to-Agent protocol
│   ├── blueprints/         # Blueprint canvas engine
│   ├── llm_catalog/        # LLM provider catalog
│   ├── models/             # Data models
│   ├── modules/            # Module installer and resolver
│   ├── repositories/       # Repository pattern data access
│   ├── schemas/            # Pydantic schemas
│   ├── state/              # Workflow state management
│   ├── tasks/              # Background tasks
│   └── tools/              # Agent tools (web search, document parsing)
├── packages/               # Shared npm packages (monorepo)
│   ├── api-client/         # @danwa/api-client
│   ├── ui-core/            # @danwa/ui-core
│   └── i18n/               # @danwa/i18n
├── scripts/                # Management, migration, and utility scripts
├── config/                 # Configuration files and prompts
├── modules/                # Module definitions (managed by danwa-studio)
├── profiles/               # Profile definitions
├── templates/              # Jinja2 templates and document templates
├── deploy/                 # Deployment configs (Nginx, Prometheus)
├── tests/                  # pytest backend tests + BATS script tests
├── data/                   # Runtime data (database, DMS storage)
├── pyproject.toml          # Python dependencies (uv)
├── Dockerfile              # Docker image
└── docker-compose.yml      # Local development stack
```

## Quick Start

```bash
# Install dependencies
bash setup.sh

# Start backend only
bash manage.sh start

# Start backend + detect sibling frontends
bash manage.sh start

# Check status
bash manage.sh status
```

## Development

```bash
# Backend with auto-reload
uv run uvicorn backend.main:app --reload --port 8000

# Run tests
uv run pytest tests/

# Lint
uv run ruff check backend/
uv run ruff format backend/
```

## Shared Packages

The npm packages `@danwa/api-client`, `@danwa/ui-core`, and `@danwa/i18n` are consumed by `danwa` and `danwa-studio` via `file:` protocol references:

```json
"@danwa/api-client": "file:../packages/api-client"
```

## API Client Generation

```bash
cd packages/api-client
npm run generate  # Generates client from /openapi.json
```

## Docker

```bash
docker compose up -d
```

## Version

Current version: **1.2.0** (defined in `pyproject.toml` and `version` file).
