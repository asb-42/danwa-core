"""Pytest conftest for the danwa-core test suite.

Centralised fixtures and configuration used by every test module.

Notes
-----
* ``pythonpath = ["."]`` is set in ``pyproject.toml`` so the project root is
  importable.  This conftest adds further safety by inserting the repo root
  to ``sys.path`` at conftest load time — useful when running ``pytest``
  from sub-directories.
* We never write to the real on-disk modules dir.  Fixtures that touch the
  filesystem always use ``tmp_path``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure project root is on sys.path (defensive — pyproject.toml already does it).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip DANWA_* env vars from the test process so the Settings class
    loads with its in-code defaults.

    The default tests are deterministic and don't depend on the developer's
    local ``.env``.  Tests that *want* to verify env-var handling do their
    own ``monkeypatch.setenv`` after this fixture runs.
    """
    for k in list(os.environ):
        if k.startswith("DANWA_"):
            monkeypatch.delenv(k, raising=False)
    # Force a known JWT secret so any code path that builds a token works
    monkeypatch.setenv("DANWA_JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only")


# ---------------------------------------------------------------------------
# Filesystem fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_modules_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a tempdir-based modules root and redirect the service layer to it."""
    import backend.modules.service as _svc

    modules = tmp_path / "modules"
    modules.mkdir(parents=True)
    (modules / "llm-profiles").mkdir()
    monkeypatch.setattr(_svc, "MODULES_DIR", str(modules))
    return modules


@pytest.fixture
def temp_catalog_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a tempdir for LLM catalog fetches."""
    cache = tmp_path / "catalog-cache"
    cache.mkdir(parents=True)
    monkeypatch.setenv("DANWA_CATALOG_CACHE_DIR", str(cache))
    return cache


# ---------------------------------------------------------------------------
# Data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_user_dict() -> dict:
    """A minimal but realistic user record."""
    return {
        "id": "user-1",
        "email": "[email protected]",
        "display_name": "Alice",
        "password_hash": "$2b$12$abcdefghijklmnopqrstuv",
        "role": "admin",
        "tenant_id": "_default",
        "is_active": True,
    }


@pytest.fixture
def sample_debate_request_dict() -> dict:
    """A minimal but valid ``DebateRequest`` payload."""
    return {
        "case": {"text": "Soll die Stadt ein Tempolimit einführen?"},
        "max_rounds": 3,
        "consensus_threshold": 0.8,
    }


# ---------------------------------------------------------------------------
# Async / event loop
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop_policy():
    """Use the default asyncio policy everywhere."""
    import asyncio

    return asyncio.DefaultEventLoopPolicy()
