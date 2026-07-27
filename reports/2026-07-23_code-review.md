# Code Review: danwa-core

**Date:** 2026-07-23  
**Reviewer:** Principal Staff Engineer  
**Scope:** Auth, multi-tenancy, LLM service, A2A, module installer, persistence, interactive mode

---

## 1. Executive Summary

The danwa-core backend is architecturally sound with several genuinely well-engineered security mechanisms: the AST-walking `safe_eval` (replacing dangerous `eval()`), Fernet envelope encryption for BYOK keys, DNS-rebinding SSRF defence in the A2A validator, and dev-mode auth guardrails. The most critical risk identified is in the **module installer** — a Zip Slip vulnerability in `install_from_url` combined with an SSRF hole (`urllib.request.urlopen` with no URL validation) and path traversal via untrusted manifest `file_entry["path"]` values. These three issues together allow a malicious module ZIP to write arbitrary files to the host filesystem. The persistence layer's `check_same_thread=False` pattern across all SQLite stores is a latent concurrency hazard under FastAPI's async event loop.

---

## 2. Critical & High Severity Issues (Must Fix)

### 2.1 Zip Slip — Arbitrary File Write via Malicious ZIP — [Security]

- **Location:** `backend/modules/installer.py:474` — `install_from_url()`
- **The Problem:** `zf.extractall(tmp_dir)` extracts ZIP entries without validating that each entry's path stays within `tmp_dir`. A ZIP entry with a name like `../../etc/cron.d/malicious` or `../../../data/auth.db` will write outside the intended directory, achieving arbitrary file write on the host. This is the classic CVE-2018-1000613 / Zip Slip pattern.
- **The Fix:**

```python
def _safe_extractall(zf: zipfile.ZipFile, target_dir: Path) -> None:
    """Extract all entries, rejecting path-traversal names (Zip Slip defence)."""
    target_dir = target_dir.resolve()
    for info in zf.infolist():
        # Resolve the entry path and verify it stays inside target_dir.
        member_path = (target_dir / info.filename).resolve()
        if not str(member_path).startswith(str(target_dir) + os.sep):
            raise InstallationError(
                f"Refusing to extract '{info.filename}' — path traversal attempt"
            )
    zf.extractall(target_dir)
```

Then replace `zf.extractall(tmp_dir)` with `_safe_extractall(zf, tmp_dir)`.

### 2.2 SSRF in `install_from_url` — No URL Validation — [Security]

- **Location:** `backend/modules/installer.py:458` — `urllib.request.urlopen(url, timeout=60)`
- **The Problem:** The `url` parameter is passed directly to `urllib.request.urlopen()` with no scheme validation, no private-IP blocking, and no DNS-rebinding defence. An attacker who can influence the `url` parameter (e.g. via the module service's `install_from_url` API or catalog download URL) can make the server fetch `http://169.254.169.254/latest/meta-data/` (AWS metadata), `http://127.0.0.1:6379/` (Redis), or any internal service. The A2A validator (`backend/a2a/url_validator.py`) already implements exactly the right defence — this codepath doesn't use it.
- **The Fix:**

```python
from backend.a2a.url_validator import validate_a2a_url

def install_from_url(self, url: str) -> InstallationReport:
    """Install a module from a ZIP URL."""
    import urllib.request

    # Reuse the existing SSRF defence from the A2A validator.
    try:
        url = validate_a2a_url(url, allow_private_ips=False)
    except Exception as e:
        return InstallationReport(
            status="error",
            module_id="<unknown>",
            version="0.0.0",
            errors=[f"URL validation failed: {e}"],
        )

    try:
        with urllib.request.urlopen(url, timeout=60) as resp:
            data = resp.read()
    # ... rest unchanged
```

### 2.3 Path Traversal via Manifest `file_entry["path"]` — [Security]

- **Location:** `backend/modules/installer.py:127` — `_register_in_db()`
- **The Problem:** `fpath = self.modules_dir / module_id / file_entry["path"]` uses an untrusted string from the module manifest to construct a filesystem path. A manifest with `"path": "../../data/auth.db"` will resolve to a path outside the module directory. The file is then read with `fpath.read_text()` — while this particular line only *reads*, the path is also stored in the DB translation cache, and the same unsanitized path pattern appears in `validation.py:322` (`verify_checksums`). A malicious module could read arbitrary files from the host.
- **The Fix:**

```python
def _safe_module_path(modules_dir: Path, module_id: str, relative_path: str) -> Path:
    """Resolve a manifest file path, rejecting path traversal."""
    base = (modules_dir / module_id).resolve()
    resolved = (base / relative_path).resolve()
    if not str(resolved).startswith(str(base) + os.sep) and resolved != base:
        raise InstallationError(
            f"Path traversal in manifest: '{relative_path}' escapes module directory"
        )
    return resolved
```

Use this helper everywhere a manifest `path` or `profile_file` is joined against the filesystem.

### 2.4 SQLite `check_same_thread=False` with No Serialisation — [Concurrency]

- **Location:** Every persistence store (`tenant_store.py:25`, `user_key_store.py:212`, `event_store.py:32`, `case_store.py`, `tag_store.py`, `membership_store.py`, `debate_store.py`, etc.)
- **The Problem:** All stores open SQLite connections with `check_same_thread=False` and share a single `self.conn` across FastAPI's async worker threads. SQLite allows concurrent reads but serialises writes via file locking. With WAL mode, concurrent writes from different threads can raise `sqlite3.OperationalError: database is locked` — which is currently unhandled in most stores (no retry, no `busy_timeout` beyond the `timeout=10` constructor arg). Under load, this causes 500 errors on write-heavy endpoints.
- **The Fix:** Use a `threading.Lock` per store instance, or switch to a connection-per-request pattern. The minimal fix:

```python
import threading

class EventStore:
    def __init__(self, ...):
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._write_lock = threading.Lock()

    def append_event(self, ...):
        with self._write_lock:
            self.conn.execute(...)
            self.conn.commit()
```

---

## 3. Architectural & Design Improvements (Should Fix)

### 3.1 Interactive Router Creates `EventEmbeddingStore` on Every Event Append — [Performance]

- **Location:** `backend/api/routers/interactive.py:159-160` — `append_event()`
- **The Problem:** Every call to `POST /interactive/spaces/{id}/events` constructs `EventEmbeddingStore()`, which calls `chromadb.PersistentClient()` and `get_or_create_collection()`. This is a heavyweight initialisation (opens on-disk SQLite + HNSW index) happening on every single event append. Under active debate, this adds 10-50ms per event.
- **The Fix:** Make `EventEmbeddingStore` a module-level singleton (like `_store` and `_projector_manager`):

```python
_embedding_store: EventEmbeddingStore | None = None

def _get_embedding_store() -> EventEmbeddingStore:
    global _embedding_store
    if _embedding_store is None:
        _embedding_store = EventEmbeddingStore()
    return _embedding_store
```

### 3.2 `get_current_user` Circular Self-Import Pattern — [Architecture]

- **Location:** `backend/api/deps.py:492, 520, 541, 573` — `from backend.api.deps import get_current_user as _gcu`
- **The Problem:** Functions in `deps.py` import `get_current_user` from `deps.py` itself via a lazy runtime import. This works but is fragile — it exists only because `get_current_user` is defined *below* its callers in the file. It makes the dependency graph opaque and prevents static analysis tools from tracing the dependency.
- **The Fix:** Move `get_current_user` above the tenant/context functions, or extract the auth dependency into a separate `backend/api/auth_deps.py` module that both `deps.py` and the routers import from.

### 3.3 Interactive Mode Fire-and-Forget `asyncio.create_task` for Event Bus Publish — [Resilience]

- **Location:** `backend/api/routers/interactive.py:155` — `asyncio.create_task(bus.publish(...))`
- **The Problem:** The event bus publish (which triggers SSE delivery to all connected clients) is dispatched as a fire-and-forget task. If the task raises (e.g. Redis is down), the exception is silently swallowed by the event loop's default exception handler — the client never receives the event, and no error is surfaced. The `append_event` endpoint returns 200 as if everything succeeded.
- **The Fix:** At minimum, attach a done callback that logs failures:

```python
task = asyncio.create_task(bus.publish(stream_name, {"event_id": event.event_id}))
task.add_done_callback(lambda t: t.exception() and logger.error("Event bus publish failed: %s", t.exception()))
```

For production, consider a durable outbox pattern so events survive a bus outage.

---

## 4. Performance & Resilience Optimizations (Nice to Have)

- **`get_case_dir` filesystem scan:** `backend/api/deps.py:166` — `get_case_dir()` iterates `CASE_BASE.iterdir()` scanning for a matching case directory on every call when the project store misses. This is O(tenants) filesystem stat calls per request. Consider caching the case→path mapping.

- **`SynthesisProjector._render_markdown` N+1 query:** `backend/services/interactive/projectors/synthesis_projector.py:132` — the BFS tree walk executes a `SELECT ... WHERE event_id = ?` query per event and a `SELECT ... WHERE parent_id = ?` per level. For a 50-event tree, this is ~100 queries. Use a single `SELECT * FROM debate_events WHERE space_id = ? ORDER BY created_at` and build the tree in Python.

- **`EventStore.get_thread` recursive `get_event` calls:** `backend/persistence/event_store.py:252` — `get_thread()` calls `get_event()` per node in a BFS loop, each issuing a separate `SELECT`. This is N queries for an N-node thread. Fetch all children at once with `get_children()` (already done for the first level) and build the tree in memory.

---

## 5. Clarifying Questions for the Author

1. **Module installer attack surface:** Is `install_from_url` exposed via an authenticated API endpoint, or is it only called from internal/admin scripts? If it's API-accessible, the SSRF + Zip Slip + path traversal combination is a critical remote code execution vector.
2. **SQLite concurrency expectations:** What is the expected concurrent write load? If debates are primarily single-user with occasional agent writes, the `check_same_thread=False` pattern may be acceptable. If multiple users can append events to the same space simultaneously, the write-lock fix is urgent.
3. **Event embedding failure tolerance:** When `EventEmbeddingStore` fails (e.g. ChromaDB not installed, disk full), should the event append still succeed (current behaviour: best-effort, logged warning), or should it be a hard failure?
