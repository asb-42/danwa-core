"""Tests for /api/v1/system/install-gpu-deps.

These tests cover the **on-demand GPU-deps install** endpoint added in
Phase 8 of the repo-setup-orchestration plan. The endpoint exists
because the default install of danwa-core is minimal — it skips the
``[gpu]`` optional-dependency group (easyocr → torch + nvidia-cu*,
~3 GB) and operators enable GPU OCR via this HTTP endpoint rather
than re-running setup.sh.

The test cases below exercise:
- GET  /install-gpu-deps/status   — fast probe, no shell exec.
- POST /install-gpu-deps           — kicks off background `uv sync --group gpu`.
- POST /install-gpu-deps           — short-circuits when already installed.
- POST /install-gpu-deps           — rejects second concurrent job.
- GET  /install-gpu-deps/{job_id}  — poll; 404 for unknown.
"""

from __future__ import annotations

import backend.api.routers.system as system_router
from fastapi.testclient import TestClient


# ─────────────────────────────────────────────────────────────────────
# GET /install-gpu-deps/status — fast probe
# ─────────────────────────────────────────────────────────────────────


class TestInstallGPUDepsStatus:
    """GET /api/v1/system/install-gpu-deps/status"""

    def test_status_returns_expected_shape(self, client: TestClient):
        """Status payload always has the documented keys."""
        response = client.get("/api/v1/system/install-gpu-deps/status")
        assert response.status_code == 200
        body = response.json()
        # Required keys (per docstring of install_gpu_deps_status).
        assert "installed" in body
        assert "install_command" in body
        assert "alt_install_command" in body
        assert "recommended_group" in body
        assert "download_estimate_mb" in body
        # Type checks.
        assert isinstance(body["installed"], bool)
        assert isinstance(body["install_command"], str)
        assert isinstance(body["alt_install_command"], str)
        assert isinstance(body["recommended_group"], str)
        assert isinstance(body["download_estimate_mb"], int)

    def test_status_install_command_matches_uv_sync_group_gpu(self, client: TestClient):
        """The recommended install command must mention `uv sync --group gpu`."""
        response = client.get("/api/v1/system/install-gpu-deps/status")
        body = response.json()
        if not body["installed"]:
            # Operator is in the not-yet-installed state — must be told
            # the exact command to run.
            assert "uv sync --group gpu" in body["install_command"], (
                f"Expected 'uv sync --group gpu' in install_command, "
                f"got: {body['install_command']!r}"
            )
        else:
            # Already installed — install_command should be empty.
            assert body["install_command"] == ""

    def test_status_recommended_group_is_gpu(self, client: TestClient):
        """The PEP 735 group name is exposed for tooling that builds the command dynamically."""
        response = client.get("/api/v1/system/install-gpu-deps/status")
        body = response.json()
        assert body["recommended_group"] == "gpu"


# ─────────────────────────────────────────────────────────────────────
# POST /install-gpu-deps — kicks off install
# ─────────────────────────────────────────────────────────────────────


class TestInstallGPUDepsPost:
    """POST /api/v1/system/install-gpu-deps"""

    def test_post_when_already_installed_returns_already_installed(
        self, client: TestClient, monkeypatch
    ):
        """If easyocr is importable, POST short-circuits with status='already_installed'."""
        # Force the probe to say "installed" without requiring the
        # actual torch + easyocr wheels.
        monkeypatch.setattr(system_router, "_probe_easyocr", lambda: True)

        response = client.post("/api/v1/system/install-gpu-deps")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "already_installed"
        assert body["job_id"] == ""

    def test_post_when_not_installed_returns_started_or_409(
        self, client: TestClient, monkeypatch
    ):
        """When easyocr is missing and uv is present, POST returns 'started' (background)."""
        monkeypatch.setattr(system_router, "_probe_easyocr", lambda: False)
        # shutil.which('uv') returns the path on a real dev box.
        # If uv is missing, this test asserts the 503 path instead.

        response = client.post("/api/v1/system/install-gpu-deps")
        if response.status_code == 503:
            # uv missing on this system — fine, the short-circuit
            # is correctly raising HTTPException(503).
            assert "uv" in response.json()["detail"].lower()
            return
        # uv present — job started.
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "started"
        assert body["job_id"]
        assert body["poll_url"].endswith(body["job_id"])

        # Cleanup the background process so test isolation is preserved.
        from backend.api.routers.system import _GPU_INSTALL_JOBS

        _GPU_INSTALL_JOBS.pop(body["job_id"], None)

    def test_post_rejects_second_concurrent_job(
        self, client: TestClient, monkeypatch
    ):
        """Two simultaneous installs return 409 with a pointer to the running job."""
        # Pretend install is needed.
        monkeypatch.setattr(system_router, "_probe_easyocr", lambda: False)

        # First call: start a job (uv may or may not be present).
        first = client.post("/api/v1/system/install-gpu-deps")
        if first.status_code == 503:
            # uv missing — can't really exercise the 409 path here.
            return
        assert first.status_code == 200, first.text
        first_body = first.json()
        if first_body["status"] != "started":
            # short-circuited (already_installed) — nothing to overlap with.
            return

        # Manually mark the job as still running (it was already started in
        # background, but we don't want to wait for the actual uv sync to
        # finish in CI).
        from backend.api.routers.system import _GPU_INSTALL_JOBS

        _GPU_INSTALL_JOBS[first_body["job_id"]]["status"] = "running"

        # Second call: must be rejected with 409.
        second = client.post("/api/v1/system/install-gpu-deps")
        assert second.status_code == 409
        assert first_body["job_id"] in second.json()["detail"]

        # Cleanup.
        _GPU_INSTALL_JOBS.pop(first_body["job_id"], None)


# ─────────────────────────────────────────────────────────────────────
# GET /install-gpu-deps/{job_id} — poll
# ─────────────────────────────────────────────────────────────────────


class TestInstallGPUDepsPoll:
    """GET /api/v1/system/install-gpu-deps/{job_id}"""

    def test_poll_unknown_job_returns_404(self, client: TestClient):
        response = client.get("/api/v1/system/install-gpu-deps/does-not-exist")
        assert response.status_code == 404
        assert "job_id" in response.json()["detail"]

    def test_poll_known_job_returns_metadata(self, client: TestClient):
        from backend.api.routers.system import _GPU_INSTALL_JOBS

        # Inject a fake completed job.
        _GPU_INSTALL_JOBS["test-job-xyz"] = {
            "status": "completed",
            "started_at": 0.0,
            "finished_at": 1.0,
            "returncode": 0,
            "log_file": "/tmp/fake.log",
            "cmd": ["uv", "sync", "--group", "gpu"],
        }
        try:
            response = client.get("/api/v1/system/install-gpu-deps/test-job-xyz")
            assert response.status_code == 200
            body = response.json()
            assert body["job_id"] == "test-job-xyz"
            assert body["status"] == "completed"
            assert body["returncode"] == 0
        finally:
            _GPU_INSTALL_JOBS.pop("test-job-xyz", None)
