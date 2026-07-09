"""Migration v025: Create debate_spaces and debate_events tables for Interactive Mode.

Run: python -m backend.migrations.v025_interactive_debate
"""

from __future__ import annotations

import sqlite3
from pathlib import Path


def migrate(db_path: Path | str) -> None:
    """Create the interactive debate tables."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        -- ═══════════════════════════════════════════════════════════════════
        -- DebateSpaces: root aggregate for interactive debates
        -- ═══════════════════════════════════════════════════════════════════
        CREATE TABLE IF NOT EXISTS debate_spaces (
            space_id    TEXT PRIMARY KEY,
            title       TEXT NOT NULL,
            description TEXT,
            project_id  TEXT,
            tenant_id   TEXT,
            created_by  TEXT,
            status      TEXT NOT NULL DEFAULT 'open',
            event_count INTEGER NOT NULL DEFAULT 0,
            fork_count  INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_spaces_tenant
            ON debate_spaces(tenant_id);
        CREATE INDEX IF NOT EXISTS idx_spaces_project
            ON debate_spaces(project_id);

        -- ═══════════════════════════════════════════════════════════════════
        -- DebateEvents: append-only event log (the core of Event Sourcing)
        -- ═══════════════════════════════════════════════════════════════════
        CREATE TABLE IF NOT EXISTS debate_events (
            event_id      TEXT PRIMARY KEY,
            space_id      TEXT NOT NULL REFERENCES debate_spaces(space_id),
            parent_id     TEXT REFERENCES debate_events(event_id),
            event_type    TEXT NOT NULL,
            actor_type    TEXT NOT NULL,
            actor_id      TEXT NOT NULL,
            role          TEXT,
            content       TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            tokens_input  INTEGER,
            tokens_output INTEGER,
            created_at    TEXT NOT NULL
        );

        -- Fast thread loading: all children of a parent in one space
        CREATE INDEX IF NOT EXISTS idx_events_space_parent
            ON debate_events(space_id, parent_id);

        -- Chronological ordering within a space
        CREATE INDEX IF NOT EXISTS idx_events_space_created
            ON debate_events(space_id, created_at);

        -- Filter by event type (e.g., find all agent_speech events)
        CREATE INDEX IF NOT EXISTS idx_events_type
            ON debate_events(event_type);
    """)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "data/interactive.db"
    migrate(path)
    print(f"✓ Migration v025 applied to {path}")
