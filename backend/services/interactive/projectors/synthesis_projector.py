"""Synthesis Projector – prepares the final report output.

Read Model:
    In-memory / temporary files (triggered on MilestoneReached).

Logic:
    On MilestoneReached (type: consensus or report_generated), traverses
    the main thread of the debate tree, collects free-text content, and
    renders into clean Markdown.

See ADR-001, Projector 4.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from backend.models.debate_event import DebateEvent
from backend.services.interactive.projectors.base import BaseProjector

logger = logging.getLogger(__name__)


class SynthesisProjector(BaseProjector):
    """Preares the final Markdown report when a consensus milestone is reached."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._init_tables()

    @property
    def name(self) -> str:
        return "synthesis"

    def _init_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS synthesis_reports (
                report_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                milestone_event_id TEXT NOT NULL,
                format TEXT NOT NULL DEFAULT 'markdown',
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_synthesis_space ON synthesis_reports(space_id);
        """)
        self.conn.commit()

    def handles_event_type(self, event_type: str) -> bool:
        return event_type == "MilestoneReached"

    def handle_event(self, event: DebateEvent) -> None:
        milestone_type = event.metadata_json.get("milestone_type", "")

        if milestone_type not in ("consensus", "report_generated"):
            return  # Only synthesize on consensus or explicit report request

        logger.info(
            "Synthesis triggered for space %s on milestone %s",
            event.space_id,
            milestone_type,
        )

        # Collect the main thread (traverse from root)
        root_event = self._find_root(event.space_id)
        if not root_event:
            logger.warning("No root event found for space %s", event.space_id)
            return

        # Build Markdown from the main thread
        markdown = self._render_markdown(event.space_id, root_event.event_id)

        # Store the report
        now = datetime.now(UTC).isoformat()
        report_id = f"report-{event.event_id[:8]}"

        self.conn.execute(
            """INSERT OR REPLACE INTO synthesis_reports
               (report_id, space_id, milestone_event_id, format, content, created_at)
               VALUES (?, ?, ?, 'markdown', ?, ?)""",
            (report_id, event.space_id, event.event_id, markdown, now),
        )
        self.conn.commit()

        logger.info("Synthesis report %s generated for space %s", report_id, event.space_id)

    def _find_root(self, space_id: str) -> DebateEvent | None:
        """Find the root event (SpaceCreated) for a space."""
        row = self.conn.execute(
            """SELECT * FROM debate_events
               WHERE space_id = ? AND parent_id IS NULL
               ORDER BY created_at ASC LIMIT 1""",
            (space_id,),
        ).fetchone()
        if row:
            from backend.models.debate_event import DebateEvent as DebateEventModel
            return DebateEventModel(
                event_id=row["event_id"],
                space_id=row["space_id"],
                parent_id=row["parent_id"],
                event_type=row["event_type"],
                actor_type=row["actor_type"],
                actor_id=row["actor_id"],
                role=row["role"],
                content=row["content"],
                metadata_json=row["metadata_json"] if isinstance(row["metadata_json"], dict) else {},
                tokens_input=row["tokens_input"],
                tokens_output=row["tokens_output"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
        return None

    def _render_markdown(self, space_id: str, root_event_id: str) -> str:
        """Traverse the main thread and render as Markdown."""
        lines: list[str] = []
        lines.append("# Debate Report\n")

        # BFS traversal of the main thread
        queue = [root_event_id]
        visited: set[str] = set()
        step = 0

        while queue:
            event_id = queue.pop(0)
            if event_id in visited:
                continue
            visited.add(event_id)

            row = self.conn.execute(
                "SELECT * FROM debate_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if not row:
                continue

            step += 1
            actor = row["actor_id"]
            role = row["role"]
            content = row["content"]
            event_type = row["event_type"]

            # Format the step
            role_str = f" ({role})" if role else ""
            lines.append(f"## Step {step}: {actor}{role_str} [{event_type}]\n")
            lines.append(f"{content}\n")

            # Get children for main thread (first child only for linear view)
            children = self.conn.execute(
                """SELECT event_id FROM debate_events
                   WHERE space_id = ? AND parent_id = ?
                   ORDER BY created_at ASC""",
                (space_id, event_id),
            ).fetchall()

            if children:
                queue.append(children[0]["event_id"])
                # Note: side branches are ignored in main thread synthesis

        return "\n".join(lines)

    # ── Read Model Queries ────────────────────────────────────────────────

    def get_latest_report(self, space_id: str) -> dict | None:
        """Return the most recent synthesis report for a space."""
        row = self.conn.execute(
            """SELECT * FROM synthesis_reports
               WHERE space_id = ?
               ORDER BY created_at DESC LIMIT 1""",
            (space_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_reports(self, space_id: str) -> list[dict]:
        """Return all synthesis reports for a space."""
        rows = self.conn.execute(
            """SELECT * FROM synthesis_reports
               WHERE space_id = ?
               ORDER BY created_at DESC""",
            (space_id,),
        ).fetchall()
        return [dict(r) for r in rows]
