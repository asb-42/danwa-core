# Installing & Running danwa-core

> **Quickstart guide** for the `danwa-core` backend + orchestrator.
> For the full architecture and design decisions see [`README.md`](README.md).

This document is part of the multi-repo orchestration described in
[`plans/2026-06-22_repo-setup-orchestration.md`](../../danwa/plans/2026-06-22_repo-setup-orchestration.md)
(Phase 9).

---

## Prerequisites

| Tool | Min version | How to install |
|------|-------------|----------------|
| **Python** | 3.11+ | `nvm install 22` for node, or your distro's package manager |
| **uv** | latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **Node.js** | 22.x | only required if you start sibling frontends via this orchestrator |
| **npm** | bundled with Node | only required if you start sibling frontends |
| **curl** | any | usually pre-installed |
| **git** | any | usually pre-installed |

The shared bash library `libdanwa.sh` lives in
[`scripts/libdanwa.sh`](scripts/libdanwa.sh) (vendored copy at
[`.lib/libdanwa.sh`](.lib/libdanwa.sh) after `setup.sh`).

**Current `libdanwa.sh` version:** **v1.0.0** (see `LIBDANWA_VERSION`
at the top of the library).

---

## Quickstart

Three commands get you running from a fresh clone:

```bash
# 1. Install dependencies (uv sync, vendoring libdanwa.sh)
bash setup.sh

# 2. Start the backend (uvicorn on port 8000)
bash manage.sh start

# 3. Open the API docs
xdg-open http://localhost:8000/docs  # or visit it in your browser
```

The backend will start on **http://localhost:8000**. The Swagger UI is at
**/docs**, the OpenAPI schema at **/openapi.json**.

For the **interactive dashboard**:

```bash
bash manage.sh dashboard
```

For a quick **status overview** (human + machine-readable):

```bash
bash manage.sh status              # human-readable
bash manage.sh status --json       # JSON for danwa-studio SystemManagementView
```

---

## Sibling-Setup (Full Stack — Orchestrator Mode)

`danwa-core/manage.sh` is the **central orchestrator**. When started, it
auto-detects the sibling frontends in the parent directory and starts
them too.

```
parent-dir/
├── danwa-core/        # Backend (uvicorn + FastAPI)  ← THIS REPO
├── danwa/             # User-frontend (Vite, port 5173)
└── danwa-studio/      # Admin/dev-frontend (Vite, port 5174)
```

### One-stop full-stack setup

```bash
mkdir ~/danwa-stack && cd ~/danwa-stack
git clone https://github.com/asb-42/danwa-core.git
git clone https://github.com/asb-42/danwa.git
git clone https://github.com/asb-42/danwa-studio.git

cd danwa-core
bash setup.sh        # installs uv, Python deps, vendors libdanwa.sh
bash manage.sh start # starts backend + auto-detects danwa + danwa-studio
```

Default ports:

| Component | Port | URL |
|-----------|------|-----|
| danwa-core backend (API + docs) | 8000 | http://localhost:8000/docs |
| danwa (user-app) | 5173 | http://localhost:5173 |
| danwa-studio (admin/dev) | 5174 | http://localhost:5174 |

### Watcher-Loop (auto-respawn on crash)

By default the backend stops when you `bash manage.sh stop`. To make it
auto-respawn after crashes, enable the watcher loop:

```bash
BACKEND_WATCHER_ENABLED=1 bash manage.sh start
```

The watcher polls every 2 s (configurable via `BACKEND_WATCHER_INTERVAL`)
and re-launches the backend if the PID dies.

### Graceful restart via Studio

`danwa-studio` has a **"Restart backend" button** that calls:

```
POST http://localhost:8000/api/v1/system/restart-backend
```

The endpoint ([`backend/api/routers/system_control.py`](backend/api/routers/system_control.py))
sends `SIGTERM` to the running uvicorn process after 200 ms. The
`danwa-core/manage.sh` watcher loop detects the death and respawns the
backend — **no manual restart needed**.

Other useful endpoints:

| Endpoint | Purpose |
|----------|---------|
| `GET  /api/v1/system/status` | Health + pids + uptime (always 200) |
| `POST /api/v1/system/stop-backend` | Graceful stop (no auto-respawn) |
| `POST /api/v1/system/restart-backend` | Graceful restart (with watcher) |
| `POST /api/v1/system/reload-config` | Reload LLM profiles / prompts |

---

## Shared Library — `libdanwa.sh`

All `setup.sh` and `manage.sh` scripts in the `danwa-*` repo family
source a shared bash library called **`libdanwa.sh`**
([`scripts/libdanwa.sh`](scripts/libdanwa.sh)). It provides:

- Colorised logging (`log_info`, `log_ok`, `log_warn`, `log_error`)
- Process management (`pid_running`, `kill_pid`, `wait_for_url`, `wait_for_port`)
- Toolchain checks (`check_python_version`, `check_uv_installed`, `check_node_version`)
- Repo-config loading (`load_repo_config`, `discover_siblings`)

**Current version:** **v1.0.0**.

On first `bash setup.sh`, the library is **vendored** into
[`.lib/libdanwa.sh`](.lib/libdanwa.sh) for offline operation. To update
to a newer release:

```bash
bash setup.sh            # re-vendors if the source-of-truth file changed
```

The `manage.sh` shim refuses to start if the vendored library is on an
incompatible major version (anything not matching `v1.*`).

---

## Troubleshooting

### `ERROR: libdanwa.sh not found. Run setup.sh first.`

You tried to run `manage.sh` before `setup.sh`. Fix:

```bash
bash setup.sh
```

If `setup.sh` itself can't find `libdanwa.sh`:

```bash
cp scripts/libdanwa.sh .lib/libdanwa.sh
```

### `uv: command not found`

Install Astral's `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

### Port 8000 already in use

Another process is bound to 8000. Either stop it or pick a different port:

```bash
BACKEND_PORT=8080 bash manage.sh start
```

### Backend starts but Studio can't reach it

The orchestrator binds to `0.0.0.0` by default. If you're behind a
reverse proxy, ensure it forwards `/api/v1/system/*` correctly. Check
with `curl http://localhost:8000/api/v1/system/status`.

### `ModuleNotFoundError: No module named 'backend'`

The `PYTHONPATH` is wrong. The shim sets it via `setup.sh`, but if you
run `uv run` manually:

```bash
PYTHONPATH=. uv run uvicorn backend.main:app --host 0.0.0.0 --port 8000
```

### Backend keeps respawning after I `kill` it

You have `BACKEND_WATCHER_ENABLED=1` set. Either disable the watcher:

```bash
BACKEND_WATCHER_ENABLED=0 bash manage.sh start
```

Or use the **Studio Restart button** (graceful, watcher-aware) instead
of `kill -9`.

---

## Files in this repo

| Path | Purpose |
|------|---------|
| [`setup.sh`](setup.sh) | Thin shim → [`repo-templates/setup.sh`](repo-templates/setup.sh) |
| [`manage.sh`](manage.sh) | Thin shim → [`repo-templates/manage.sh`](repo-templates/manage.sh) |
| [`repo-templates/manage.sh`](repo-templates/manage.sh) | Canonical manage template (orchestrator, watcher-loop, status --json) |
| [`repo-templates/setup.sh`](repo-templates/setup.sh) | Canonical setup template |
| [`.danwa-config`](.danwa-config) | Repo metadata (BACKEND_PORT=8000, SIBLINGS=(danwa danwa-studio), …) |
| [`scripts/libdanwa.sh`](scripts/libdanwa.sh) | Shared bash library v1.0.0 |
| [`.lib/libdanwa.sh`](.lib/libdanwa.sh) | Vendored copy (created by `setup.sh`) |
| [`backend/api/routers/system_control.py`](backend/api/routers/system_control.py) | Studio restart endpoints |
| [`tests/scripts/`](tests/scripts/) | bats test suite (110+ tests for setup+manage+ci) |
| [`tests/backend/`](tests/backend/) | pytest regression suite (369 tests) |

---

## See also

- [`README.md`](README.md) — project overview, architecture, API surface
- [`plans/2026-06-22_repo-setup-orchestration.md`](../../danwa/plans/2026-06-22_repo-setup-orchestration.md) — multi-repo orchestration plan (Phases 1–11)
- `../danwa/INSTALL.md` — install guide for the user-app frontend (sibling)
- `../danwa-studio/INSTALL.md` — install guide for the admin/dev frontend (sibling)