"""Tests for backend.core.llm_id_aliases — legacy-ID → UUID resolution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core import llm_id_aliases as aliases

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the module-level cache + remap _MAPPING_FILE to a tmp path."""
    aliases._LEGACY_ALIASES.clear()
    fake_file = tmp_path / "llm_id_mapping.json"
    fake_file.write_text(json.dumps({"old-key": "uuid-1234567890"}), encoding="utf-8")
    monkeypatch.setattr(aliases, "_MAPPING_FILE", fake_file)
    yield
    aliases._LEGACY_ALIASES.clear()


# ---------------------------------------------------------------------------
# resolve_llm_id
# ---------------------------------------------------------------------------


def test_resolve_llm_id_none_returns_empty() -> None:
    assert aliases.resolve_llm_id(None) == ""


def test_resolve_llm_id_empty_returns_empty() -> None:
    assert aliases.resolve_llm_id("") == ""


def test_resolve_llm_id_resolves_known_alias() -> None:
    assert aliases.resolve_llm_id("old-key") == "uuid-1234567890"


def test_resolve_llm_id_passes_through_unknown() -> None:
    assert aliases.resolve_llm_id("not-an-alias") == "not-an-alias"


def test_resolve_llm_id_idempotent() -> None:
    """Resolving twice yields the same result."""
    assert aliases.resolve_llm_id("old-key") == aliases.resolve_llm_id("old-key")


def test_resolve_llm_id_already_uuid_passes_through() -> None:
    assert aliases.resolve_llm_id("ac-12345678-1234-1234-1234-123456789012") == "ac-12345678-1234-1234-1234-123456789012"


# ---------------------------------------------------------------------------
# _load_mapping — error paths
# ---------------------------------------------------------------------------


def test_load_mapping_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(aliases, "_MAPPING_FILE", tmp_path / "absent.json")
    aliases._LEGACY_ALIASES.clear()
    assert aliases._load_mapping() == {}


def test_load_mapping_invalid_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(aliases, "_MAPPING_FILE", bad)
    aliases._LEGACY_ALIASES.clear()
    assert aliases._load_mapping() == {}


def test_load_mapping_empty_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(aliases, "_MAPPING_FILE", empty)
    aliases._LEGACY_ALIASES.clear()
    assert aliases._load_mapping() == {}


# ---------------------------------------------------------------------------
# _ensure_loaded — lazy loading
# ---------------------------------------------------------------------------


def test_ensure_loaded_loads_once() -> None:
    aliases._LEGACY_ALIASES.clear()
    aliases._ensure_loaded()
    assert "old-key" in aliases._LEGACY_ALIASES
    # second call should not re-read (idempotent)
    aliases._ensure_loaded()
    assert "old-key" in aliases._LEGACY_ALIASES


# ---------------------------------------------------------------------------
# get_default_llm_profile_id
# ---------------------------------------------------------------------------


def test_get_default_llm_profile_id_returns_known_default() -> None:
    """When the canonical alias is present, return its UUID; else empty string."""
    # The active default alias is ``xiaomi-mimo-v2.5-pro``; not in our fake map.
    assert aliases.get_default_llm_profile_id() == ""


def test_get_default_llm_profile_id_returns_uuid_when_mapping_present(tmp_path: Path) -> None:
    """If the mapping contains the canonical default alias, return the UUID."""
    aliases._LEGACY_ALIASES[aliases._ACTIVE_DEFAULT_ALIAS] = "uuid-abc"
    assert aliases.get_default_llm_profile_id() == "uuid-abc"
