"""EventStore – append-only SQLite persistence for the interactive debate mode.

Integrates with the CQRS ProjectorManager to dispatch events to read model
builders after each write. This is the synchronous projector path (v1).

See ADR-001: Thin Events with CQRS Projectors.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from backend.models.debate_event import DebateEvent, normalize_event_type
from backend.models.debate_space import DebateSpace

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path("data/interactive.db")


class EventStore:
    """Append-only event log with thread traversal and projector integration."""

    def __init__(self, db_path: Path | str | None = None, projector_manager=None):
        import threading

        self.db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_db()
        self._migrate_db()
        self._projector_manager = projector_manager
        # Write lock: serialises write operations across async worker threads.
        # SQLite with check_same_thread=False allows concurrent writes which
        # can raise "database is locked" under FastAPI's event loop.
        self._write_lock = threading.Lock()

    def set_projector_manager(self, manager) -> None:
        """Inject the projector manager (called after construction)."""
        self._projector_manager = manager

    def _init_db(self) -> None:
        """Create tables if they don't exist."""
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS debate_spaces (
                space_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                case_id TEXT,
                tenant_id TEXT,
                created_by TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                event_count INTEGER NOT NULL DEFAULT 0,
                fork_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_spaces_tenant ON debate_spaces(tenant_id);
            CREATE INDEX IF NOT EXISTS idx_spaces_case ON debate_spaces(case_id);

            CREATE TABLE IF NOT EXISTS debate_events (
                event_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL REFERENCES debate_spaces(space_id),
                parent_id TEXT REFERENCES debate_events(event_id),
                event_type TEXT NOT NULL,
                actor_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                role TEXT,
                content TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                tokens_input INTEGER,
                tokens_output INTEGER,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_events_space_parent ON debate_events(space_id, parent_id);
            CREATE INDEX IF NOT EXISTS idx_events_space_created ON debate_events(space_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_events_type ON debate_events(event_type);
        """)
        self.conn.commit()

    def _migrate_db(self) -> None:
        """Apply schema migrations for existing databases."""
        # Add case_id column if missing (interactive DMS integration)
        try:
            self.conn.execute("SELECT case_id FROM debate_spaces LIMIT 1")
        except sqlite3.OperationalError:
            self.conn.execute("ALTER TABLE debate_spaces ADD COLUMN case_id TEXT")
            self.conn.execute("CREATE INDEX IF NOT EXISTS idx_spaces_case ON debate_spaces(case_id)")
            self.conn.commit()
            logger.info("Migrated debate_spaces: added case_id column")

    # ── Space CRUD ────────────────────────────────────────────────────────

    def create_space(
        self,
        title: str,
        description: str | None = None,
        case_id: str | None = None,
        tenant_id: str | None = None,
        created_by: str | None = None,
    ) -> DebateSpace:
        space_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        with self._write_lock:
            self.conn.execute(
                """INSERT INTO debate_spaces
                   (space_id, title, description, case_id, tenant_id, created_by, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (space_id, title, description, case_id, tenant_id, created_by, now, now),
            )
            self.conn.commit()

        space = self.get_space(space_id)

        # Emit SpaceCreated event (ADR-001 lifecycle event)
        if space:
            self.append_event(
                space_id=space_id,
                event_type="SpaceCreated",
                actor_type="user",
                actor_id=created_by or "system",
                content=title,
                metadata_json={"description": description},
            )

        return space  # type: ignore[return-value]

    def get_space(self, space_id: str) -> DebateSpace | None:
        row = self.conn.execute(
            "SELECT * FROM debate_spaces WHERE space_id = ?", (space_id,)
        ).fetchone()
        return self._row_to_space(row) if row else None

    def update_space_counters(self, space_id: str) -> None:
        """Recalculate event_count and fork_count from the event log.

        ``event_count`` is the total number of events in the space (including
        the SpaceCreated lifecycle event). ``fork_count`` counts parent events
        that have more than one direct child — i.e. points in the tree where
        the discussion branched. Events with ``parent_id IS NULL`` (roots)
        are never forks.
        """
        row = self.conn.execute(
            """SELECT
                (SELECT COUNT(*) FROM debate_events WHERE space_id = ?) AS event_count,
                COALESCE((
                    SELECT COUNT(*) FROM (
                        SELECT parent_id
                        FROM debate_events
                        WHERE space_id = ? AND parent_id IS NOT NULL
                        GROUP BY parent_id
                        HAVING COUNT(*) > 1
                    )
                ), 0) AS fork_count""",
            (space_id, space_id),
        ).fetchone()
        if row:
            with self._write_lock:
                self.conn.execute(
                    """UPDATE debate_spaces
                       SET event_count = ?, fork_count = ?, updated_at = ?
                       WHERE space_id = ?""",
                    (row["event_count"], row["fork_count"], datetime.now(UTC).isoformat(), space_id),
                )
                self.conn.commit()

    def update_space(
        self,
        space_id: str,
        title: str | None = None,
        description: str | None = None,
        case_id: str | None = None,
    ) -> DebateSpace | None:
        """Update mutable fields of a debate space.

        Only updates fields that are explicitly provided (not None).
        Pass case_id="" to unlink a space from its case.
        """
        space = self.get_space(space_id)
        if not space:
            return None

        updates = []
        params = []
        if title is not None:
            updates.append("title = ?")
            params.append(title)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if case_id is not None:
            updates.append("case_id = ?")
            params.append(case_id if case_id else None)

        if not updates:
            return space

        updates.append("updated_at = ?")
        params.append(datetime.now(UTC).isoformat())
        params.append(space_id)

        with self._write_lock:
            self.conn.execute(
                f"UPDATE debate_spaces SET {', '.join(updates)} WHERE space_id = ?",
                params,
            )
            self.conn.commit()

        return self.get_space(space_id)

    def list_spaces(
        self,
        tenant_id: str | None = None,
        case_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[DebateSpace]:
        query = "SELECT * FROM debate_spaces WHERE 1=1"
        params: list = []
        if tenant_id:
            query += " AND tenant_id = ?"
            params.append(tenant_id)
        if case_id:
            query += " AND case_id = ?"
            params.append(case_id)
        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_space(r) for r in rows]

    # ── Event CRUD (append-only) ──────────────────────────────────────────

    def append_event(
        self,
        space_id: str,
        event_type: str,
        actor_type: str,
        actor_id: str,
        content: str | dict = "",
        parent_id: str | None = None,
        role: str | None = None,
        metadata_json: dict | None = None,
        tokens_input: int | None = None,
        tokens_output: int | None = None,
    ) -> DebateEvent:
        """Append a new event to the log. Never updates existing events.

        After writing, dispatches to the ProjectorManager (CQRS) and
        updates space counters. Thread-safe via ``_write_lock``.
        """
        # Normalize legacy event types (ADR-001 migration)
        event_type = normalize_event_type(event_type)

        event_id = str(uuid.uuid4())
        now = datetime.now(UTC).isoformat()
        meta = json.dumps(metadata_json or {})
        content_str = json.dumps(content) if isinstance(content, dict) else content

        with self._write_lock:
            self.conn.execute(
                """INSERT INTO debate_events
                   (event_id, space_id, parent_id, event_type, actor_type, actor_id,
                    role, content, metadata_json, tokens_input, tokens_output, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id, space_id, parent_id, event_type, actor_type, actor_id,
                 role, content_str, meta, tokens_input, tokens_output, now),
            )
            self.conn.commit()

        event = self.get_event(event_id)
        if not event:
            raise RuntimeError(f"Failed to retrieve event {event_id} after insert")

        # Update space counters
        self.update_space_counters(space_id)

        # Dispatch to CQRS projectors (synchronous in v1)
        if self._projector_manager:
            try:
                self._projector_manager.handle_event(event)
            except Exception:
                logger.exception("Projector dispatch failed for event %s", event_id)

        return event

    def get_event(self, event_id: str) -> DebateEvent | None:
        row = self.conn.execute(
            "SELECT * FROM debate_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        return self._row_to_event(row) if row else None

    def get_children(self, space_id: str, parent_id: str | None = None) -> list[DebateEvent]:
        """Get direct children of a parent event (or root events if parent_id=None)."""
        rows = self.conn.execute(
            """SELECT * FROM debate_events
               WHERE space_id = ? AND parent_id IS ?
               ORDER BY created_at ASC""",
            (space_id, parent_id),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_thread(
        self,
        space_id: str,
        root_event_id: str,
        max_depth: int | None = None,
    ) -> list[DebateEvent]:
        """Trace a full thread from root_event_id downwards (BFS).

        Fetches all events in the space in a single query and builds the
        tree in memory, avoiding N+1 per-node ``get_event``/``get_children``
        calls.
        """
        # Bulk-fetch all events for the space once, build a parent→children index.
        all_rows = self.conn.execute(
            """SELECT * FROM debate_events WHERE space_id = ?
               ORDER BY created_at ASC""",
            (space_id,),
        ).fetchall()
        events_by_id: dict[str, DebateEvent] = {}
        children_by_parent: dict[str | None, list[DebateEvent]] = {}
        for row in all_rows:
            evt = self._row_to_event(row)
            events_by_id[evt.event_id] = evt
            children_by_parent.setdefault(evt.parent_id, []).append(evt)

        result: list[DebateEvent] = []
        queue: list[tuple[str, int]] = [(root_event_id, 0)]
        visited: set[str] = set()

        while queue:
            current_id, depth = queue.pop(0)
            if current_id in visited:
                continue
            if max_depth is not None and depth > max_depth:
                continue
            visited.add(current_id)

            event = events_by_id.get(current_id)
            if event:
                result.append(event)
                for child in children_by_parent.get(current_id, []):
                    queue.append((child.event_id, depth + 1))

        return result

    def get_full_tree(self, space_id: str) -> list[DebateEvent]:
        """Get all events in a space ordered chronologically."""
        rows = self.conn.execute(
            """SELECT * FROM debate_events
               WHERE space_id = ?
               ORDER BY created_at ASC""",
            (space_id,),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_events_by_type(
        self,
        space_id: str,
        event_type: str,
        limit: int = 100,
    ) -> list[DebateEvent]:
        # Normalize legacy event types
        event_type = normalize_event_type(event_type)
        rows = self.conn.execute(
            """SELECT * FROM debate_events
               WHERE space_id = ? AND event_type = ?
               ORDER BY created_at DESC LIMIT ?""",
            (space_id, event_type, limit),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_token_usage(self, space_id: str) -> dict[str, int]:
        """Aggregate token usage across all events in a space."""
        row = self.conn.execute(
            """SELECT
                COALESCE(SUM(tokens_input), 0) as total_input,
                COALESCE(SUM(tokens_output), 0) as total_output
               FROM debate_events WHERE space_id = ?""",
            (space_id,),
        ).fetchone()
        return {
            "total_input": row["total_input"] if row else 0,
            "total_output": row["total_output"] if row else 0,
        }

    # ── Serialisation helpers ─────────────────────────────────────────────

    def _row_to_space(self, row: sqlite3.Row) -> DebateSpace:
        return DebateSpace(
            space_id=row["space_id"],
            title=row["title"],
            description=row["description"],
            case_id=row["case_id"],
            tenant_id=row["tenant_id"],
            created_by=row["created_by"],
            status=row["status"],
            event_count=row["event_count"],
            fork_count=row["fork_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _row_to_event(self, row: sqlite3.Row) -> DebateEvent:
        meta_raw = row["metadata_json"]
        meta = json.loads(meta_raw) if isinstance(meta_raw, str) else meta_raw
        content_raw = row["content"]
        # Try to parse content as JSON (for dict payloads)
        try:
            content = json.loads(content_raw) if content_raw.startswith(("{", "[")) else content_raw
        except (json.JSONDecodeError, AttributeError):
            content = content_raw

        return DebateEvent(
            event_id=row["event_id"],
            space_id=row["space_id"],
            parent_id=row["parent_id"],
            event_type=row["event_type"],
            actor_type=row["actor_type"],
            actor_id=row["actor_id"],
            role=row["role"],
            content=content,
            metadata_json=meta,
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def close(self) -> None:
        self.conn.close()
