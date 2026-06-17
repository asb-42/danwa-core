"""Tests for backend.llm_catalog.fetcher — git clone/pull wrapper.

We exercise only the pure-Python paths in ``fetch_source``:
- No git on PATH → error result, no exception
- Existing directory is not a git working tree → fresh clone attempt
- HEAD-SHA lookup with no git available → returns ""

The actual ``git clone``/``git fetch`` is exercised at the subprocess level
but we mock ``subprocess.run`` to avoid network/external-tool dependence.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from backend.core.config import Settings
from backend.llm_catalog.fetcher import (
    FetchResult,
    _git,
    _head_sha,
    fetch_source,
)
from backend.llm_catalog.sources import get_source

# ---------------------------------------------------------------------------
# _git
# ---------------------------------------------------------------------------


def test_git_raises_when_git_not_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(FileNotFoundError):
        _git("status", cwd=Path("/tmp"))


def test_git_returns_returncode_and_output(tmp_path: Path) -> None:
    fake = MagicMock()
    fake.return_value = MagicMock(returncode=0, stdout="hello\n", stderr="")
    with patch("backend.llm_catalog.fetcher.subprocess.run", fake):
        rc, out, err = _git("status", cwd=tmp_path)
    assert rc == 0
    assert out == "hello\n"
    assert err == ""


# ---------------------------------------------------------------------------
# _head_sha
# ---------------------------------------------------------------------------


def test_head_sha_returns_empty_when_no_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert _head_sha(tmp_path) == ""


def test_head_sha_returns_stripped_sha(tmp_path: Path) -> None:
    fake = MagicMock()
    fake.return_value = MagicMock(returncode=0, stdout="abcdef1234\n", stderr="")
    with patch("backend.llm_catalog.fetcher.subprocess.run", fake):
        sha = _head_sha(tmp_path)
    assert sha == "abcdef1234"


def test_head_sha_returns_empty_when_rev_parse_fails(tmp_path: Path) -> None:
    fake = MagicMock()
    fake.return_value = MagicMock(returncode=128, stdout="", stderr="fatal")
    with patch("backend.llm_catalog.fetcher.subprocess.run", fake):
        sha = _head_sha(tmp_path)
    assert sha == ""


def test_head_sha_handles_timeout(tmp_path: Path) -> None:
    def _raise(*_args: Any, **_kw: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=10)

    with patch("backend.llm_catalog.fetcher.subprocess.run", side_effect=_raise):
        assert _head_sha(tmp_path) == ""


# ---------------------------------------------------------------------------
# fetch_source
# ---------------------------------------------------------------------------


def test_fetch_source_no_git_returns_error_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    settings = Settings(catalog_cache_dir=tmp_path / "cache")
    src = get_source("catwalk", settings)
    result = fetch_source(src, settings)
    assert result.error is not None
    assert "git" in result.error.lower()
    assert result.cloned is False
    assert result.pulled is False


def test_fetch_source_force_clone_runs_git_clone(tmp_path: Path) -> None:
    settings = Settings(catalog_cache_dir=tmp_path / "cache")
    src = get_source("catwalk", settings)

    # Mock shutil.which and subprocess.run
    fake_proc = MagicMock(returncode=0, stdout="Cloning into...", stderr="")

    def _fake_which(cmd: str) -> str | None:
        return "/usr/bin/git" if cmd == "git" else None

    with (
        patch("backend.llm_catalog.fetcher.shutil.which", side_effect=_fake_which),
        patch("backend.llm_catalog.fetcher.subprocess.run", return_value=fake_proc) as run_mock,
    ):
        result = fetch_source(src, settings, force_clone=True)
    assert result.cloned is True
    assert result.error is None
    # The first subprocess.run call is `git clone ...`
    first_call_args = run_mock.call_args_list[0].args[0]
    assert first_call_args[0] == "git"
    assert first_call_args[1] == "clone"


def test_fetch_source_clone_failure_returns_error(tmp_path: Path) -> None:
    settings = Settings(catalog_cache_dir=tmp_path / "cache")
    src = get_source("catwalk", settings)
    fake_proc = MagicMock(returncode=128, stdout="", stderr="fatal: not found")

    def _fake_which(cmd: str) -> str | None:
        return "/usr/bin/git" if cmd == "git" else None

    with (
        patch("backend.llm_catalog.fetcher.shutil.which", side_effect=_fake_which),
        patch("backend.llm_catalog.fetcher.subprocess.run", return_value=fake_proc),
    ):
        result = fetch_source(src, settings, force_clone=True)
    assert result.cloned is False
    assert result.error is not None
    assert "git clone failed" in result.error


def test_fetch_source_clone_timeout_returns_error(tmp_path: Path) -> None:
    settings = Settings(catalog_cache_dir=tmp_path / "cache")
    src = get_source("catwalk", settings)

    def _fake_which(cmd: str) -> str | None:
        return "/usr/bin/git" if cmd == "git" else None

    def _fake_run(*_args: Any, **_kw: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="git clone", timeout=10)

    with (
        patch("backend.llm_catalog.fetcher.shutil.which", side_effect=_fake_which),
        patch("backend.llm_catalog.fetcher.subprocess.run", side_effect=_fake_run),
    ):
        result = fetch_source(src, settings, force_clone=True, timeout=10)
    assert result.cloned is False
    assert result.error is not None
    assert "timed out" in result.error.lower()


def test_fetch_source_existing_clone_pulls(tmp_path: Path) -> None:
    """If the target already has a .git dir, do ``git fetch`` + ``git reset``."""
    settings = Settings(catalog_cache_dir=tmp_path / "cache")
    src = get_source("catwalk", settings)

    # Create a fake existing clone
    target = tmp_path / "cache" / "catwalk"
    target.mkdir(parents=True)
    (target / ".git").mkdir()

    fake_proc = MagicMock(returncode=0, stdout="", stderr="")

    def _fake_which(cmd: str) -> str | None:
        return "/usr/bin/git" if cmd == "git" else None

    with (
        patch("backend.llm_catalog.fetcher.shutil.which", side_effect=_fake_which),
        patch("backend.llm_catalog.fetcher.subprocess.run", return_value=fake_proc) as run_mock,
    ):
        result = fetch_source(src, settings)
    assert result.pulled is True
    # First call: git fetch origin <branch>
    first = run_mock.call_args_list[0].args[0]
    assert first[:3] == ["git", "fetch", "origin"]


def test_fetch_source_existing_clone_fetch_failure(tmp_path: Path) -> None:
    settings = Settings(catalog_cache_dir=tmp_path / "cache")
    src = get_source("catwalk", settings)
    target = tmp_path / "cache" / "catwalk"
    target.mkdir(parents=True)
    (target / ".git").mkdir()

    fail_proc = MagicMock(returncode=128, stdout="", stderr="fetch failed")

    def _fake_which(cmd: str) -> str | None:
        return "/usr/bin/git" if cmd == "git" else None

    with (
        patch("backend.llm_catalog.fetcher.shutil.which", side_effect=_fake_which),
        patch("backend.llm_catalog.fetcher.subprocess.run", return_value=fail_proc),
    ):
        result = fetch_source(src, settings)
    assert result.pulled is False
    assert "git fetch failed" in (result.error or "")


# ---------------------------------------------------------------------------
# FetchResult serialization
# ---------------------------------------------------------------------------


def test_fetch_result_to_dict() -> None:
    fr = FetchResult(
        source="catwalk",
        path=Path("/tmp/cache/catwalk"),
        cloned=True,
        pulled=False,
        commit_sha="abc",
        elapsed_ms=100,
    )
    d = fr.to_dict()
    assert d["source"] == "catwalk"
    assert d["cloned"] is True
    assert d["commit_sha"] == "abc"
    assert d["elapsed_ms"] == 100
    assert d["error"] is None


def test_fetch_result_to_dict_with_error() -> None:
    fr = FetchResult(
        source="x",
        path=Path("/x"),
        cloned=False,
        pulled=False,
        commit_sha="",
        elapsed_ms=5,
        error="boom",
    )
    d = fr.to_dict()
    assert d["error"] == "boom"
