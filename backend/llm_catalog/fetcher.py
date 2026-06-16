"""Catalog fetcher — ``git clone`` / ``git pull`` to a local cache.

Subprocess-based (no GitPython dep, no new pyproject entries).
The fetched working tree is read-only from the perspective of
catalog consumers: nothing in the integration mutates files in
``<cache>/<source>/``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.core.config import Settings
from backend.llm_catalog.sources import SourceSpec, resolve_cache_root

logger = logging.getLogger(__name__)


@dataclass
class FetchResult:
    """Outcome of a fetch attempt."""

    source: str
    path: Path
    cloned: bool               # True if a fresh clone happened
    pulled: bool               # True if a `git pull` was issued
    commit_sha: str
    elapsed_ms: int
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "path": str(self.path),
            "cloned": self.cloned,
            "pulled": self.pulled,
            "commit_sha": self.commit_sha,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


def _git(*args: str, cwd: Path, timeout: int = 60) -> tuple[int, str, str]:
    if not shutil.which("git"):
        raise FileNotFoundError("git executable not found on PATH")
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _head_sha(cwd: Path) -> str:
    try:
        rc, out, _ = _git("rev-parse", "HEAD", cwd=cwd, timeout=10)
        if rc == 0:
            return out.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def fetch_source(
    source: SourceSpec,
    settings: Settings,
    *,
    force_clone: bool = False,
    timeout: int = 180,
) -> FetchResult:
    """Make sure ``source`` is checked out in the local cache.

    - If the cache dir doesn't exist (or ``force_clone=True``): run
      ``git clone <url> <dir>``.
    - Otherwise run ``git fetch`` + ``git reset --hard origin/<branch>``
      so the working tree matches the remote.

    On any failure the cached state is left untouched and the error
    is captured in the returned ``FetchResult``.
    """
    cache_root = resolve_cache_root(settings)
    target = cache_root / source.cache_dir_name
    t0 = time.monotonic()

    def _mk_result(**kw: Any) -> FetchResult:
        return FetchResult(
            source=source.name,
            path=target,
            elapsed_ms=int((time.monotonic() - t0) * 1000),
            **kw,
        )

    if not shutil.which("git"):
        return _mk_result(cloned=False, pulled=False, commit_sha="", error="git not on PATH")

    # Fresh clone
    if force_clone or not target.exists() or not (target / ".git").is_dir():
        if target.exists():
            shutil.rmtree(target)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", "--branch", source.branch, source.repo_url, str(target)],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if proc.returncode != 0:
                return _mk_result(
                    cloned=False,
                    pulled=False,
                    commit_sha="",
                    error=f"git clone failed (rc={proc.returncode}): {proc.stderr.strip()[:500]}",
                )
        except subprocess.TimeoutExpired:
            return _mk_result(
                cloned=False,
                pulled=False,
                commit_sha="",
                error=f"git clone timed out after {timeout}s",
            )
        except Exception as e:  # noqa: BLE001
            return _mk_result(
                cloned=False,
                pulled=False,
                commit_sha="",
                error=f"{type(e).__name__}: {e}",
            )
        return _mk_result(
            cloned=True,
            pulled=False,
            commit_sha=_head_sha(target),
        )

    # Update existing clone
    try:
        # Fetch the configured branch and hard-reset so a force-push
        # upstream doesn't leave us with a stale working tree.
        rc, out, err = _git("fetch", "origin", source.branch, cwd=target, timeout=timeout)
        if rc != 0:
            return _mk_result(
                cloned=False,
                pulled=False,
                commit_sha=_head_sha(target),
                error=f"git fetch failed (rc={rc}): {err.strip()[:500]}",
            )
        rc, out, err = _git(
            "reset", "--hard", f"origin/{source.branch}", cwd=target, timeout=30,
        )
        if rc != 0:
            return _mk_result(
                cloned=False,
                pulled=False,
                commit_sha=_head_sha(target),
                error=f"git reset failed (rc={rc}): {err.strip()[:500]}",
            )
    except subprocess.TimeoutExpired:
        return _mk_result(
            cloned=False,
            pulled=False,
            commit_sha=_head_sha(target),
            error=f"git fetch timed out after {timeout}s",
        )
    return _mk_result(
        cloned=False,
        pulled=True,
        commit_sha=_head_sha(target),
    )


def fetch_all(
    settings: Settings,
    *,
    sources: list[str] | None = None,
    timeout: int = 180,
) -> list[FetchResult]:
    """Fetch every source (or only the named ones)."""
    from backend.llm_catalog.sources import get_source  # avoid cycle

    selected: list[SourceSpec] = []
    if sources:
        for name in sources:
            selected.append(get_source(name, settings))
    else:
        from backend.llm_catalog.sources import list_sources as _ls

        selected = _ls(settings)

    return [fetch_source(s, settings, timeout=timeout) for s in selected]
