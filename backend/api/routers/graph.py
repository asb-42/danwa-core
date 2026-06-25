"""Case-Space Knowledge Graph API router.

Implements the graph endpoints described in
``plans/2026-06-14_case-space-workspace.md`` (Phase 4 + Anhang A).

Phase-4 endpoints:

  GET  /api/v1/graph/local?entity_type=…&entity_id=…&hops=1   → GraphPayload
  GET  /api/v1/graph/global?tenant_id=…&filters=…             → GraphPayload
  GET  /api/v1/graph/edges?src=…&tgt=…                        → EdgeDetail

All endpoints are feature-gated by
``settings.enable_case_space_graph``.

Performance: the global endpoint enforces a hard cap on
returned nodes; if more exist, the response carries
``truncated: true, total_count, sampled_count`` so the
frontend can decide whether to surface a notice.

The graph is *derived* from the existing case/debate/document
stores — no new database tables are required.  The dedup work
is done in Python at request time; for very large tenants this
should eventually be replaced by a pre-computed cache (Phase 5+
in the plan, see 4.3 graph_edge_cache).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.api.deps import get_case_store, get_current_user
from backend.core.config import settings
from backend.models.schemas import (
    EdgeDetail,
    GraphEdge,
    GraphNode,
    GraphPayload,
)
from backend.models.user import User
from backend.services.graph_edge_cache import get_graph_edge_cache_service

logger = logging.getLogger(__name__)

router = APIRouter()

# ─── Tunables ────────────────────────────────────────────────────────
GLOBAL_DEFAULT_LIMIT = 200
GLOBAL_MAX_LIMIT = 500
LOCAL_MAX_HOPS = 2


def _require_graph() -> None:
    if not settings.enable_case_space_graph:
        raise HTTPException(
            status_code=404,
            detail="Case-Space Graph is not enabled (set DANWA_ENABLE_CASE_SPACE_GRAPH=true)",
        )


# ─── Helpers ────────────────────────────────────────────────────────


def _entity_kind(case_id: str | None, debate_id: str | None, document_id: str | None) -> str:
    if debate_id:
        return "Debate"
    if document_id:
        return "Document"
    if case_id:
        return "Case"
    return "Unknown"


def _build_local_subgraph(case_store, entity_type: str, entity_id: str, hops: int) -> GraphPayload:
    """1–2 hop subgraph centred on the given entity.

    1-hop: case→debates, case→tags
    2-hop: debate→documents (via DMS rag_context table)
    """
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    if entity_type.lower() == "case":
        case = case_store.get(case_store._cache and next(iter(case_store._cache), "") or "", entity_id)  # type: ignore[arg-type]
        if case is None:
            # Try across all known tenants
            for tid in _known_tenants_via_case_store(case_store):
                c = case_store.get(tid, entity_id)
                if c is not None:
                    case = c
                    break
        if case is None:
            raise HTTPException(status_code=404, detail=f"{entity_type} {entity_id} not found")

        # Centre node
        nodes.append(
            GraphNode(
                id=f"case:{case.id}",
                type="Case",
                label=case.title,
                meta={"status": case.status, "tenant_id": case.tenant_id},
            )
        )

        # Debates in this case
        try:
            from backend.api.deps import get_debate_store_for_case

            debate_store = get_debate_store_for_case(case.id)
            for d in debate_store.list_all(limit=200):
                did = d.get("debate_id") or d.get("id", "")
                nodes.append(
                    GraphNode(
                        id=f"debate:{did}",
                        type="Debate",
                        label=d.get("topic") or d.get("title") or "(untitled)",
                        meta={"status": d.get("status", "unknown"), "case_id": case.id},
                    )
                )
                edges.append(
                    GraphEdge(
                        src=f"case:{case.id}",
                        tgt=f"debate:{did}",
                        type="contains",
                        weight=1.0,
                    )
                )

                # 2-hop: documents linked via DMS rag_context
                if hops >= 2:
                    session_id = d.get("session_id", "")
                    if session_id:
                        doc_ids = _get_document_ids_for_session(case.id, session_id)
                        for doc_id in doc_ids:
                            doc_node_id = f"document:{doc_id}"
                            # Avoid duplicate nodes
                            if not any(n.id == doc_node_id for n in nodes):
                                nodes.append(
                                    GraphNode(
                                        id=doc_node_id,
                                        type="Document",
                                        label=doc_id,
                                        meta={"case_id": case.id, "debate_id": did},
                                    )
                                )
                            edges.append(
                                GraphEdge(
                                    src=f"debate:{did}",
                                    tgt=doc_node_id,
                                    type="references",
                                    weight=1.0,
                                )
                            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("graph/local: debate store unavailable for case %s: %s", case.id, exc)

        # Tags on this case
        for t in case.tags or []:
            tag_id = f"tag:{t}"
            nodes.append(
                GraphNode(
                    id=tag_id,
                    type="Tag",
                    label=t,
                )
            )
            edges.append(
                GraphEdge(
                    src=f"case:{case.id}",
                    tgt=tag_id,
                    type="tagged_with",
                    weight=1.0,
                )
            )

    elif entity_type.lower() == "debate":
        # Reverse traversal: find the parent case
        parent_case = _find_case_for_debate(case_store, entity_id)
        if parent_case:
            nodes.append(
                GraphNode(
                    id=f"case:{parent_case.id}",
                    type="Case",
                    label=parent_case.title,
                    meta={"status": parent_case.status, "tenant_id": parent_case.tenant_id},
                )
            )
            edges.append(
                GraphEdge(
                    src=f"case:{parent_case.id}",
                    tgt=f"debate:{entity_id}",
                    type="contains",
                    weight=1.0,
                )
            )
        # Always include the debate node itself
        nodes.append(
            GraphNode(
                id=f"debate:{entity_id}",
                type="Debate",
                label=entity_id,
                meta={"case_id": parent_case.id if parent_case else None},
            )
        )
    else:
        raise HTTPException(status_code=422, detail=f"Unknown entity_type: {entity_type}")

    return GraphPayload(
        nodes=nodes,
        edges=edges,
        truncated=False,
        total_count=len(nodes),
        sampled_count=len(nodes),
    )


def _get_document_ids_for_session(case_id: str, session_id: str) -> list[str]:
    """Get document IDs linked to a session via DMS rag_context table."""
    try:
        import sqlite3
        from pathlib import Path

        dms_dir = Path("data/tenants") / "_default" / "cases" / case_id / "dms"
        # Also try other tenants
        if not dms_dir.exists():
            for tenant_dir in (Path("data/tenants")).iterdir():
                candidate = tenant_dir / "cases" / case_id / "dms"
                if candidate.exists():
                    dms_dir = candidate
                    break

        dms_db = dms_dir / "dms.db"
        if not dms_db.exists():
            return []

        conn = sqlite3.connect(str(dms_db))
        cursor = conn.execute(
            "SELECT document_id FROM rag_context WHERE session_id = ?",
            (session_id,),
        )
        doc_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        return doc_ids
    except Exception as exc:  # noqa: BLE001
        logger.debug("graph: failed to get documents for session %s: %s", session_id, exc)
        return []


def _find_case_for_debate(case_store, debate_id: str):
    """Find the parent case for a debate by searching all tenants."""
    try:
        from backend.api.deps import get_debate_store_for_case
        from pathlib import Path

        # Search all known case directories for a debate with this ID
        base = Path("data/tenants")
        if base.is_dir():
            for tenant_dir in sorted(base.iterdir()):
                if not tenant_dir.is_dir():
                    continue
                cases_dir = tenant_dir / "cases"
                if not cases_dir.is_dir():
                    continue
                for case_dir in cases_dir.iterdir():
                    if not case_dir.is_dir():
                        continue
                    debates_dir = case_dir / "debates"
                    if not debates_dir.is_dir():
                        continue
                    debate_file = debates_dir / f"{debate_id}.json"
                    if debate_file.exists():
                        # Found it — load the case
                        tid = tenant_dir.name
                        cid = case_dir.name
                        return case_store.get(tid, cid)
    except Exception as exc:  # noqa: BLE001
        logger.debug("graph: failed to find case for debate %s: %s", debate_id, exc)
    return None


def _known_tenants_via_case_store(case_store) -> list[str]:
    cache = getattr(case_store, "_cache", None)
    if isinstance(cache, dict):
        return list(cache.keys())
    return []


def _build_global_subgraph(case_store, tenant_id: str, limit: int) -> GraphPayload:
    """Tenant-wide subgraph: every Case, its debates, and its tags."""
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []

    cases = case_store.list_by_tenant(tenant_id)
    total_cases = len(cases)
    truncated = False

    for c in cases[:limit]:
        nodes.append(
            GraphNode(
                id=f"case:{c.id}",
                type="Case",
                label=c.title,
                meta={"status": c.status, "tenant_id": c.tenant_id},
            )
        )
        # Tags
        for t in c.tags or []:
            tag_id = f"tag:{t}"
            if not any(n.id == tag_id for n in nodes):
                nodes.append(GraphNode(id=tag_id, type="Tag", label=t))
            edges.append(GraphEdge(src=f"case:{c.id}", tgt=tag_id, type="tagged_with", weight=1.0))
        # Debates
        try:
            from backend.api.deps import get_debate_store_for_case

            debate_store = get_debate_store_for_case(c.id)
            for d in debate_store.list_all(limit=50):
                did = d.get("debate_id") or d.get("id", "")
                nodes.append(
                    GraphNode(
                        id=f"debate:{did}",
                        type="Debate",
                        label=d.get("topic") or d.get("title") or "(untitled)",
                        meta={"status": d.get("status", "unknown"), "case_id": c.id},
                    )
                )
                edges.append(GraphEdge(src=f"case:{c.id}", tgt=f"debate:{did}", type="contains", weight=1.0))
        except Exception as exc:  # noqa: BLE001
            logger.warning("graph/global: debate store for case %s unavailable: %s", c.id, exc)

    if total_cases > limit:
        truncated = True

    return GraphPayload(
        nodes=nodes,
        edges=edges,
        truncated=truncated,
        total_count=total_cases,
        sampled_count=min(limit, total_cases),
    )


# ─── Endpoints ──────────────────────────────────────────────────────


@router.get("/graph/local", response_model=GraphPayload)
def get_local_graph(
    entity_type: str = Query(..., min_length=1),
    entity_id: str = Query(..., min_length=1),
    hops: int = Query(1, ge=1, le=LOCAL_MAX_HOPS),
    case_store=Depends(get_case_store),
) -> GraphPayload:
    """Return the 1-hop (or up to 2-hop) subgraph around one entity."""
    _require_graph()
    return _build_local_subgraph(case_store, entity_type, entity_id, hops)


@router.get("/graph/global", response_model=GraphPayload)
def get_global_graph(
    tenant_id: str = Query(..., min_length=1),
    limit: int = Query(GLOBAL_DEFAULT_LIMIT, ge=1, le=GLOBAL_MAX_LIMIT),
    case_store=Depends(get_case_store),
) -> GraphPayload:
    """Return a tenant-wide subgraph (capped at ``limit`` cases)."""
    _require_graph()
    return _build_global_subgraph(case_store, tenant_id, limit)


@router.get("/graph/edges", response_model=EdgeDetail)
def get_edge_details(
    src: str = Query(..., min_length=1),
    tgt: str = Query(..., min_length=1),
    case_id: str | None = Query(
        None,
        min_length=1,
        description=(
            "Optional case id.  When supplied, the service is scoped "
            "to that case's tenant and only that case's audit events "
            "are considered.  When omitted, the active tenant is "
            "inferred from the auth context."
        ),
    ),
    user: User = Depends(get_current_user),
) -> EdgeDetail:
    """Return metadata for one edge (kind, weight, evidence).

    Phase 4.3 / 5.2: delegates to
    :class:`GraphEdgeCacheService` which materialises edges from
    the audit log.  When no evidence is found the response carries
    a short placeholder so the UI can still render a "no evidence
    yet" hint without falling back to a hard error.
    """
    _require_graph()

    # Resolve the tenant for this lookup.  If the caller supplied a
    # case_id we look up that case's tenant directly; otherwise we
    # use the authenticated user's tenant from the auth context.
    tenant_id: str | None = None
    if case_id:
        # Try every known tenant for the case
        for tid in _known_tenants_via_case_store(_peek_case_store()):
            c = _peek_case_store().get(tid, case_id)
            if c is not None:
                tenant_id = tid
                break

    if not tenant_id:
        # Use the authenticated user's tenant
        tenant_id = user.tenant_id

    if not tenant_id:
        return EdgeDetail(
            src=src,
            tgt=tgt,
            type="unknown",
            weight=1.0,
            evidence=[
                "Edge evidence is not yet materialised for this lookup. "
                "Open a Workspace and try again \u2014 the cache will be "
                "populated from the audit log on the next request."
            ],
        )

    service = get_graph_edge_cache_service()
    ev = service.get_evidence(tenant_id, src, tgt)
    if ev is None:
        return EdgeDetail(
            src=src,
            tgt=tgt,
            type="unknown",
            weight=1.0,
            evidence=[f"No audit evidence found for edge {src} \u2192 {tgt} in tenant {tenant_id}."],
        )
    return EdgeDetail(
        src=ev.src,
        tgt=ev.tgt,
        type=ev.type,
        weight=ev.weight,
        evidence=ev.evidence,
        created_at=ev.created_at,
    )


def _peek_case_store():
    """Local helper to access the case_store at request time.

    The router is a module-level object; case_store is created
    lazily by get_case_store() which uses a FastAPI dependency.
    Outside the request handler we need a stable accessor that
    does not raise — we fall back to the lru_cache-protected
    singleton in backend.api.deps.
    """
    from backend.api.deps import get_case_store

    return get_case_store()
