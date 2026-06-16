"""Catalog API — fetch + browse public LLM metadata databases.

Endpoints
---------
- GET    /api/v1/catalog/sources
        → list known catalog sources + their last-fetch status

- POST   /api/v1/catalog/sources/{name}/fetch[?force=true]
        → git clone / pull a single source into the local cache

- POST   /api/v1/catalog/fetch-all
        → fetch all configured sources in one call

- GET    /api/v1/catalog/catalog[?source=catwalk|llm_db]
        → return the normalized catalog (in-memory after fetch)

- POST   /api/v1/catalog/import[?dry_run=true]
        → diff local modules vs. the catalog; optional apply
        (Phase 2 — import_engine)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from backend.core.config import Settings
from backend.llm_catalog.fetcher import (
    FetchResult,
    fetch_all,
    fetch_source,
)
from backend.llm_catalog.normalize import load_all_normalized
from backend.llm_catalog.sources import (
    SourceSpec,
    list_sources,
    resolve_cache_root,
)

logger = logging.getLogger(__name__)

router = APIRouter()


# ─── Source status cache (best-effort, no persistence) ──────────────
_STATUS_CACHE: dict[str, FetchResult] = {}


def _record_status(results: list[FetchResult]) -> list[FetchResult]:
    for r in results:
        _STATUS_CACHE[r.source] = r
    return results


# ─── Endpoints ───────────────────────────────────────────────────────


@router.get("/sources", response_model=list[dict[str, Any]])
async def list_catalog_sources() -> list[dict[str, Any]]:
    """List configured catalog sources + their last-fetch status."""
    settings = Settings()
    specs = list_sources(settings)
    out: list[dict[str, Any]] = []
    for s in specs:
        status = _STATUS_CACHE.get(s.name)
        out.append(
            {
                "name": s.name,
                "repo_url": s.repo_url,
                "branch": s.branch,
                "path": s.path,
                "description": s.description,
                "last_fetch": (status.to_dict() if status else None),
            }
        )
    return out


@router.post("/sources/{name}/fetch", response_model=dict[str, Any])
async def fetch_one_source(
    name: str,
    force: bool = Query(False, description="Force a fresh clone instead of pulling"),
) -> dict[str, Any]:
    """Fetch a single source by name (case-insensitive)."""
    try:
        spec = _spec_or_404(name)
    except HTTPException:
        raise
    settings = Settings()
    result = fetch_source(spec, settings, force_clone=force)
    _record_status([result])
    if result.error:
        raise HTTPException(status_code=502, detail=result.to_dict())
    return result.to_dict()


@router.post("/fetch-all", response_model=list[dict[str, Any]])
async def fetch_all_sources() -> list[dict[str, Any]]:
    """Fetch every configured source (used by 'Refresh all' button)."""
    settings = Settings()
    results = fetch_all(settings)
    _record_status(results)
    # Per-source 502 if any failed, but always return the full list
    for r in results:
        if r.error:
            logger.warning("catalog fetch failed for %s: %s", r.source, r.error)
    return [r.to_dict() for r in results]


@router.get("/catalog", response_model=dict[str, Any])
async def get_normalized_catalog(
    source: str | None = Query(None, description="Filter to a single source name"),
) -> dict[str, Any]:
    """Return the normalized catalog (loads from the local cache).

    The cache is populated by an earlier ``POST .../fetch`` call.
    If a source is missing locally, its entry is returned as
    ``{models: [], error: "not fetched yet"}``.
    """
    settings = Settings()
    cache_dir = resolve_cache_root(settings)
    all_specs = list_sources(settings)
    chosen: list[SourceSpec]
    if source:
        chosen = [s for s in all_specs if s.name == source.lower()]
        if not chosen:
            raise HTTPException(404, detail=f"Unknown source {source!r}")
    else:
        chosen = all_specs

    by_source = load_all_normalized(chosen, cache_dir)
    return {
        "sources": [
            {
                "name": s.name,
                "model_count": len(by_source.get(s.name, [])),
                "models": [m.to_dict() for m in by_source.get(s.name, [])],
            }
            for s in chosen
        ],
    }


@router.post("/import", response_model=dict[str, Any])
async def import_catalog(
    dry_run: bool = Query(True, description="If true, only compute the diff; do not write"),
    sources: str | None = Query(
        None,
        description="Optional comma-separated list of source names to include (default: all)",
    ),
) -> dict[str, Any]:
    """Diff local ``llm-profiles/`` against the catalog and (optionally) apply.

    The endpoint first ensures both configured sources are fetched
    (idempotent), then computes the diff and either reports it (default
    ``dry_run=true``) or applies it to the local ``llm-profiles/`` tree
    (``dry_run=false``).

    Apply is non-destructive for stale entries — it writes a
    ``.stale`` flag file but does not delete.  A separate cleanup
    job can act on that flag (out of scope for this endpoint).
    """
    from backend.llm_catalog.import_engine import run_import

    settings = Settings()
    src_list: list[str] | None = None
    if sources:
        src_list = [s.strip() for s in sources.split(",") if s.strip()]
    try:
        report = run_import(settings, dry_run=dry_run, sources=src_list)
    except Exception as e:  # noqa: BLE001 — surface any failure to the caller
        logger.exception("catalog import failed")
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")
    return report.to_dict()


# ─── Helpers ─────────────────────────────────────────────────────────


def _spec_or_404(name: str) -> SourceSpec:
    try:
        from backend.llm_catalog.sources import get_source  # local to avoid cycle
        return get_source(name, Settings())
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown catalog source {name!r}",
        )
