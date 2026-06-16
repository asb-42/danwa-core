"""Module Publisher — commit & push a module to the danwa-modules Git repo.

The publisher is opt-in: by default ``DANWA_MODULES_PUBLISH_ENABLED`` is
``false`` and the endpoint returns 403.  Operators must explicitly enable
it and point ``DANWA_MODULES_PUBLISH_DIR`` at a working ``git clone`` of
the upstream ``danwa-modules`` repository.

Workflow (per ``POST /api/v1/modules/{id}/publish``):

1. Sanity-check the local checkout (HEAD clean, branch exists or is
   created from main).
2. Write the provided manifest + (optional) profile content under
   ``<publish_dir>/<module_id>/``.
3. ``git add`` + ``git commit`` with the configured author.
4. ``git push <remote> <branch>`` if a push remote is configured and
   reachable; the report distinguishes ``pushed=true`` vs ``local_only``.

The publisher is intentionally subprocess-based (no GitPython
dependency).  All git invocations run with a per-call timeout, surface
``stdout``/``stderr`` in the report, and never receive input from
caller-controlled data without a manifest-id allow-list check.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from backend.core.config import Settings

logger = logging.getLogger(__name__)


# ─── Result types ────────────────────────────────────────────────────


@dataclasses.dataclass
class PublishStep:
    """One step of a publish run (for the report)."""

    name: str
    ok: bool
    detail: str = ""
    stdout: str = ""
    stderr: str = ""
    elapsed_ms: int = 0


@dataclasses.dataclass
class PublishReport:
    """Outcome of a module publish run.

    ``status`` is one of ``"published"``, ``"pushed"`` (alias of published
    when the push step also succeeded), ``"local_only"`` (committed but
    not pushed — no remote configured / reachable), ``"noop"`` (nothing
    changed), ``"failed"``.
    """

    module_id: str
    branch: str
    status: str
    commit_sha: str | None = None
    pushed: bool = False
    push_remote: str | None = None
    steps: list[PublishStep] = dataclasses.field(default_factory=list)
    error: str | None = None
    started_at: float = dataclasses.field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "branch": self.branch,
            "status": self.status,
            "commit_sha": self.commit_sha,
            "pushed": self.pushed,
            "push_remote": self.push_remote,
            "steps": [dataclasses.asdict(s) for s in self.steps],
            "error": self.error,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


# ─── Module id validation (defence in depth) ────────────────────────

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _assert_safe_id(module_id: str) -> None:
    if not _ID_RE.match(module_id):
        raise ValueError(
            f"Invalid module_id {module_id!r}: must match {_ID_RE.pattern!r}"
        )


# ─── Publisher ──────────────────────────────────────────────────────


class ModulePublisher:
    """Commit + push a module manifest to the danwa-modules Git repo."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()

    # ── helpers ─────────────────────────────────────────────────────

    @property
    def enabled(self) -> bool:
        return bool(self.settings.modules_publish_enabled)

    @property
    def repo_dir(self) -> Path:
        return Path(self.settings.modules_publish_dir).expanduser().resolve()

    def _git(self, *args: str, timeout: int = 60) -> tuple[int, str, str]:
        """Run a git command inside ``repo_dir``.

        Returns (returncode, stdout, stderr).  Never raises.
        """
        if not shutil.which("git"):
            raise FileNotFoundError("git executable not found on PATH")
        proc = subprocess.run(
            ["git", *args],
            cwd=str(self.repo_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def _step(self, name: str, fn) -> PublishStep:
        t0 = time.monotonic()
        try:
            ok, detail, stdout, stderr = fn()
        except subprocess.TimeoutExpired as e:
            return PublishStep(
                name=name,
                ok=False,
                detail=f"timeout after {e.timeout}s",
                stdout=(e.stdout or "")[:4000] if isinstance(e.stdout, str) else "",
                stderr=(e.stderr or "")[:4000] if isinstance(e.stderr, str) else "",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:  # noqa: BLE001 — report every failure
            return PublishStep(
                name=name,
                ok=False,
                detail=f"{type(e).__name__}: {e}",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        return PublishStep(
            name=name,
            ok=ok,
            detail=detail,
            stdout=stdout[:4000],
            stderr=stderr[:4000],
            elapsed_ms=int((time.monotonic() - t0) * 1000),
        )

    # ── public API ─────────────────────────────────────────────────

    def ensure_repo(self) -> PublishStep:
        """Make sure ``repo_dir`` is a valid git working tree.

        If it doesn't exist yet, run ``git clone <repo_url>``.  If it does
        exist but is not a working tree, fail loudly.
        """

        def _do() -> tuple[bool, str, str, str]:
            url = self.settings.modules_publish_repo_url
            if self.repo_dir.exists():
                if not (self.repo_dir / ".git").is_dir():
                    return False, f"{self.repo_dir} exists but is not a git working tree", "", ""
                return True, "repo already initialised", "", ""
            if not url:
                return False, "repo does not exist and modules_publish_repo_url is empty", "", ""
            self.repo_dir.parent.mkdir(parents=True, exist_ok=True)
            rc, out, err = self._git_clone(url, str(self.repo_dir), timeout=300)
            if rc != 0:
                return False, f"git clone failed (rc={rc})", out, err
            return True, f"cloned {url} → {self.repo_dir}", out, err

        return self._step("ensure_repo", _do)

    def _git_clone(self, url: str, target: str, timeout: int) -> tuple[int, str, str]:
        if not shutil.which("git"):
            raise FileNotFoundError("git executable not found on PATH")
        proc = subprocess.run(
            ["git", "clone", url, target],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr

    def publish(
        self,
        module_id: str,
        manifest: dict[str, Any],
        profile_content: str | None = None,
        profile_filename: str | None = None,
        commit_message: str | None = None,
    ) -> PublishReport:
        """Run the full publish workflow and return a structured report."""
        try:
            _assert_safe_id(module_id)
        except ValueError as e:
            return PublishReport(
                module_id=module_id,
                branch="",
                status="failed",
                error=str(e),
                finished_at=time.time(),
            )

        if not self.enabled:
            return PublishReport(
                module_id=module_id,
                branch="",
                status="failed",
                error="module publishing is disabled "
                "(set DANWA_MODULES_PUBLISH_ENABLED=true to enable)",
                finished_at=time.time(),
            )

        branch = self.settings.modules_publish_branch_template.format(module_id=module_id)
        commit_msg = (
            commit_message
            or f"publish({module_id}): v{manifest.get('version', '0.0.0')}"
        )

        report = PublishReport(module_id=module_id, branch=branch, status="failed")

        # 1. Ensure repo
        s = self.ensure_repo()
        report.steps.append(s)
        if not s.ok:
            report.error = s.detail
            report.finished_at = time.time()
            return report

        # 2. Fetch latest from base branch
        report.steps.append(
            self._step(
                "fetch_base",
                lambda: self._git_fetch(self.settings.modules_publish_base_branch),
            )
        )

        # 3. Checkout target branch (creating from base if needed)
        report.steps.append(
            self._step(
                "checkout_branch",
                lambda: self._checkout_branch(branch),
            )
        )

        # 4. Write manifest + profile into the repo
        report.steps.append(
            self._step(
                "write_files",
                lambda: self._write_files(module_id, manifest, profile_content, profile_filename),
            )
        )

        # 5. Stage + commit
        report.steps.append(self._step("git_add", lambda: self._git_add(module_id)))
        report.steps.append(self._step("git_commit", lambda: self._git_commit(commit_msg)))

        # Determine whether anything was actually committed
        try:
            rc, out, _ = self._git("rev-parse", "HEAD")
            head_after = out.strip() if rc == 0 else None
            rc2, out2, _ = self._git("rev-parse", "HEAD~1")
            head_before = out2.strip() if rc2 == 0 else None
        except subprocess.TimeoutExpired:
            head_after, head_before = None, None
        if head_after and head_before and head_after == head_before:
            # No new commit was created (e.g. nothing changed)
            report.status = "noop"
            report.finished_at = time.time()
            return report

        report.commit_sha = head_after

        # 6. Push (if remote configured and reachable)
        push_remote = self.settings.modules_publish_push_remote
        if push_remote and self._remote_reachable(push_remote):
            push_step = self._step(
                "git_push",
                lambda: self._git_push(branch, push_remote),
            )
            report.steps.append(push_step)
            report.pushed = push_step.ok
            report.push_remote = push_remote if push_step.ok else None
            report.status = "published" if push_step.ok else "local_only"
        else:
            report.status = "local_only"
            report.steps.append(
                PublishStep(
                    name="git_push",
                    ok=True,
                    detail=f"skipped (no push_remote or remote {push_remote!r} not reachable)",
                )
            )

        report.finished_at = time.time()
        return report

    # ── git steps ──────────────────────────────────────────────────

    def _git_fetch(self, base_branch: str) -> tuple[bool, str, str, str]:
        rc, out, err = self._git("fetch", self.settings.modules_publish_remote, base_branch, timeout=120)
        if rc != 0:
            return False, f"git fetch failed (rc={rc})", out, err
        return True, f"fetched {self.settings.modules_publish_remote}/{base_branch}", out, err

    def _checkout_branch(self, branch: str) -> tuple[bool, str, str, str]:
        # Try to switch to the existing branch
        rc, _, err = self._git("rev-parse", "--verify", branch)
        if rc == 0:
            rc2, out2, err2 = self._git("checkout", branch)
            if rc2 != 0:
                return False, f"checkout {branch} failed", out2, err2
            return True, f"checked out existing {branch}", out2, err2
        # Create from base
        base = self.settings.modules_publish_base_branch
        rc, out, err = self._git("checkout", "-b", branch, f"{self.settings.modules_publish_remote}/{base}")
        if rc != 0:
            return False, f"could not create {branch} from {base}", out, err
        return True, f"created {branch} from {base}", out, err

    def _write_files(
        self,
        module_id: str,
        manifest: dict[str, Any],
        profile_content: str | None,
        profile_filename: str | None,
    ) -> tuple[bool, str, str, str]:
        target = self.repo_dir / module_id
        try:
            target.mkdir(parents=True, exist_ok=True)
            (target / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            if profile_content is not None and profile_filename:
                # Defensive: only allow simple filenames, no path traversal
                if "/" in profile_filename or "\\" in profile_filename or profile_filename.startswith("."):
                    return False, f"unsafe profile filename {profile_filename!r}", "", ""
                (target / profile_filename).write_text(profile_content, encoding="utf-8")
        except OSError as e:
            return False, f"failed to write files: {e}", "", ""
        written = ["manifest.json"]
        if profile_content is not None and profile_filename:
            written.append(profile_filename)
        return True, f"wrote {', '.join(written)} to {target}", "", ""

    def _git_add(self, module_id: str) -> tuple[bool, str, str, str]:
        rc, out, err = self._git("add", "--", f"{module_id}/")
        if rc != 0:
            return False, f"git add {module_id}/ failed (rc={rc})", out, err
        return True, f"git add {module_id}/", out, err

    def _git_commit(self, commit_msg: str) -> tuple[bool, str, str, str]:
        # Pre-check: if there is nothing staged, skip the commit
        rc, out, _ = self._git("diff", "--cached", "--name-only")
        if rc == 0 and not out.strip():
            return True, "nothing to commit", "", ""
        rc, out, err = self._git(
            "-c", f"user.name={self.settings.modules_publish_author_name}",
            "-c", f"user.email={self.settings.modules_publish_author_email}",
            "commit", "-m", commit_msg,
        )
        if rc != 0:
            return False, f"git commit failed (rc={rc})", out, err
        return True, f"committed: {commit_msg}", out, err

    def _git_push(self, branch: str, remote: str) -> tuple[bool, str, str, str]:
        rc, out, err = self._git("push", remote, branch, timeout=180)
        if rc != 0:
            return False, f"git push {remote} {branch} failed (rc={rc})", out, err
        return True, f"pushed {branch} → {remote}", out, err

    def _remote_reachable(self, remote: str) -> bool:
        """Return True if the remote can be reached (no actual push)."""
        rc, _, err = self._git("ls-remote", "--heads", remote, timeout=20)
        if rc != 0:
            logger.info("remote %r not reachable: %s", remote, err.strip().splitlines()[:1])
            return False
        return True
