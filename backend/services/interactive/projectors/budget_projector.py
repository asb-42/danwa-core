"""Audit & Budget Projector – token tracking and cost control.

Read Model:
    token_budgets aggregate table.

Logic:
    Sums metadata.tokens_input / tokens_output on each event.
    Groups by branch, actor, and space for comparative analysis.

See ADR-001, Projector 3.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import UTC, datetime

from backend.models.debate_event import DebateEvent
from backend.services.interactive.projectors.base import BaseProjector

logger = logging.getLogger(__name__)

# Approximate costs per 1k tokens (update as pricing changes)
_COST_PER_1K_TOKENS = {
    "input": 0.003,
    "output": 0.015,
}


class BudgetProjector(BaseProjector):
    """Tracks token usage and cost per space, branch, and actor."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._init_tables()

    @property
    def name(self) -> str:
        return "audit_budget"

    def _init_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS token_budgets (
                id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                branch_id TEXT,
                actor_id TEXT,
                tokens_input INTEGER NOT NULL DEFAULT 0,
                tokens_output INTEGER NOT NULL DEFAULT 0,
                total_cost_usd REAL NOT NULL DEFAULT 0.0,
                event_count INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_budget_space ON token_budgets(space_id);
            CREATE INDEX IF NOT EXISTS idx_budget_branch ON token_budgets(space_id, branch_id);
        """)
        self.conn.commit()

    def handles_event_type(self, event_type: str) -> bool:
        # Handle all events that might carry token info
        return True

    def handle_event(self, event: DebateEvent) -> None:
        tokens_in = event.tokens_input or event.metadata_json.get("tokens_input", 0) or 0
        tokens_out = event.tokens_output or event.metadata_json.get("tokens_output", 0) or 0

        if tokens_in == 0 and tokens_out == 0:
            return  # No token data to track

        cost = (
            (tokens_in / 1000) * _COST_PER_1K_TOKENS["input"]
            + (tokens_out / 1000) * _COST_PER_1K_TOKENS["output"]
        )

        now = datetime.now(UTC).isoformat()

        # Upsert budget record for this space + actor
        existing = self.conn.execute(
            """SELECT id FROM token_budgets
               WHERE space_id = ? AND actor_id = ? AND (branch_id IS ?)""",
            (event.space_id, event.actor_id, None),
        ).fetchone()

        if existing:
            self.conn.execute(
                """UPDATE token_budgets
                   SET tokens_input = tokens_input + ?,
                       tokens_output = tokens_output + ?,
                       total_cost_usd = total_cost_usd + ?,
                       event_count = event_count + 1,
                       updated_at = ?
                   WHERE id = ?""",
                (tokens_in, tokens_out, cost, now, existing["id"]),
            )
        else:
            budget_id = str(uuid.uuid4())
            self.conn.execute(
                """INSERT INTO token_budgets
                   (id, space_id, branch_id, actor_id, tokens_input, tokens_output,
                    total_cost_usd, event_count, updated_at)
                   VALUES (?, ?, NULL, ?, ?, ?, ?, 1, ?)""",
                (budget_id, event.space_id, event.actor_id, tokens_in, tokens_out, cost, now),
            )

        self.conn.commit()

    # ── Read Model Queries ────────────────────────────────────────────────

    def get_budget(self, space_id: str) -> list[dict]:
        """Return token budgets per actor for a space."""
        rows = self.conn.execute(
            """SELECT * FROM token_budgets
               WHERE space_id = ?
               ORDER BY total_cost_usd DESC""",
            (space_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_total_cost(self, space_id: str) -> dict:
        """Return aggregated token usage and cost for a space."""
        row = self.conn.execute(
            """SELECT
                COALESCE(SUM(tokens_input), 0) as total_input,
                COALESCE(SUM(tokens_output), 0) as total_output,
                COALESCE(SUM(total_cost_usd), 0.0) as total_cost,
                COALESCE(SUM(event_count), 0) as total_events
               FROM token_budgets WHERE space_id = ?""",
            (space_id,),
        ).fetchone()
        return dict(row) if row else {
            "total_input": 0,
            "total_output": 0,
            "total_cost": 0.0,
            "total_events": 0,
        }
