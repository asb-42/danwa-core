"""Tree Graph Projector – builds the visual graph for SvelteFlow UI.

Read Model:
    debate_tree_nodes / debate_tree_edges tables (or Redis JSON).

Logic:
    Listens to *Acted and BranchForked events.
    Ignores large text content — stores only lightweight node/edge data.

Storage:
    SQLite tables (same DB as event store for simplicity in v1).

See ADR-001, Projector 1.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid

from backend.models.debate_event import DebateEvent
from backend.services.interactive.projectors.base import BaseProjector

logger = logging.getLogger(__name__)

_ACTOR_LABELS = {
    "user": "User",
    "agent": "Agent",
    "a2a": "External",
    "system": "System",
}

_EVENT_LABELS = {
    "UserActed": "User input",
    "AgentActed": "Agent response",
    "A2AActed": "External agent",
    "ToolRequested": "Tool call",
    "ToolExecuted": "Tool result",
    "ContextSynthesized": "Context built",
    "BranchForked": "Branch created",
    "MilestoneReached": "Milestone",
}


class TreeProjector(BaseProjector):
    """Builds the lightweight tree graph for the SvelteFlow frontend."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._init_tables()

    @property
    def name(self) -> str:
        return "tree_graph"

    def _init_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS debate_tree_nodes (
                node_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                role TEXT,
                label TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS debate_tree_edges (
                edge_id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_tree_nodes_space ON debate_tree_nodes(space_id);
            CREATE INDEX IF NOT EXISTS idx_tree_edges_space ON debate_tree_edges(space_id);
        """)
        self.conn.commit()

    def handles_event_type(self, event_type: str) -> bool:
        return event_type in (
            "UserActed",
            "AgentActed",
            "A2AActed",
            "ToolRequested",
            "ToolExecuted",
            "ContextSynthesized",
            "BranchForked",
            "MilestoneReached",
        )

    def handle_event(self, event: DebateEvent) -> None:
        # Build a short label for the UI
        label = self._build_label(event)

        # Insert node
        self.conn.execute(
            """INSERT OR REPLACE INTO debate_tree_nodes
               (node_id, space_id, event_type, actor_id, role, label, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.space_id,
                event.event_type,
                event.actor_id,
                event.role,
                label,
                event.created_at.isoformat(),
            ),
        )

        # Insert edge if parent exists
        if event.parent_id:
            edge_id = str(uuid.uuid4())
            self.conn.execute(
                """INSERT OR IGNORE INTO debate_tree_edges
                   (edge_id, space_id, source_id, target_id)
                   VALUES (?, ?, ?, ?)""",
                (edge_id, event.space_id, event.parent_id, event.event_id),
            )

        self.conn.commit()

    def _build_label(self, event: DebateEvent) -> str:
        """Generate a short, human-readable label for the SvelteFlow node."""
        actor_label = _ACTOR_LABELS.get(event.actor_type, event.actor_type)
        event_label = _EVENT_LABELS.get(event.event_type, event.event_type)

        if event.role:
            return f"{actor_label} ({event.role}) – {event_label}"

        # Truncate content for display
        content = event.content if isinstance(event.content, str) else str(event.content)
        if len(content) > 60:
            content = content[:57] + "..."

        return f"{actor_label}: {content}" if content else f"{actor_label} – {event_label}"

    # ── Read Model Queries ────────────────────────────────────────────────

    def get_nodes(self, space_id: str) -> list[dict]:
        """Return all nodes for a space (lightweight, for SvelteFlow)."""
        rows = self.conn.execute(
            "SELECT * FROM debate_tree_nodes WHERE space_id = ? ORDER BY created_at ASC",
            (space_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_edges(self, space_id: str) -> list[dict]:
        """Return all edges for a space (for SvelteFlow)."""
        rows = self.conn.execute(
            "SELECT * FROM debate_tree_edges WHERE space_id = ?",
            (space_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_tree_graph(self, space_id: str) -> dict:
        """Return the full tree graph as { nodes, edges } for SvelteFlow."""
        return {
            "nodes": self.get_nodes(space_id),
            "edges": self.get_edges(space_id),
        }
