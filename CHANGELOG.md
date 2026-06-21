# Changelog

All notable changes to **danwa-core** (the FastAPI backend, extracted from
the danwa monorepo) are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project does **not** follow [Semantic Versioning](https://semver.org/)
strictly yet — the major version has not reached 1.0.0.

## [1.2.0] - 2026-06-20 — Test suite + v024 migration

### Added
- **Test suite** migrated from the danwa monorepo (v0.3.0 baseline, commit
  `81f8124`). Two test trees were copied 1:1 because they use the `backend.*`
  import layout that exists in danwa-core:
  - `tests/backend/` — 138 Python files (FastAPI route, model, service tests)
  - `tests/rag_regression/` — 8 Python files (focused regression tests)
  - `tests/MIGRATION_NOTES.md` — documents what was and wasn't migrated and why
- **Migration `v024_rag_project_id_dedup`** (`backend/migrations/v024_rag_project_id_dedup.py`).
  Rewrites DMS ChromaDB `project_id` from the legacy synthetic scope
  `case:{tenant_id}:{case_id}` to the bare `{case_id}` so old documents are
  visible to `get_chunks_by_document()` and `dms.list_documents()`. Safe to
  re-run.

### Test baseline (post-migration)
```
3646 collected
3068 passed (84%)
556 failed + 16 errors  — real monorepo-vs-danwa-core deltas; triaged separately
4 skipped
2 xfailed
```

The 556 failures and 16 errors are **not** caused by the migration itself.
They are pre-existing differences between the monorepo at v0.3.0 and the
current danwa-core state, and will be addressed in follow-up commits.

### Not migrated (intentional)
- `tests/test_*.py` (top-level) — use `src.dms.*` / `src.core.*` import layout
  that doesn't exist in danwa-core (code was reorganised to `backend.*`).
- `tests/frontend/` — tests the Svelte frontend, stays in `danwa` repo.
- `tests/manager/` — empty.

Refs: `plans/2026-06-20_danwa-user-facing-migration.md` (Phase 0a)

## [1.1.0] - initial danwa-core extraction

Initial extraction of the FastAPI backend from the danwa monorepo.
Includes all `backend/` code, `pyproject.toml`, `Dockerfile`, `docker-compose.yml`,
`Makefile`, `deploy/`, `config/`, `modules/`, `profiles/`, `scripts/`, `packages/`.
No tests included in this initial commit (see 1.2.0 for the test migration).
