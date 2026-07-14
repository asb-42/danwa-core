"""Context & RAG Projector – maintains the shared debate state.

Read Model:
    debate_state table (structured facts) + ChromaDB (vectors).

Logic:
    Extracts structured_output (claims, critiques, evidence) from AgentActed
    events. Embeds content text into ChromaDB for semantic search.

Storage:
    SQLite ``debate_state`` table + ChromaDB collection.

See ADR-001, Projector 2.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import UTC, datetime

from backend.models.debate_event import DebateEvent
from backend.services.interactive.projectors.base import BaseProjector

logger = logging.getLogger(__name__)


class ContextProjector(BaseProjector):
    """Maintains the structured debate state for the ContextSynthesizer."""

    def __init__(self, conn: sqlite3.Connection, embedding_store=None):
        self.conn = conn
        self.embedding_store = embedding_store
        self._init_tables()

    @property
    def name(self) -> str:
        return "context_rag"

    def _init_tables(self) -> None:
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS debate_state (
                id TEXT PRIMARY KEY,
                space_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                fact_type TEXT NOT NULL,
                fact_content TEXT NOT NULL,
                actor_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_state_space ON debate_state(space_id);
            CREATE INDEX IF NOT EXISTS idx_state_fact_type ON debate_state(fact_type);
            CREATE INDEX IF NOT EXISTS idx_state_event ON debate_state(event_id);
        """)
        self.conn.commit()

    def handles_event_type(self, event_type: str) -> bool:
        return event_type in ("AgentActed", "UserActed", "ToolExecuted")

    def handle_event(self, event: DebateEvent) -> None:
        now = datetime.now(UTC).isoformat()

        # Extract structured_output from metadata if present
        structured = event.metadata_json.get("structured_output", {})

        if structured:
            # Write each claim as a fact
            for claim in structured.get("claims", []):
                self._write_fact(event, "claim", claim, now)

            for critique in structured.get("critiques", []):
                self._write_fact(event, "critique", critique, now)

            for evidence in structured.get("evidence", []):
                self._write_fact(event, "evidence", evidence, now)

            for question in structured.get("questions", []):
                self._write_fact(event, "question", question, now)

        # Also store the free-text content as a fact
        content = event.content if isinstance(event.content, str) else json.dumps(event.content)
        if content:
            self._write_fact(event, "content", content, now)

        # Embed into ChromaDB if available
        if self.embedding_store and content:
            try:
                self.embedding_store.embed_event(
                    event_id=event.event_id,
                    space_id=event.space_id,
                    content=content,
                    event_type=event.event_type,
                    actor_id=event.actor_id,
                    role=event.role,
                )
            except Exception:
                logger.warning("Failed to embed event %s in context projector", event.event_id)

    def _write_fact(
        self,
        event: DebateEvent,
        fact_type: str,
        fact_content: str,
        now: str,
    ) -> None:
        fact_id = str(uuid.uuid4())
        self.conn.execute(
            """INSERT INTO debate_state
               (id, space_id, event_id, fact_type, fact_content, actor_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (fact_id, event.space_id, event.event_id, fact_type, fact_content, event.actor_id, now),
        )
        self.conn.commit()

    # ── Read Model Queries ────────────────────────────────────────────────

    def get_facts(
        self,
        space_id: str,
        fact_type: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return facts for a space, optionally filtered by type."""
        if fact_type:
            rows = self.conn.execute(
                """SELECT * FROM debate_state
                   WHERE space_id = ? AND fact_type = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (space_id, fact_type, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM debate_state
                   WHERE space_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (space_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_claims(self, space_id: str, limit: int = 20) -> list[dict]:
        return self.get_facts(space_id, fact_type="claim", limit=limit)

    def get_critiques(self, space_id: str, limit: int = 20) -> list[dict]:
        return self.get_facts(space_id, fact_type="critique", limit=limit)

    def get_evidence(self, space_id: str, limit: int = 20) -> list[dict]:
        return self.get_facts(space_id, fact_type="evidence", limit=limit)

    def get_open_questions(self, space_id: str, limit: int = 10) -> list[dict]:
        return self.get_facts(space_id, fact_type="question", limit=limit)
