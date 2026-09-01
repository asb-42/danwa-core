"""Hybrid retriever — BM25 + vector search with optional cross-encoder re-ranking.

Migrated from src/dms/hybrid_retriever.py.
"""

import logging
import threading
import time
from typing import Any

from rank_bm25 import BM25Okapi

from backend.services.dms.metadata_index import MetadataIndex
from backend.services.dms.vector_store import DMSVectorStore

logger = logging.getLogger(__name__)

# BM25 corpus cache TTL in seconds
_CORPUS_CACHE_TTL = 300  # 5 minutes

# §4.2 (2026-08-31 review): ONE cross-encoder per process, loaded lazily
# on the first re-rank. Previously every ``HybridRetriever.__init__`` —
# i.e. every ``DMS`` instance, one per case — constructed its own
# ``CrossEncoder`` (downloading/loading weights per case directory).
# The loader is module-level shared across all retrievers; failures
# disable re-ranking for the whole process (same visible behavior as
# before, minus the N-times load attempts).
_CROSS_ENCODER_LOCK = threading.Lock()
_CROSS_ENCODER: Any | None = None
_CROSS_ENCODER_FAILED = False


def _get_cross_encoder() -> Any | None:
    """Load the shared cross-encoder once; ``None`` if unavailable."""
    global _CROSS_ENCODER, _CROSS_ENCODER_FAILED
    if _CROSS_ENCODER is not None:
        return _CROSS_ENCODER
    if _CROSS_ENCODER_FAILED:
        return None
    with _CROSS_ENCODER_LOCK:
        if _CROSS_ENCODER is not None:
            return _CROSS_ENCODER
        if _CROSS_ENCODER_FAILED:
            return None
        try:
            from sentence_transformers import CrossEncoder

            _CROSS_ENCODER = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")
            logger.info("Shared CrossEncoder loaded for re-ranking")
        except ImportError:
            logger.warning("sentence_transformers not installed, re-ranking disabled")
            _CROSS_ENCODER_FAILED = True
        except Exception as e:
            logger.warning("Failed to load CrossEncoder: %s, re-ranking disabled", e)
            _CROSS_ENCODER_FAILED = True
    return _CROSS_ENCODER

# Hard cap on the number of chunks fed into the BM25 index (P4.5+ §4.6).
#
# Without a cap, a project with N chunks will tokenize all N of them and
# build an in-memory BM25 index that is O(N) in both CPU and memory.
# On a large project (N ≥ 50k) this is a real DoS vector: a single query
# to ``retrieve()`` would block the event loop for seconds-to-minutes
# and the index would consume hundreds of MB.
#
# 10 000 was chosen because:
#   * it is large enough that real RAG results are unaffected for the
#     vast majority of projects (the BM25 top-20 still drives the
#     final ranking via RRF + cross-encoder reranking);
#   * it is small enough that index construction is bounded at
#     single-digit MB and well under a second of CPU;
#   * it is conservative — a project that actually has more than
#     10 000 chunks in BM25-corpus range is almost certainly a sign
#     that something upstream is chunking too aggressively.
MAX_BM25_CORPUS_SIZE = 10_000


class HybridRetriever:
    """Combines BM25 keyword search with vector similarity search using RRF."""

    def __init__(self, vector_store: DMSVectorStore, metadata_index: MetadataIndex | None = None):
        """Initialise HybridRetriever.

        Cheap by design (§4.1/§4.2 of the 2026-08-31 review): no model
        loads, no Chroma I/O. The cross-encoder is a module-level shared
        lazy singleton (see ``_get_cross_encoder``); re-ranking uses it
        via the ``cross_encoder`` property.
        """
        self.vector_store = vector_store
        self.metadata_index = metadata_index
        self.rrf_k = 60  # Standard RRF constant

        # BM25 corpus + index cache: (project_id, timestamp, chunks, bm25)
        self._corpus_cache: tuple[str, float, list[dict], Any] | None = None
        self._corpus_cache_lock = threading.RLock()

        # §4.2: cross-encoder override slot. Tests (and future callers)
        # may inject a fake; when unset, the module-level shared lazy
        # singleton is used. ``_cross_encoder_override = "unset"`` so a
        # legitimately-None override ("disable reranking") is distinct
        # from “use the shared one”.
        self._cross_encoder_override: Any = "unset"

    @property
    def cross_encoder(self) -> Any | None:
        """The process-shared cross-encoder, or ``None`` if unavailable."""
        if self._cross_encoder_override != "unset":
            return self._cross_encoder_override
        return _get_cross_encoder()

    @cross_encoder.setter
    def cross_encoder(self, value: Any) -> None:
        """Override the shared cross-encoder (test seam / explicit disable)."""
        self._cross_encoder_override = value

    def retrieve(self, query: str, project_id: str | None = None, k: int = 5) -> list[dict[str, Any]]:
        """Retrieve the instance."""
        chunks, bm25 = self._fetch_chunks(project_id)
        bm25_results = self._bm25_retrieve(query, chunks, bm25, top_n=20)
        vector_results = self.vector_store.search(query, project_id=project_id, k=20)

        rrf_scores = self._rrf_combine(bm25_results, vector_results)
        if not rrf_scores:
            return []

        chunk_map: dict[str, dict] = {chunk["id"]: chunk for chunk in chunks}
        for vr in vector_results:
            meta = vr["metadata"]
            chunk_id = f"{meta['document_id']}_chunk_{meta['chunk_index']}"
            if chunk_id not in chunk_map:
                chunk_map[chunk_id] = {
                    "id": chunk_id,
                    "text": vr["text"],
                    "metadata": meta,
                }

        sorted_chunk_ids = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:k]
        final_results = []
        for chunk_id, score in sorted_chunk_ids:
            chunk = chunk_map.get(chunk_id)
            if chunk:
                final_results.append(
                    {
                        "text": chunk["text"],
                        "metadata": chunk["metadata"],
                        "score": score,
                        "source": "hybrid",
                    }
                )

        if self.cross_encoder and final_results:
            pairs = [(query, res["text"]) for res in final_results]
            try:
                rerank_scores = self.cross_encoder.predict(pairs)
                for res, new_score in zip(final_results, rerank_scores):
                    res["score"] = float(new_score)
                final_results.sort(key=lambda x: x["score"], reverse=True)
            except Exception as e:
                logger.warning("Re-ranking failed: %s", e)

        return final_results[:k]

    def _fetch_chunks(self, project_id: str | None) -> tuple[list[dict[str, Any]], Any]:
        """Fetch chunks and BM25 index with TTL-based caching (§4.4).

        Returns ``(chunks, bm25_or_none)``. The BM25 index is built
        once per cache window and reused for every query in that
        window. Construction happens under ``_corpus_cache_lock`` so
        concurrent queries for the same project cannot build duplicate
        indexes — the first caller builds, the rest wait and reuse
        (single-flight). The corpus cap bounds the worst-case build to
        well under a second, so holding the lock across the build is
        safe (§4.4 of the 2026-08-31 review explicitly downgraded this
        to low urgency once capped).

        The BM25 corpus is capped at :data:`MAX_BM25_CORPUS_SIZE`
        chunks (P4.5+ §4.6) to bound index construction. The full chunk
        list is returned so the ``chunk_map`` lookup in :meth:`retrieve`
        stays complete — only the BM25 input is truncated.
        """
        now = time.time()
        with self._corpus_cache_lock:
            cache = self._corpus_cache
            if cache is not None and cache[0] == project_id and (now - cache[1]) < _CORPUS_CACHE_TTL:
                return cache[2], cache[3]
            chunks, bm25 = self._build_corpus_cache(project_id)
            self._corpus_cache = (project_id, time.time(), chunks, bm25)
            return chunks, bm25

    def _build_corpus_cache(self, project_id: str | None) -> tuple[list[dict[str, Any]], Any]:
        """Fetch chunks and build the BM25 index (no caching side effects)."""
        chunks = self._fetch_chunks_uncached(project_id)
        bm25 = None
        if chunks:
            corpus = chunks
            if len(corpus) > MAX_BM25_CORPUS_SIZE:
                logger.warning(
                    "HybridRetriever: BM25 corpus truncated from %d to %d chunks "
                    "(P4.5+ §4.6 cap). Cross-encoder reranking still uses the "
                    "top-k results, so quality is unaffected for the returned set; "
                    "consider re-chunking the source documents if you need full "
                    "keyword coverage.",
                    len(corpus),
                    MAX_BM25_CORPUS_SIZE,
                )
                corpus = corpus[:MAX_BM25_CORPUS_SIZE]
            tokenized = [self._tokenize(c["text"]) for c in corpus]
            bm25 = BM25Okapi(tokenized)
        return chunks, bm25

    def _fetch_chunks_uncached(self, project_id: str | None) -> list[dict[str, Any]]:
        """Fetch chunks uncached the instance."""
        if project_id and self.metadata_index:
            return self.metadata_index.get_chunks_by_project(project_id)
        if not project_id:
            # Refuse to dump the entire collection into the BM25 corpus when
            # no tenant scope is supplied. Cross-tenant bleed must never
            # happen — even by accident.
            logger.warning("_fetch_chunks called without project_id — returning empty corpus")
            return []
        try:
            results = self.vector_store.collection.get(
                where={"project_id": {"$eq": project_id}},
                include=["documents", "metadatas"],
            )
            chunks = []
            for chunk_id, doc_text, meta in zip(
                results.get("ids", []),
                results.get("documents", []),
                results.get("metadatas", []),
            ):
                chunks.append(
                    {
                        "id": chunk_id,
                        "text": doc_text,
                        "metadata": meta,
                    }
                )
            return chunks
        except Exception as e:
            logger.error("Failed to fetch chunks: %s", e)
            return []

    def _bm25_retrieve(self, query: str, chunks: list[dict], bm25: Any = None, top_n: int = 20) -> list[dict[str, Any]]:
        """BM25 retrieve using a pre-built index when available."""
        if not chunks or bm25 is None:
            return []
        try:
            tokenized_query = self._tokenize(query)
            scores = bm25.get_scores(tokenized_query)
            top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_n]
            results = []
            for idx in top_indices:
                chunk = chunks[idx]
                results.append(
                    {
                        "id": chunk["id"],
                        "text": chunk["text"],
                        "metadata": chunk["metadata"],
                        "bm25_score": scores[idx],
                    }
                )
            return results
        except Exception as e:
            logger.error("BM25 retrieval failed: %s", e)
            return []

    @staticmethod
    def _tokenize(text: str) -> list[str]:
        """Tokenize text for BM25: lowercase, split, filter short tokens."""
        return [t.lower() for t in text.split() if len(t) > 1]

    def _rrf_combine(self, bm25_results: list[dict], vector_results: list[dict]) -> dict[str, float]:
        """Rrf combine the instance."""
        rrf_scores: dict[str, float] = {}
        for rank, result in enumerate(bm25_results, start=1):
            chunk_id = result["id"]
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1 / (self.rrf_k + rank)
        for rank, result in enumerate(vector_results, start=1):
            meta = result["metadata"]
            chunk_id = f"{meta['document_id']}_chunk_{meta['chunk_index']}"
            rrf_scores[chunk_id] = rrf_scores.get(chunk_id, 0) + 1 / (self.rrf_k + rank)
        return rrf_scores
