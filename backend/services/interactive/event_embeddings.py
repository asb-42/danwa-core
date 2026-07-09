"""EventEmbeddingStore – ChromaDB vector store for debate event content.

Embeds agent_speech and user_message events to enable semantic search
across the debate tree. Used by the Context Synthesizer to find
relevant side-branches without traversing the entire tree.
"""

from __future__ import annotations

import logging
from pathlib import Path

import chromadb

logger = logging.getLogger(__name__)

_DEFAULT_CHROMA_PATH = Path("data/interactive_embeddings")


class EventEmbeddingStore:
    """Vector store for event content embeddings."""

    def __init__(
        self,
        chroma_path: str | Path | None = None,
        collection_name: str = "debate_events",
    ):
        chroma_dir = Path(chroma_path) if chroma_path else _DEFAULT_CHROMA_PATH
        chroma_dir.mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=str(chroma_dir))
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        logger.info(
            "EventEmbeddingStore loaded: %d events in '%s'",
            self.collection.count(),
            collection_name,
        )

    def embed_event(
        self,
        event_id: str,
        space_id: str,
        content: str,
        event_type: str,
        actor_id: str,
        role: str | None = None,
    ) -> None:
        """Embed a single event's content for semantic search."""
        if not content or not content.strip():
            return

        metadata = {
            "space_id": space_id,
            "event_type": event_type,
            "actor_id": actor_id,
        }
        if role:
            metadata["role"] = role

        self.collection.upsert(
            ids=[event_id],
            documents=[content],
            metadatas=[metadata],
        )

    def search_similar(
        self,
        space_id: str,
        query: str,
        exclude_event_ids: list[str] | None = None,
        n_results: int = 5,
    ) -> list[dict]:
        """Find events semantically similar to the query within a space.

        Returns list of dicts with keys: event_id, text, metadata, relevance_score.
        """
        if self.collection.count() == 0:
            return []

        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=n_results,
                where={"space_id": {"$eq": space_id}},
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.warning("Event embedding search failed: %s", e)
            return []

        exclude_set = set(exclude_event_ids or [])
        output = []
        for event_id, doc, meta, dist in zip(
            results["ids"][0],
            results["documents"][0],
            results["metadatas"][0],
            results["distances"][0],
        ):
            if event_id in exclude_set:
                continue
            output.append(
                {
                    "event_id": event_id,
                    "text": doc,
                    "metadata": meta,
                    "relevance_score": max(0.0, 1.0 - dist),
                }
            )
        return sorted(output, key=lambda x: x["relevance_score"], reverse=True)

    def delete_event(self, event_id: str) -> None:
        """Remove an event from the embedding store."""
        self.collection.delete(ids=[event_id])

    def count(self) -> int:
        return self.collection.count()
