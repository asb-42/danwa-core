# DOX: tests/

## Purpose

Test suites for danwa-core: backend unit/integration tests (pytest), RAG regression tests, and shell script tests (BATS).

## Ownership

- **Backend Tests**: `tests/backend/` — 171 pytest test files
- **RAG Regression**: `tests/rag_regression/` — 8 RAG-specific regression tests
- **Script Tests**: `tests/scripts/` — BATS shell script tests

## Local Contracts

- Backend tests use fixtures from `tests/backend/conftest.py`
- Script tests use BATS framework with helpers in `tests/scripts/helpers/`
- RAG regression tests use mock LLM contracts

## Work Guidance

- Add tests for new features and bug fixes
- Follow existing test patterns (naming, fixtures, assertions)
- Keep tests independent and idempotent
- Use fixtures for shared setup, not module-level state

## Verification

- Backend: `pytest tests/backend/ -v`
- Scripts: `bats tests/scripts/`
- All tests must pass before merge

## Child DOX Index

| Child | Purpose |
|-------|---------|
| `tests/backend/` | Backend unit/integration tests (171 files) |
| `tests/rag_regression/` | RAG pipeline regression tests (8 files) |
| `tests/scripts/` | Shell script tests (BATS framework) |
