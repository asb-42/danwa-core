# danwa-core

FastAPI Backend für Danwa (Multi-Agent Debate Platform) — Shared Backend für danwa (Endbenutzer) und danwa-studio (Admin/Dev).

## Struktur

```
danwa-core/
├── backend/                 # FastAPI Application
│   ├── main.py             # App Factory (uvicorn entry point)
│   ├── api/                # API Routes
│   ├── core/               # Config, Security, Logging
│   ├── workflow/           # LangGraph Workflow Engine
│   ├── services/           # Business Logic Services
│   ├── persistence/        # Data Persistence
│   ├── a2a/                # A2A Protocol
│   └── ...
├── packages/               # Shared npm Packages (monorepo style)
│   ├── api-client/         # @danwa/api-client
│   ├── ui-core/            # @danwa/ui-core
│   └── i18n/               # @danwa/i18n
├── scripts/                # Utility Scripts
├── config/                 # Configuration Files
├── modules/                # Module Definitions (read-only, managed by danwa-studio)
├── profiles/               # Profile Definitions (read-only)
├── deploy/                 # Deployment Configs
├── pyproject.toml          # Python Dependencies
├── Dockerfile              # Docker Image
└── docker-compose.yml      # Local Development Stack
```

## Entwicklung

```bash
# Backend starten
uv run uvicorn backend.main:app --reload --port 8000

# Shared Packages entwickeln
cd packages/api-client && npm run dev
cd packages/ui-core && npm run dev
cd packages/i18n && npm run build
```

## API Client Generierung

```bash
cd packages/api-client
npm run generate  # Generiert Client aus /openapi.json
```

## Deployment

```bash
docker compose up -d
```

## Shared Packages

Die Packages `@danwa/api-client`, `@danwa/ui-core`, `@danwa/i18n` werden als npm-Pakete publiziert und von `danwa` und `danwa-studio` konsumiert.

In Development via `file:` Protocol:
```json
"@danwa/api-client": "file:../packages/api-client"
```