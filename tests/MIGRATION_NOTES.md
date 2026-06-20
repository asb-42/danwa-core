# Test Migration Notes (0a.1)

Migrated from danwa monorepo at v0.3.0 baseline (commit 81f8124).

## Source

- `tests/backend/` — 138 Python files (FastAPI route tests, models, services)
- `tests/rag_regression/` — 8 Python files (focused regression tests)

## Not Migrated (intentional)

- `tests/test_*.py` (top-level) — these use the `src.dms.*` and `src.core.*` import layout
  that does not exist in danwa-core (code was reorganised to `backend.services.dms.*` during
  the danwa → danwa-core split). They need import rewrites or are obsolete and belong in
  danwa-modules. **Tracked as separate work item.**
- `tests/frontend/` — 2 Python tests that read Svelte/JS files (`Header.svelte`,
  `vite.config.js`). They test the danwa frontend, not danwa-core, and stay in the
  danwa repo.
- `tests/manager/` — empty, ignored.

## Test Run Baseline (danwa-core 13f501b + this commit)

```
3646 collected
3068 passed
556 failed
16 errors
4 skipped
2 xfailed
```

84% pass rate. The 556 failures + 16 errors are **real** danwa ↔ danwa-core
deltas that need to be triaged and fixed in subsequent commits. They are not caused
by the migration but by code-reorganisation between monorepo extraction and this point.

## Migration Files Touched

- `tests/backend/conftest.py`
- `tests/backend/*_api.py` (multiple)
- `tests/backend/*_regression.py` (multiple)
- `tests/rag_regression/conftest.py`
- `tests/rag_regression/*_regression.py` (multiple)
