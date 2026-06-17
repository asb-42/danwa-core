"""Tests for backend.llm_catalog.sources — catalog source registry."""

from __future__ import annotations

import pytest

from backend.core.config import Settings
from backend.llm_catalog.sources import (
    SourceSpec,
    get_source,
    list_sources,
    resolve_cache_root,
)


def _settings() -> Settings:
    return Settings()


# ---------------------------------------------------------------------------
# SourceSpec
# ---------------------------------------------------------------------------


def test_source_spec_is_frozen() -> None:
    s = SourceSpec(name="x", repo_url="u", branch="b", path="p")
    with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
        s.name = "y"  # type: ignore[misc]


def test_source_spec_cache_dir_name() -> None:
    s = SourceSpec(name="catwalk", repo_url="u", branch="b", path="p")
    assert s.cache_dir_name == "catwalk"


def test_source_spec_default_description() -> None:
    s = SourceSpec(name="x", repo_url="u", branch="b", path="p")
    assert s.description == ""


def test_source_spec_with_description() -> None:
    s = SourceSpec(name="x", repo_url="u", branch="b", path="p", description="d")
    assert s.description == "d"


# ---------------------------------------------------------------------------
# get_source
# ---------------------------------------------------------------------------


def test_get_source_catwalk() -> None:
    s = get_source("catwalk", _settings())
    assert s.name == "catwalk"
    assert "catwalk" in s.repo_url.lower() or s.branch == "main"


def test_get_source_llm_db() -> None:
    s = get_source("llm_db", _settings())
    assert s.name == "llm_db"


def test_get_source_case_insensitive() -> None:
    s = get_source("CATWALK", _settings())
    assert s.name == "catwalk"


def test_get_source_unknown_raises() -> None:
    with pytest.raises(KeyError):
        get_source("not-a-source", _settings())


def test_get_source_no_settings_uses_defaults() -> None:
    """Calling with ``settings=None`` should fall back to default Settings()."""
    s = get_source("catwalk")
    assert s.name == "catwalk"


# ---------------------------------------------------------------------------
# list_sources
# ---------------------------------------------------------------------------


def test_list_sources_returns_at_least_two() -> None:
    sources = list_sources(_settings())
    names = {s.name for s in sources}
    assert {"catwalk", "llm_db"} <= names


def test_list_sources_stable_order() -> None:
    """``list_sources`` is deterministic across calls."""
    a = list_sources(_settings())
    b = list_sources(_settings())
    assert [s.name for s in a] == [s.name for s in b]


def test_list_sources_no_settings() -> None:
    sources = list_sources()
    assert len(sources) >= 2


# ---------------------------------------------------------------------------
# resolve_cache_root
# ---------------------------------------------------------------------------


def test_resolve_cache_root_creates_directory(tmp_path: Path) -> None:
    s = Settings(catalog_cache_dir=tmp_path / "cache")
    root = resolve_cache_root(s)
    assert root.exists()
    assert root.is_dir()


def test_resolve_cache_root_expands_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    s = Settings(catalog_cache_dir="~/my-cache")
    root = resolve_cache_root(s)
    assert root.exists()
    assert "my-cache" in str(root)


def test_resolve_cache_root_handles_existing_dir(tmp_path: Path) -> None:
    s = Settings(catalog_cache_dir=tmp_path / "existing")
    (tmp_path / "existing").mkdir()
    root = resolve_cache_root(s)
    assert root == (tmp_path / "existing").resolve()
