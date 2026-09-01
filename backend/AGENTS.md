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
- **DMS scope ids**: the canonical DMS scope for a case is the bare `case_id`
  (derive via `_case_scope_id` in `backend/api/routers/case_scoped.py`, never
  an inline literal). The debate/workflow RAG path resolves DMS instances by
  the same bare id via `get_dms_for_project` (ProjectStore miss is not fatal —
  CaseStore cases have `case.json`, no `project.json`). Legacy
  `case:{tenant}:{case}` data is rewritten by migration
  `v024_rag_project_id_dedup` (Chroma metadata, `documents.project_id`,
  `rag_context.session_id`) on startup. Pinned by
  `tests/rag_regression/test_rag_scope_id_regression.py`.
- **DMS uploads are async in routes**: FastAPI routes must await
  `upload_document_async` / `add_document_async` (bounded `_INGEST_POOL` in
  `backend/services/dms/service.py`); sync `upload_document` / `add_document`
  are only for worker threads/tests without a running event loop.
- **DMS SQLite access goes through the DMSDB lock wrappers**: never call
  `dms.db.conn.execute/commit/...` from routers or services — use
  `dms.db.execute/executemany/commit/rollback` (RLock-serialized, `DMSDB` in
  `backend/services/dms/database.py`). The shared connection serves the
  request thread, `_INGEST_POOL`, and the agent worker. Multi-chunk ingestion
  uses `add_chunk(commit=False)` batches + ONE final `commit()`
  (`RAGPipeline.process_document`) so a concurrent delete cannot interleave
  mid-ingestion. Pinned by `tests/backend/test_dms_database.py`,
  `tests/backend/test_dms_core_comprehensive.py`.
- **One DMS instance per case**: `_get_dms_for_case` (case_scoped.py) caches
  under BOTH `("case", tenant_id, case_id)` and the bare `case_id` string key
  (the bare key is the canonical scope id); `get_dms_for_project(case_id)` and
  the agent worker reuse the same instance via that alias instead of opening a
  second DMS over the same directory. Do not construct `DMS` inline for a case
  outside these factories.
- **DMS routes require auth**: every route in `dms.py` and the case-scoped
  DMS/analysis routes takes `user: User = Depends(get_current_user)`;
  tenant-scoped routes additionally enforce membership via
  `_check_tenant_access` (admin bypass, fail-closed 403). Tests run with auth
  disabled via the autouse `_disable_auth` fixture (conftest).
- **Scanned-PDF ingestion**: empty-text PDFs are rasterized page-by-page
  (pdfplumber, `ocr_pdf_resolution`, capped by `ocr_pdf_max_pages`) and OCRed
  per page; a PDF/image with no OCR engine raises `ValueError` → HTTP 422,
  never a binary-as-text fallback. PaddleOCR lang codes map from `ocr_lang`
  (`deu→german`, `eng→en`, override `ocr_paddle_lang`).
- **Ingestion metadata is persisted**: `RAGPipeline.process_file` writes
  `word_count/char_count/page_count/ocr_used` and
  `metadata_json={"truncated": bool}` via `update_document_metadata` (allowed
  fields extended accordingly); route responses surface `truncated`. Ingestion
  truncation ceiling lives in `doc_parser.MAX_CONTEXT_CHARS` (2M chars,
  sanity ceiling only — prompt budgets are enforced at chunking/retrieval).
- **DMS singletons are lazy and shared** (review §4.1–§4.3, §4.8):
  `DMSVectorStore.__init__` performs no Chroma I/O — first-touch goes
  through `_ensure_initialized()` (RLock, double-checked) and failed init
  retries on the next call; all operations go via `self.collection`.
  `HybridRetriever` is cheap to construct: the cross-encoder is a
  module-level shared lazy singleton (`_get_cross_encoder`, overridable
  per-instance via the `cross_encoder` setter — a test seam). BM25
  corpus+index caching is single-flight under `_corpus_cache_lock` with
  a TTL; the BM25 corpus is capped by `MAX_BM25_CORPUS_SIZE` (10k chunks).
  `DEFAULT_DMS_CONFIG` contains no `embedding_model` — collections use
  Chroma's default embedding function; do not re-add the key (switching
  EFs corrupts existing collections).
- **ProfileService/UserKeyStore are app-lifetime singletons** (review
  §4.8): one global `ProfileService` via
  `profile_service.get_shared_profile_service()` — used by
  `deps.get_profile_service`, the profiles/llm_profiles routers,
  `node_functions._get_profile_service`, and as the default for
  `LLMService`/`TranslationService`/`AssistantService` (`profile_service=`/
  `db_path=` parameters keep tests isolated). Per-case instances go through
  `deps.get_profile_service_for_case_cached` and are invalidated by
  `ProjectStore.update` (via `invalidate_profile_service_cache`) so config
  writes are always seen on the next request. `legacy_nodes._get_profile_service`
  delegates to those caches. `UserKeyStore` is singleton via
  `deps.get_user_key_store()` (default path only); the BYOK router uses it.
  After profile writes, routers call `reload()` on the shared instance so
  every layer sees fresh data.
- **Uploads stream to disk** (review §4.5): both DMS upload routes copy in
  1 MiB chunks with a running total and abort with 413 the moment
  `max_file_size_mb` (from `load_dms_config()`) is exceeded — the body is
  never fully buffered in RAM; temp files are unlinked on every exit path.
- **Zombie documents are requeued at startup** (review §4.7): zero-chunk
  document rows (signal: `NOT EXISTS(document_chunks)`) are re-ingested by
  `main._requeue_zombie_documents` during lifespan if their `file_path`
  still exists; otherwise the row is marked `failed_ingest` in
  `metadata_json` (UI-visible reason) — never deleted. Pre-§2.3 rows with
  `word_count=0` but chunks are NOT zombies.

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
