"""ChromaDB vector store for document chunks.

Migrated from src/dms/vector_store.py. Now accepts explicit paths.

§4.1 (2026-08-31 review): Chroma client setup (``PersistentClient``,
``get_or_create_collection``, ``count``) is deferred to first use —
``DMS`` construction (which happens per case, on the request thread,
on cache miss) no longer pays the full client bootstrap inline. The
first actual vector-store operation initializes lazily under a lock.
"""

import logging
import threading
from pathlib import Path

import chromadb

logger = logging.getLogger(__name__)


class DMSVectorStore:
    """Persistent vector store backed by ChromaDB (lazy-initialized)."""

    def __init__(self, chroma_path: str | Path, collection_name: str = "document_chunks"):
        """Initialise DMSVectorStore (cheap — no Chroma I/O here)."""
        self._chroma_path = str(Path(chroma_path))
        self._collection_name = collection_name
        self._client: chromadb.api.ClientAPI | None = None
        self._collection = None
        self._lock = threading.RLock()

    # -- lazy initialization ------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Create the Chroma client + collection on first use (idempotent).

        Guarded by an RLock: multiple threads may race the first
        operation on a freshly constructed store.
        """
        if self._collection is not None:
            return
        with self._lock:
            if self._collection is not None:
                return
            chroma_dir = Path(self._chroma_path)
            chroma_dir.mkdir(parents=True, exist_ok=True)
            client = chromadb.PersistentClient(path=str(chroma_dir))
            collection = client.get_or_create_collection(
                name=self._collection_name, metadata={"hnsw:space": "cosine"}
            )
            logger.info(
                "DMS VectorStore loaded: %d chunks in '%s'",
                collection.count(),
                self._collection_name,
            )
            # Assign client only after collection is fully usable so a
            # failure above leaves both as None (retry on next call).
            self._client = client
            self._collection = collection

    @property
    def collection(self):
        """The Chroma collection (initializes on first access)."""
        self._ensure_initialized()
        return self._collection

    def add_chunks(self, document_id: str, chunks: list[dict], project_id: str = "") -> None:
        """Add chunks."""
        if not chunks:
            return
        ids = []
        documents = []
        metadatas = []
        for chunk in chunks:
            chunk_index = chunk.get("chunk_index", 0)
            chunk_id = f"{document_id}_chunk_{chunk_index}"
            ids.append(chunk_id)
            documents.append(chunk["text"])
            metadatas.append(
                {
                    "document_id": document_id,
                    "project_id": project_id,
                    "chunk_index": chunk_index,
                    "page": chunk.get("page", 0),
                    "file_name": chunk.get("file_name", ""),
                }
            )
        self.collection.add(ids=ids, documents=documents, metadatas=metadatas)
        logger.info("Added %d chunks for document %s", len(chunks), document_id)

    def search(self, query: str, project_id: str | None = None, k: int = 5) -> list[dict]:
        """Search the instance."""
        if self.count() == 0:
            return []
        where = None
        if project_id is not None:
            where = {"project_id": {"$eq": project_id}}
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=k,
                where=where,
                include=["documents", "metadatas", "distances"],
            )
            output = []
            for doc, meta, dist in zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ):
                output.append(
                    {
                        "text": doc,
                        "metadata": meta,
                        "relevance_score": max(0.0, 1.0 - dist),
                    }
                )
            return sorted(output, key=lambda x: x["relevance_score"], reverse=True)
        except Exception as e:
            logger.warning("DMS search failed: %s", e)
            return []

    def delete_document_chunks(self, document_id: str) -> None:
        """Delete document chunks."""
        self.collection.delete(where={"document_id": {"$eq": document_id}})
        logger.info("Deleted chunks for document %s", document_id)

    def count(self) -> int:
        """Count the instance."""
        return self.collection.count()
