"""System management API router.

Provides endpoints for:
- profile reloading (``POST /reload-profiles``)
- log viewing (``GET /logs``)
- on-demand heavy-dep installation (``/install-gpu-deps``)

The ``/install-gpu-deps`` family exists because the default install
of danwa-core is *minimal* — it skips the ``[gpu]`` optional-dependency
group (see ``pyproject.toml``) to keep fresh-clone install under a
minute and under 200 MB instead of pulling torch + triton +
8 nvidia-cu* wheels (~3 GB). Operators who later need GPU-accelerated
OCR call this endpoint instead of re-running ``setup.sh --gpu`` by
hand. The Python ``import easyocr`` calls are already lazy (inside
function bodies) so the backend boots either way; the failure
surfaces only when OCR is exercised.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)

router = APIRouter()

_LOG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "logs"
_LOG_FILE = _LOG_DIR / "debate-agent.log"

# ───────────────────────────────────────────────────────────────────────
# /install-gpu-deps — on-demand GPU-deps installer
# ───────────────────────────────────────────────────────────────────────
# Background job tracking. In-memory only — restarting the backend
# drops in-flight jobs (which is fine: the operator can simply re-POST).
_GPU_INSTALL_JOBS: dict[str, dict] = {}
_GPU_INSTALL_LOCK = threading.Lock()
_GPU_INSTALL_LOGS_DIR = _LOG_DIR / "gpu-installs"


def _probe_easyocr() -> bool:
    """Return True if easyocr is importable in this Python env."""
    try:
        import easyocr  # noqa: F401

        return True
    except ImportError:
        return False
    except Exception:  # torch wheel mismatch, etc.
        return False


@router.get("/install-gpu-deps/status", tags=["system"])
def install_gpu_deps_status() -> dict:
    """Report GPU-deps state without doing any install work.

    Fast endpoint — no shell exec, no network. Safe to poll from a
    UI health badge or a frontend ``isBackendReachable``-style probe.

    Returns:
        Dict with:
          - ``installed`` (bool): whether ``easyocr`` is importable now.
          - ``install_command`` (str): shell command the operator can
            paste to enable GPU OCR. Empty when already installed.
          - ``recommended_group`` (str): ``"gpu"`` (PEP 735 name).
          - ``download_estimate_mb`` (int): rough size the install will
            pull (~3000 MB = torch + triton + 8 nvidia-cu* wheels).
    """
    installed = _probe_easyocr()
    return {
        "installed": installed,
        "install_command": "" if installed else "uv sync --group gpu",
        "alt_install_command": "" if installed else "pip install -e .[gpu]",
        "recommended_group": "gpu",
        "download_estimate_mb": 0 if installed else 3000,
    }


@router.post("/install-gpu-deps", tags=["system"])
def install_gpu_deps(background: bool = Query(default=True)) -> dict:
    """Kick off ``uv sync --group gpu`` to install the optional GPU deps.

    Runs in the background by default (returns 202-style payload
    immediately). Pass ``?background=false`` for a synchronous install
    that blocks until ``uv sync`` finishes (useful in tests; not
    recommended in production — the install may take 5–30 minutes).

    Returns:
        Dict with ``job_id``, ``status`` (``"started"`` or ``"completed"``),
        ``log_file`` path, and a ``poll_url`` for status checks.
    """
    if _probe_easyocr():
        return {
            "job_id": "",
            "status": "already_installed",
            "message": "easyocr is already importable in this Python environment.",
            "log_file": None,
            "poll_url": None,
        }

    uv_path = shutil.which("uv")
    if not uv_path:
        raise HTTPException(
            status_code=503,
            detail=(
                "`uv` is not on PATH. Install with: "
                "curl -LsSf https://astral.sh/uv/install.sh | sh"
            ),
        )

    # Find the repo root (parent of backend/api/routers/system.py).
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    pyproject = repo_root / "pyproject.toml"
    if not pyproject.exists():
        raise HTTPException(
            status_code=500,
            detail=(
                f"pyproject.toml not found at {pyproject}. "
                "This endpoint must run from inside the danwa-core repo root."
            ),
        )

    job_id = uuid.uuid4().hex[:12]
    _GPU_INSTALL_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _GPU_INSTALL_LOGS_DIR / f"uv-sync-gpu-{int(time.time())}-{job_id}.log"

    cmd = [uv_path, "sync", "--group", "gpu"]
    logger.info(
        "Starting GPU-deps install (job_id=%s): %s (cwd=%s, log=%s)",
        job_id, " ".join(cmd), repo_root, log_path,
    )

    if not background:
        # Synchronous path: blocks for the duration of uv sync.
        # Only suitable for tests / interactive scripts.
        with open(log_path, "w", encoding="utf-8") as logf:
            logf.write(f"# synchronous uv sync --group gpu\n# cmd: {' '.join(cmd)}\n# cwd: {repo_root}\n\n")
            logf.flush()
            try:
                proc = subprocess.run(
                    cmd, cwd=str(repo_root), stdout=logf, stderr=subprocess.STDOUT,
                    check=False, env={**os.environ, "PYTHONUNBUFFERED": "1"},
                )
            except Exception as exc:
                logf.write(f"\n# ERROR spawning uv: {exc}\n")
                raise HTTPException(status_code=500, detail=f"uv spawn failed: {exc}") from exc
        return {
            "job_id": job_id,
            "status": "completed" if proc.returncode == 0 else "failed",
            "returncode": proc.returncode,
            "log_file": str(log_path),
            "poll_url": None,
            "message": (
                "easyocr is now importable; restart workers to pick it up."
                if proc.returncode == 0
                else f"uv sync exited {proc.returncode}; see {log_path}"
            ),
        }

    # Background path: spawn detached, return 202-style payload.
    with _GPU_INSTALL_LOCK:
        # Reject if another job is already in flight (one at a time).
        for jid, meta in _GPU_INSTALL_JOBS.items():
            if meta.get("status") == "running":
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"Another GPU-deps install is already running "
                        f"(job_id={jid}). Wait for it to finish or poll "
                        f"/api/v1/system/install-gpu-deps/{jid}."
                    ),
                )

        logf = open(log_path, "w", encoding="utf-8")
        logf.write(f"# background uv sync --group gpu\n# cmd: {' '.join(cmd)}\n# cwd: {repo_root}\n# job_id: {job_id}\n\n")
        logf.flush()
        try:
            proc = subprocess.Popen(
                cmd, cwd=str(repo_root), stdout=logf, stderr=subprocess.STDOUT,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
            )
        except Exception as exc:
            logf.close()
            raise HTTPException(status_code=500, detail=f"uv spawn failed: {exc}") from exc

        _GPU_INSTALL_JOBS[job_id] = {
            "status": "running",
            "started_at": time.time(),
            "pid": proc.pid,
            "log_file": str(log_path),
            "cmd": cmd,
        }

    # Reaper thread: poll the subprocess so the job dict reflects completion.
    def _reap() -> None:
        rc = proc.wait()
        with _GPU_INSTALL_LOCK:
            # The job may have been popped (e.g. by a test) between
            # the spawn and the reap — in that case re-insert so the
            # final status is visible.
            prior = _GPU_INSTALL_JOBS.get(job_id, {})
            _GPU_INSTALL_JOBS[job_id] = {
                **prior,
                "status": "completed" if rc == 0 else "failed",
                "started_at": prior.get("started_at", time.time()),
                "pid": prior.get("pid", proc.pid),
                "log_file": prior.get("log_file", str(log_path)),
                "cmd": prior.get("cmd", cmd),
                "finished_at": time.time(),
                "returncode": rc,
            }
        try:
            logf.close()
        except Exception:
            pass
        logger.info("GPU-deps install job_id=%s finished rc=%s", job_id, rc)

    threading.Thread(target=_reap, name=f"gpu-install-{job_id}", daemon=True).start()

    return {
        "job_id": job_id,
        "status": "started",
        "pid": proc.pid,
        "log_file": str(log_path),
        "poll_url": f"/api/v1/system/install-gpu-deps/{job_id}",
        "message": (
            "uv sync --group gpu is running in the background. "
            "This may take 5–30 minutes on a fresh clone."
        ),
    }


@router.get("/install-gpu-deps/{job_id}", tags=["system"])
def get_install_gpu_deps_status(job_id: str) -> dict:
    """Poll a background ``install-gpu-deps`` job.

    Returns 404 if the job_id is unknown (e.g. backend restarted —
    job dict is in-memory only). 410 Gone is NOT used; the operator
    can simply re-POST.
    """
    with _GPU_INSTALL_LOCK:
        meta = _GPU_INSTALL_JOBS.get(job_id)
    if meta is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"job_id={job_id} not found. Job state is in-memory only; "
                "if the backend restarted, re-POST /install-gpu-deps."
            ),
        )
    return {"job_id": job_id, **meta}


@router.post("/reload-profiles")
def reload_profiles() -> dict:
    """Reload all profiles from YAML files.

    Forces ProfileService and PromptService singletons to re-read
    their YAML/markdown files from disk. Also clears the workflow
    nodes' cached ProfileService instances so running debates pick
    up the updated profiles immediately.
    """
    from backend.api.routers.profiles import get_profile_service
    from backend.workflow import legacy_nodes as workflow_nodes
    from backend.workflow import node_functions

    try:
        ps = get_profile_service()
        ps.reload()
        logger.info("Profiles reloaded successfully")

        # Also clear prompt cache
        prompt_svc = workflow_nodes._get_prompt_service()
        prompt_svc.clear_cache()
        logger.info("Prompt cache cleared")

        # Clear workflow nodes' cached ProfileService/PromptService instances
        # so that running debates pick up updated profiles immediately
        workflow_nodes._profile_service_cache.clear()
        workflow_nodes._prompt_service_cache.clear()
        workflow_nodes._profile_service = None
        workflow_nodes._prompt_service = None
        node_functions._profile_service = None
        node_functions._prompt_service = None
        logger.info("Workflow nodes' profile/prompt service caches cleared")

        return {
            "status": "ok",
            "message": "Profiles and prompts reloaded",
            "llm_profiles": len(ps.list_llm_profiles()),
        }
    except Exception as exc:
        logger.error("Profile reload failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Reload failed: {exc}") from exc


@router.get("/logs")
def get_logs(
    lines: int = Query(default=100, ge=1, le=5000, description="Number of recent log lines"),
    search: str | None = Query(default=None, description="Filter lines containing this text"),
) -> dict:
    """Return recent backend log lines.

    Reads the last N lines from the log file, optionally filtered by a search term.
    """
    if not _LOG_FILE.exists():
        return {"lines": [], "total_lines": 0, "log_file": str(_LOG_FILE)}

    try:
        with open(_LOG_FILE, encoding="utf-8") as f:
            all_lines = f.readlines()

        if search:
            all_lines = [line for line in all_lines if search.lower() in line.lower()]

        # Return last N lines
        selected = all_lines[-lines:]
        return {
            "lines": [line.rstrip("\n") for line in selected],
            "total_lines": len(all_lines),
            "returned_lines": len(selected),
            "log_file": str(_LOG_FILE),
        }
    except Exception as exc:
        logger.error("Failed to read log file: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to read logs: {exc}") from exc
