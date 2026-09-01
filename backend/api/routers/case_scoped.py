"""Case-scoped API router — tenant/case-aware endpoints for debates and DMS.

These routes replace the legacy ``X-Project-Id`` header pattern with
path-based tenant + case resolution:

  ``/api/v1/tenants/{tid}/cases/{cid}/debates/...``
  ``/api/v1/tenants/{tid}/cases/{cid}/dms/...``
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel

from backend.api.deps import (
    get_audit_service,
    get_case_store,
    get_current_user,
    get_project_store,
    get_tag_store,
    get_tenant_store,
)
from backend.models.schemas import (
    DebateListItem,
    DebateRequest,
    DebateResponse,
    DebateStatus,
    DebateStatusResponse,
    OOBInputBody,
    OOBInputResponse,
    RoundData,
    TagInfo,
)
from backend.models.user import User
from backend.persistence.audit import AuditService
from backend.persistence.case_store import CaseStore
from backend.persistence.debate_store import DebateStore
from backend.persistence.project_store import ProjectStore
from backend.persistence.tag_store import TagStore
from backend.persistence.tenant_store import TenantStore

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_tenant_access(user: User, tenant_id: str) -> None:
    """Verify the user has access to the given tenant (fail-closed).

    Same contract as ``backend.api.routers.inbox._check_tenant_access``:
    admins bypass; everyone else needs a membership row. Any store
    failure results in 403 — never open access (§2.7 of the
    2026-08-31 review: DMS/debate/audit routes previously trusted the
    attacker-controlled ``tenant_id`` path parameter).
    """
    if user.role == "admin":
        return
    try:
        from backend.api.deps import get_membership_store

        membership_store = get_membership_store()
        membership = membership_store.get(tenant_id, user.id)
        if membership is None:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied: you are not a member of tenant {tenant_id}",
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("case_scoped: failed to check tenant access for user %s: %s", user.id, exc)
        raise HTTPException(status_code=403, detail="Access denied: unable to verify tenant membership")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_case_dir(tenant_id: str, case_id: str, case_store: CaseStore) -> Path:
    """Resolve case dir internally."""
    case = case_store.get(tenant_id, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")
    return case_store.get_case_dir(tenant_id, case_id)


def _get_debate_store_for_case(tenant_id: str, case_id: str, case_store: CaseStore) -> DebateStore:
    """Return (or lazily create) debate store for case."""
    case_dir = _resolve_case_dir(tenant_id, case_id, case_store)
    debates_dir = case_dir / "debates"
    debates_dir.mkdir(parents=True, exist_ok=True)
    return DebateStore(data_dir=debates_dir)


def _resolve_llm_model(llm_profile_id: str, project_id: str) -> str:
    """Resolve llm model internally."""
    if not llm_profile_id:
        return ""
    try:
        from backend.api.deps import get_blueprint_repository

        repo = get_blueprint_repository()
        profile = repo.get_llm_profile(llm_profile_id)
        if profile:
            return profile.model
    except Exception as e:
        logger.warning("Failed to resolve LLM profile %s: %s", llm_profile_id, e)
    return llm_profile_id


def _resolve_tags(tenant_id: str, tag_ids: list[str], tag_store: TagStore) -> list[TagInfo]:
    """Resolve a list of tag IDs to TagInfo objects."""
    if not tag_ids:
        return []
    result = []
    for tid in tag_ids:
        tag = tag_store.get(tenant_id, tid)
        if tag:
            result.append(TagInfo(id=tag.id, name=tag.name, color=tag.color))
    return result


def _build_debate_item(
    d: dict,
    debates: list[dict],
    *,
    tenant_id: str = "",
    tenant_name: str = "",
    case_id: str = "",
    case_title: str = "",
    tags: list[TagInfo] | None = None,
) -> DebateListItem:
    """Build a DebateListItem from raw debate dict with optional tenant/case context."""
    req = d.get("request", {})
    if hasattr(req, "case"):
        case_text = req.case.text
        language = getattr(req, "language", "de") or "de"
    elif isinstance(req, dict):
        case_text = req.get("case", {}).get("text", "") or ""
        language = req.get("language", "de") or "de"
    else:
        case_text = ""
        language = "de"

    result = d.get("result")
    consensus = result.get("final_consensus") if isinstance(result, dict) else None

    fork_info = d.get("fork_info")
    parent_id = fork_info.get("parent_debate_id") if isinstance(fork_info, dict) else None

    debate_id_current = d["debate_id"]
    forks_count = sum(
        1 for other_d in debates if isinstance(other_d.get("fork_info"), dict) and other_d["fork_info"].get("parent_debate_id") == debate_id_current
    )

    return DebateListItem(
        debate_id=d["debate_id"],
        status=d["status"],
        title=d.get("title", ""),
        current_round=d.get("current_round", 0),
        max_rounds=d.get("max_rounds", 3),
        consensus_score=consensus,
        case_preview=case_text[:120],
        case_text=case_text,
        language=language,
        created_at=d.get("created_at", datetime.now(UTC)),
        updated_at=d.get("updated_at", datetime.now(UTC)),
        project_id=case_id,
        project_name=case_title or case_id,
        parent_debate_id=parent_id,
        forks_count=forks_count,
        is_mvp=d.get("is_mvp", False),
        tenant_id=tenant_id,
        tenant_name=tenant_name,
        case_id=case_id,
        case_title=case_title,
        tags=tags or [],
    )


# ---------------------------------------------------------------------------
# Tenant-scoped debates — /tenants/{tid}/debates
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/debates", response_model=list[DebateListItem])
async def list_tenant_debates(
    tenant_id: str,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    search: str | None = None,
    case_store: CaseStore = Depends(get_case_store),
    tag_store: TagStore = Depends(get_tag_store),
    tenant_store: TenantStore = Depends(get_tenant_store),
    user: User = Depends(get_current_user),
) -> list[DebateListItem]:
    """List ALL debates across all cases in a tenant (newest first).

    Aggregates debates from every case belonging to the tenant,
    enriching each item with case title, tenant name, and tag information.
    """
    _check_tenant_access(user, tenant_id)
    tenant = tenant_store.get(tenant_id)
    tenant_name = tenant.name if tenant else tenant_id

    all_cases = case_store.list_by_tenant(tenant_id)
    all_items: list[DebateListItem] = []

    for case_obj in all_cases:
        try:
            store = _get_debate_store_for_case(tenant_id, case_obj.id, case_store)
            debates = store.list_all(limit=1000)
            tags = _resolve_tags(tenant_id, case_obj.tags, tag_store)

            for d in debates:
                req = d.get("request", {})
                if hasattr(req, "case"):
                    case_text = req.case.text
                elif isinstance(req, dict):
                    case_text = req.get("case", {}).get("text", "") or ""
                else:
                    case_text = ""

                if status and d.get("status") != status:
                    continue

                debate_title = d.get("title", "")
                if search:
                    search_lower = search.lower()
                    if (
                        search_lower not in case_text.lower()
                        and search_lower not in debate_title.lower()
                        and search_lower not in d.get("debate_id", "").lower()
                    ):
                        continue

                item = _build_debate_item(
                    d,
                    debates,
                    tenant_id=tenant_id,
                    tenant_name=tenant_name,
                    case_id=case_obj.id,
                    case_title=case_obj.title,
                    tags=tags,
                )
                all_items.append(item)
        except Exception:
            logger.warning("Failed to load debates for case %s in tenant %s", case_obj.id, tenant_id, exc_info=True)
            continue

    # Sort by created_at descending, apply pagination
    all_items.sort(key=lambda x: x.created_at, reverse=True)
    return all_items[offset : offset + limit]


# ---------------------------------------------------------------------------
# Debate endpoints — /tenants/{tid}/cases/{cid}/debates
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/cases/{case_id}/debates", response_model=list[DebateListItem])
async def list_case_debates(
    tenant_id: str,
    case_id: str,
    limit: int = 50,
    offset: int = 0,
    status: str | None = None,
    search: str | None = None,
    case_store: CaseStore = Depends(get_case_store),
    tag_store: TagStore = Depends(get_tag_store),
    tenant_store: TenantStore = Depends(get_tenant_store),
    user: User = Depends(get_current_user),
) -> list[DebateListItem]:
    """List debates in a case (newest first)."""
    _check_tenant_access(user, tenant_id)
    store = _get_debate_store_for_case(tenant_id, case_id, case_store)
    debates = store.list_all(limit=limit + offset)

    case_obj = case_store.get(tenant_id, case_id)
    case_title = case_obj.title if case_obj else case_id
    tags = _resolve_tags(tenant_id, case_obj.tags if case_obj else [], tag_store)

    tenant = tenant_store.get(tenant_id)
    tenant_name = tenant.name if tenant else tenant_id

    items = []
    for d in debates:
        req = d.get("request", {})
        if hasattr(req, "case"):
            case_text = req.case.text
        elif isinstance(req, dict):
            case_text = req.get("case", {}).get("text", "") or ""
        else:
            case_text = ""

        if status and d.get("status") != status:
            continue

        debate_title = d.get("title", "")
        if search:
            search_lower = search.lower()
            if (
                search_lower not in case_text.lower()
                and search_lower not in debate_title.lower()
                and search_lower not in d.get("debate_id", "").lower()
            ):
                continue

        items.append(
            _build_debate_item(
                d,
                debates,
                tenant_id=tenant_id,
                tenant_name=tenant_name,
                case_id=case_id,
                case_title=case_title,
                tags=tags,
            )
        )

    return items[offset : offset + limit]


@router.post(
    "/tenants/{tenant_id}/cases/{case_id}/debates",
    response_model=DebateResponse,
    status_code=201,
)
async def create_case_debate(
    tenant_id: str,
    case_id: str,
    request: DebateRequest,
    audit: AuditService = Depends(get_audit_service),
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
) -> DebateResponse:
    """Create a new debate within a case (status = pending)."""
    _check_tenant_access(user, tenant_id)
    store = _get_debate_store_for_case(tenant_id, case_id, case_store)
    debate_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    debate = {
        "debate_id": debate_id,
        "status": DebateStatus.PENDING,
        "title": "",
        "request": request,
        "max_rounds": request.max_rounds,
        "current_round": 0,
        "rounds": [],
        "created_at": now,
        "updated_at": now,
        "result": None,
    }
    store.put(debate_id, debate)
    return DebateResponse(debate_id=debate_id, status=DebateStatus.PENDING, title="", created_at=now)


@router.get("/tenants/{tenant_id}/cases/{case_id}/debates/{debate_id}", response_model=DebateStatusResponse)
async def get_case_debate(
    tenant_id: str,
    case_id: str,
    debate_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
) -> DebateStatusResponse:
    """Get a single debate's status and progress."""
    _check_tenant_access(user, tenant_id)
    from backend.services.debate_workflow import build_rag_preview, extract_rag_info

    store = _get_debate_store_for_case(tenant_id, case_id, case_store)
    debate = store.get(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")

    req = debate.get("request", {})
    max_rounds = getattr(req, "max_rounds", None) if hasattr(req, "max_rounds") else req.get("max_rounds", 3) if isinstance(req, dict) else 3

    if hasattr(req, "case"):
        case_text = req.case.text
        language = getattr(req, "language", "de")
        llm_profile_id = req.llm_profile_id
    elif isinstance(req, dict):
        case_text = req.get("case", {}).get("text", "") or ""
        language = req.get("language", "de")
        llm_profile_id = req.get("llm_profile_id", "")
    else:
        case_text = ""
        language = "de"
        llm_profile_id = ""

    result = debate.get("result")
    consensus = result.get("final_consensus") if isinstance(result, dict) else None
    anomalies = result.get("anomalies", []) if isinstance(result, dict) else []

    project = get_project_store().get(case_id)
    project_name = project.name if project else case_id

    document_ids, rag_auto_retrieve = extract_rag_info(req)
    rag_enabled = bool(document_ids) or rag_auto_retrieve
    rag_preview = build_rag_preview(case_id, document_ids) if document_ids else ""

    from backend.workflow.hitl.api import get_active_interrupt, get_hitl_config
    from backend.workflow.hitl.api import is_paused as hitl_is_paused

    hitl_config = get_hitl_config(debate_id)
    hitl_enabled = hitl_config.get("hitl_enabled", False)
    hitl_mode = hitl_config.get("hitl_mode", "off")
    paused = hitl_is_paused(debate_id)
    active_interrupt = get_active_interrupt(debate_id)

    result_interactions = result.get("interactions", []) if isinstance(result, dict) else []

    fork_info = debate.get("fork_info")
    parent_id = fork_info.get("parent_debate_id") if isinstance(fork_info, dict) else None

    return DebateStatusResponse(
        debate_id=debate["debate_id"],
        status=debate["status"],
        title=debate.get("title", ""),
        current_round=debate.get("current_round", 0),
        max_rounds=max_rounds,
        consensus_score=consensus,
        rounds=[RoundData(**r) for r in debate.get("rounds", [])],
        created_at=debate.get("created_at", datetime.now(UTC)),
        updated_at=debate.get("updated_at", datetime.now(UTC)),
        case_text=case_text,
        language=language,
        prompt_language=debate.get("prompt_language", language),
        llm_profile_id=llm_profile_id,
        llm_profile_model=_resolve_llm_model(llm_profile_id, case_id),
        anomalies=anomalies,
        project_id=case_id,
        project_name=project_name,
        rag_enabled=rag_enabled,
        rag_document_count=len(document_ids),
        rag_context_preview=rag_preview,
        hitl_enabled=hitl_enabled,
        hitl_mode=hitl_mode,
        is_paused=paused,
        has_active_interrupt=active_interrupt is not None,
        total_interactions=len(result_interactions),
        parent_debate_id=parent_id,
        session_id=debate.get("session_id"),
        is_mvp=debate.get("is_mvp", False),
    )


@router.post("/tenants/{tenant_id}/cases/{case_id}/debates/{debate_id}/start", response_model=DebateStatusResponse)
async def start_case_debate(
    tenant_id: str,
    case_id: str,
    debate_id: str,
    background_tasks: BackgroundTasks,
    audit: AuditService = Depends(get_audit_service),
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
) -> DebateStatusResponse:
    """Start a pending debate — launches the workflow in a background task."""
    _check_tenant_access(user, tenant_id)
    from backend.services.debate_workflow import extract_rag_info
    from backend.tasks.dispatch import dispatch_debate_task

    store = _get_debate_store_for_case(tenant_id, case_id, case_store)
    debate = store.get(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")

    if debate["status"] != DebateStatus.PENDING:
        raise HTTPException(status_code=409, detail=f"Debate is already {debate['status'].value}")

    debate["status"] = DebateStatus.RUNNING
    debate["updated_at"] = datetime.now(UTC)
    store.put(debate_id, debate)

    dispatch_debate_task(background_tasks, debate_id, case_id, audit, store)

    req = debate.get("request", {})
    max_rounds = getattr(req, "max_rounds", None) if hasattr(req, "max_rounds") else req.get("max_rounds", 3) if isinstance(req, dict) else 3

    if hasattr(req, "case"):
        case_text = req.case.text
        language = getattr(req, "language", "de")
        llm_profile_id = req.llm_profile_id
    elif isinstance(req, dict):
        case_text = req.get("case", {}).get("text", "") or ""
        language = req.get("language", "de")
        llm_profile_id = req.get("llm_profile_id", "")
    else:
        case_text = ""
        language = "de"
        llm_profile_id = ""

    document_ids, rag_auto_retrieve = extract_rag_info(req)
    rag_enabled = bool(document_ids) or rag_auto_retrieve

    return DebateStatusResponse(
        debate_id=debate["debate_id"],
        status=debate["status"],
        title=debate.get("title", ""),
        current_round=debate.get("current_round", 0),
        max_rounds=max_rounds,
        consensus_score=None,
        rounds=[],
        created_at=debate.get("created_at", datetime.now(UTC)),
        updated_at=debate.get("updated_at", datetime.now(UTC)),
        case_text=case_text,
        language=language,
        prompt_language=language,
        llm_profile_id=llm_profile_id,
        rag_enabled=rag_enabled,
        rag_document_count=len(document_ids),
    )


@router.delete("/tenants/{tenant_id}/cases/{case_id}/debates/{debate_id}")
async def delete_case_debate(
    tenant_id: str,
    case_id: str,
    debate_id: str,
    audit: AuditService = Depends(get_audit_service),
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
) -> dict:
    """Delete a debate and its associated audit events."""
    _check_tenant_access(user, tenant_id)
    store = _get_debate_store_for_case(tenant_id, case_id, case_store)
    debate = store.get(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")

    status = debate.get("status")
    status_value = status.value if hasattr(status, "value") else status
    if status_value == "running":
        raise HTTPException(status_code=409, detail="Cannot delete a running debate")

    deleted_events = audit.delete_events(debate_id)
    store.delete(debate_id)

    from backend.workflow.hitl.api import cleanup_hitl_state

    cleanup_hitl_state(debate_id)

    logger.info("Deleted debate %s from case %s (%d audit events)", debate_id, case_id, deleted_events)
    return {"detail": "Debate deleted", "debate_id": debate_id}


@router.post("/tenants/{tenant_id}/cases/{case_id}/debates/{debate_id}/cancel")
async def cancel_case_debate(
    tenant_id: str,
    case_id: str,
    debate_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
) -> dict:
    """Cancel a running debate (idempotent)."""
    _check_tenant_access(user, tenant_id)
    from backend.services.debate_workflow import mark_cancelled

    store = _get_debate_store_for_case(tenant_id, case_id, case_store)
    debate = store.get(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")

    status = debate.get("status")
    status_val = status.value if hasattr(status, "value") else status

    if status_val in ("completed", "failed"):
        return {"status": status_val, "message": f"Debate already {status_val}"}

    mark_cancelled(debate_id)
    logger.info("Debate %s in case %s cancellation requested", debate_id, case_id)
    return {"status": "ok", "message": "Cancellation requested"}


@router.post("/tenants/{tenant_id}/cases/{case_id}/debates/{debate_id}/force-reset")
async def force_reset_case_debate(
    tenant_id: str,
    case_id: str,
    debate_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
) -> dict:
    """Force-reset a stuck 'running' debate to 'failed' (idempotent)."""
    _check_tenant_access(user, tenant_id)
    from datetime import UTC, datetime

    store = _get_debate_store_for_case(tenant_id, case_id, case_store)
    debate = store.get(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")

    status = debate.get("status")
    status_val = status.value if hasattr(status, "value") else status
    if status_val != "running":
        return {"status": status_val, "message": f"Debate is not running (current status: {status_val})"}

    store.update(
        debate_id,
        status=DebateStatus.FAILED,
        updated_at=datetime.now(UTC),
        result={"error": "Force-reset: debate was stuck in 'running' state"},
    )
    logger.info("Force-reset debate %s in case %s from 'running' to 'failed'", debate_id, case_id)
    return {"status": "ok", "message": "Debate reset to 'failed'"}


@router.post("/tenants/{tenant_id}/cases/{case_id}/debates/{debate_id}/oob", response_model=OOBInputResponse)
async def submit_case_oob_input(
    tenant_id: str,
    case_id: str,
    debate_id: str,
    body: OOBInputBody,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
) -> OOBInputResponse:
    """Submit an out-of-band input for a running debate in a case."""
    _check_tenant_access(user, tenant_id)
    from backend.api.events import publish_async
    from backend.services.debate_workflow import enqueue_oob

    store = _get_debate_store_for_case(tenant_id, case_id, case_store)
    debate = store.get(debate_id)
    if not debate:
        raise HTTPException(status_code=404, detail="Debate not found")

    status = debate.get("status")
    status_val = status.value if hasattr(status, "value") else status
    if status_val != "running":
        raise HTTPException(status_code=409, detail=f"Debate is not running (current status: {status_val})")

    oob_id = str(uuid.uuid4())
    oob_entry = {
        "oob_id": oob_id,
        "content": body.content,
        "target": body.target.model_dump(),
        "urgency": body.urgency,
        "status": "pending",
        "timestamp": datetime.now(UTC).isoformat(),
    }

    enqueue_oob(debate_id, oob_entry)

    session_id = debate.get("session_id", debate_id)
    await publish_async(
        session_id,
        "oob_input",
        {
            "type": "oob_input",
            "oob_id": oob_id,
            "content": body.content,
            "target": body.target.model_dump(),
            "urgency": body.urgency,
        },
    )

    logger.info("OOB input %s queued for debate %s in case %s", oob_id, debate_id, case_id)
    return OOBInputResponse(oob_id=oob_id, status="pending", target_resolved=body.target.type.value)


# ---------------------------------------------------------------------------
# Fork endpoint
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/cases/{case_id}/debates/{debate_id}/forks", response_model=list[DebateListItem])
async def list_case_forks(
    tenant_id: str,
    case_id: str,
    debate_id: str,
    limit: int = 50,
    offset: int = 0,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
) -> list[DebateListItem]:
    """List all forks originating from a given debate in a case."""
    _check_tenant_access(user, tenant_id)
    store = _get_debate_store_for_case(tenant_id, case_id, case_store)
    debates = store.list_all(limit=limit + offset)

    project = get_project_store().get(case_id)
    project_name = project.name if project else case_id

    items = []
    for d in debates:
        fork_info = d.get("fork_info")
        if not isinstance(fork_info, dict):
            continue
        parent = fork_info.get("parent_debate_id")
        if parent != debate_id:
            continue

        req = d.get("request", {})
        if hasattr(req, "case"):
            case_text = req.case.text
        elif isinstance(req, dict):
            case_text = req.get("case", {}).get("text", "") or ""
        else:
            case_text = ""

        result = d.get("result")
        consensus = result.get("final_consensus") if isinstance(result, dict) else None

        items.append(
            DebateListItem(
                debate_id=d["debate_id"],
                status=d["status"],
                title=d.get("title", ""),
                current_round=d.get("current_round", 0),
                max_rounds=d.get("max_rounds", 3),
                consensus_score=consensus,
                case_preview=case_text[:120],
                case_text=case_text,
                created_at=d.get("created_at", datetime.now(UTC)),
                updated_at=d.get("updated_at", datetime.now(UTC)),
                project_id=case_id,
                project_name=project_name,
                parent_debate_id=parent,
            )
        )

    return items[offset : offset + limit]


# ---------------------------------------------------------------------------
# DMS endpoints — /tenants/{tid}/cases/{cid}/dms
# ---------------------------------------------------------------------------


def _case_scope_id(tenant_id: str, case_id: str) -> str:
    """Canonical DMS scope id for a case — the bare ``case_id``.

    History: the case-scoped DMS used to bind documents to the synthetic
    scope ``f"case:{tenant_id}:{case_id}"`` while the debate/workflow RAG
    path filters by the bare ``case_id`` — so agents retrieved zero chunks
    ("Dokument nicht im RAG abrufbar"). The case-scoped factory now binds
    the bare ``case_id`` (a UUID; the per-case directory already provides
    tenant isolation), and migration v024 rewrites legacy synthetic-scope
    data.

    This helper exists so the scope id is derived in exactly ONE place
    (used by ``_get_dms_for_case`` and the interactive agent worker). A
    future refactor that re-introduces a synthetic scope here will be
    caught by ``tests/rag_regression/test_rag_scope_id_regression.py``.
    """
    return case_id


def _get_dms_for_case(tenant_id: str, case_id: str, case_store: CaseStore):
    """Get or create a DMS instance for a case.

    Multi-tenant safety:
      - The DMS cache is keyed by ``(tenant_id, case_id)`` (not by
        ``case_id`` alone), which prevents a case_id in one tenant
        from colliding with a project_id (or another case_id) in a
        different tenant.
      - Validates the case belongs to the given tenant before returning.
      - The DMS binds to the bare ``case_id`` (see ``_case_scope_id``) so
        the legacy debate/workflow RAG path — which filters ChromaDB by the
        bare ``case_id`` — finds case-scoped documents. Migration v024
        rewrites chunks from the old synthetic scope on startup.
    """
    from backend.services.dms.config import load_dms_config
    from backend.services.dms.service import DMS, _dms_cache, _dms_cache_lock

    case = case_store.get(tenant_id, case_id)
    if not case or case.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Case not found")

    cache_key = ("case", tenant_id, case_id)
    with _dms_cache_lock:
        if cache_key in _dms_cache:
            return _dms_cache[cache_key]
        # Fall back to the bare-case_id alias entry possibly created by
        # ``get_dms_for_project(case_id)`` (debate/workflow RAG path) —
        # either way, at most one DMS instance exists per case (§2.8).
        if case_id in _dms_cache and isinstance(_dms_cache[case_id], DMS):
            _dms_cache[cache_key] = _dms_cache[case_id]
            return _dms_cache[case_id]

        case_dir = case_store.get_case_dir(tenant_id, case_id)
        dms_dir = case_dir / "dms"
        dms_dir.mkdir(parents=True, exist_ok=True)

        try:
            dms_config = load_dms_config()
        except Exception:
            dms_config = {}

        # Bind the DMS to the canonical case scope. This way
        # ``MetadataIndex`` (which tags every ChromaDB document with
        # ``project_id``) and the ``rag_context`` table use the SAME id as
        # the legacy debate/workflow RAG path (bare case_id), so agents
        # can retrieve case documents. See ``_case_scope_id`` for the
        # history of the synthetic-scope split-brain this replaces.
        scope_id = _case_scope_id(tenant_id, case_id)

        from datetime import datetime

        dms = DMS(
            db_path=str(dms_dir / "dms.db"),
            chroma_path=str(dms_dir / "chroma_db"),
            config=dms_config,
            project_id=scope_id,
        )

        # Ensure the synthetic project_id exists in the projects table so
        # FOREIGN KEY constraints on documents.project_id are satisfied.
        if not dms.db.get_project(scope_id):
            case_name = case.name if hasattr(case, "name") else scope_id
            dms.db.execute(
                "INSERT OR IGNORE INTO projects (id, name, description, created_at, metadata_json) VALUES (?, ?, ?, ?, ?)",
                (scope_id, case_name, "", datetime.now().isoformat(), ""),
            )
            dms.db.commit()

        _dms_cache[cache_key] = dms
        # Alias under the bare case_id string key too, so the legacy
        # factory (``get_dms_for_project(case_id)`` — used by the debate/
        # workflow RAG path via ``resolve_rag_context``) returns THIS
        # instance instead of opening a second DMS over the same
        # directory: one case, one binding, one SQLite connection, one
        # Chroma client (§2.8 of the 2026-08-31 review). The tuple key
        # above remains authoritative for tenant-scoped lookups; it
        # cannot collide with the string key by construction.
        _dms_cache.setdefault(scope_id, dms)
        return dms


@router.get("/tenants/{tenant_id}/cases/{case_id}/dms/documents")
def list_case_documents(
    tenant_id: str,
    case_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """List documents in the case DMS."""
    _check_tenant_access(user, tenant_id)
    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    return dms.list_documents(dms._project_id)


@router.get("/tenants/{tenant_id}/cases/{case_id}/dms/documents/{document_id}")
def get_case_document(
    tenant_id: str,
    case_id: str,
    document_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Get a single document with its content for viewing.

    Uses ``get_document_content`` (metadata + joined text chunks +
    ``in_rag`` flag) — the same contract as the legacy
    ``/dms/documents/{id}`` route. Previously this returned the bare DB
    row, so the document viewer's text panel was always empty via the
    tenant/case flow (§2.6 of the 2026-08-31 review).
    """
    _check_tenant_access(user, tenant_id)
    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    doc = dms.get_document_content(document_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")
    return doc


class UpdateCaseDocumentTextRequest(BaseModel):
    """Request body for case-scoped document text updates."""

    text: str


@router.put("/tenants/{tenant_id}/cases/{case_id}/dms/documents/{document_id}/text")
def update_case_document_text(
    tenant_id: str,
    case_id: str,
    document_id: str,
    body: UpdateCaseDocumentTextRequest,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Replace a document's extracted text (re-chunks and re-indexes).

    Tenant-scoped twin of the legacy ``PUT /dms/documents/{id}/text`` —
    the frontend previously had to fall back to the legacy route
    ("No tenant-scoped equivalent yet" in ``document.js``).
    """
    _check_tenant_access(user, tenant_id)
    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    result = dms.update_document_text(document_id, body.text)
    if not result:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")
    return result


class MoveCaseDocumentRequest(BaseModel):
    """Request body for case-scoped document moves."""

    target_project_id: str


@router.post("/tenants/{tenant_id}/cases/{case_id}/dms/documents/{document_id}/move")
def move_case_document(
    tenant_id: str,
    case_id: str,
    document_id: str,
    body: MoveCaseDocumentRequest,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Move a document to another project's DMS (tenant-scoped twin).

    Mirrors the legacy ``POST /dms/documents/{id}/move``. The target must
    resolve within this deployment's tenant roots (``get_case_dir``),
    so cross-tenant moves cannot be smuggled through the target id.
    """
    _check_tenant_access(user, tenant_id)
    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    if not dms.get_document(document_id):
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found")

    if body.target_project_id == dms._project_id:
        raise HTTPException(status_code=400, detail="Source and target project are the same")

    from backend.api.deps import get_case_dir
    from backend.services.dms.service import get_dms_for_project

    try:
        get_case_dir(body.target_project_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail=f"Target project '{body.target_project_id}' not found")

    try:
        target_dms = get_dms_for_project(body.target_project_id)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Target project '{body.target_project_id}' not found")

    moved = dms.move_document_to(document_id, target_dms, target_dms._project_id)
    if not moved:
        raise HTTPException(status_code=400, detail="Failed to move document")
    return {"detail": "Document moved", "moved": document_id, "target_project_id": body.target_project_id}


@router.post("/tenants/{tenant_id}/cases/{case_id}/dms/documents")
async def upload_case_document(
    tenant_id: str,
    case_id: str,
    file: UploadFile = File(...),
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Upload a document to the case DMS.

    Ingestion (OCR/PDF parse/chunk/index) runs on the bounded DMS ingest
    pool via ``add_document_async`` — awaited here, so the event loop stays
    free for concurrent requests during processing.
    """
    _check_tenant_access(user, tenant_id)
    import tempfile

    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    filename = file.filename or "uploaded.pdf"

    # §4.5 (2026-08-31 review): this route previously had NO size check
    # and buffered the whole body in RAM. Stream to disk in 1 MiB chunks
    # and abort as soon as ``max_file_size_mb`` is exceeded (same limit
    # and 413 semantics as the legacy route).
    from backend.services.dms.config import load_dms_config

    try:
        dms_config = load_dms_config()
        max_bytes = dms_config.get("max_file_size_mb", 50) * 1024 * 1024
    except Exception:
        max_bytes = 50 * 1024 * 1024  # 50 MB default

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix)
    try:
        total = 0
        while True:
            chunk = await file.read(1024 * 1024)  # 1 MiB at a time
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=f"File too large ({total} bytes read so far). "
                    f"Maximum allowed: {max_bytes // (1024 * 1024)} MB",
                )
            tmp.write(chunk)
        tmp.close()
        result = await dms.add_document_async(tmp.name, filename=filename)
    finally:
        tmp.close() if not tmp.closed else None
        Path(tmp.name).unlink(missing_ok=True)
    if not result.get("doc_id"):
        raise HTTPException(status_code=500, detail=result.get("error") or "Failed to upload document")
    if result.get("error"):
        # Same contract as the legacy DMS route: processing errors
        # (e.g. OCR failure on an image/scanned PDF) → 422.
        raise HTTPException(status_code=422, detail=result["error"])
    return result


@router.delete("/tenants/{tenant_id}/cases/{case_id}/dms/documents/{document_id}")
def delete_case_document(
    tenant_id: str,
    case_id: str,
    document_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Delete a document from the case DMS."""
    _check_tenant_access(user, tenant_id)
    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    dms.delete_document(document_id)
    return {"detail": "Document deleted"}


@router.post("/tenants/{tenant_id}/cases/{case_id}/dms/documents/{document_id}/rag")
def add_case_document_rag(
    tenant_id: str,
    case_id: str,
    document_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Add a document to the RAG index for a case.

    Multi-tenant safety: the underlying ``add_to_rag_context`` validates
    that the document belongs to the active project; if it does not, the
    call returns 404 rather than silently attaching a foreign document.
    """
    _check_tenant_access(user, tenant_id)
    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    if dms.get_document(document_id) is None:
        raise HTTPException(status_code=404, detail=f"Document '{document_id}' not found in this case")
    added = dms.add_to_rag_context(document_id)
    if not added:
        raise HTTPException(status_code=400, detail="Document already in RAG context")
    return {"detail": "Document added to RAG index"}


@router.delete("/tenants/{tenant_id}/cases/{case_id}/dms/documents/{document_id}/rag")
def remove_case_document_rag(
    tenant_id: str,
    case_id: str,
    document_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Remove a document from the RAG index for a case."""
    _check_tenant_access(user, tenant_id)
    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    removed = dms.remove_from_rag_context(document_id)
    if not removed:
        raise HTTPException(status_code=400, detail="Document not in RAG context")
    return {"detail": "Document removed from RAG index"}


@router.get("/tenants/{tenant_id}/cases/{case_id}/dms/rag/search")
def search_case_rag(
    tenant_id: str,
    case_id: str,
    query: str = Query(default=""),
    limit: int = Query(default=5),
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Search the RAG index for a case (hybrid retriever, project-scoped)."""
    _check_tenant_access(user, tenant_id)
    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    return {"results": dms.get_rag_context(query, project_id=dms._project_id, k=limit)}


@router.get("/tenants/{tenant_id}/cases/{case_id}/dms/rag/preview")
def preview_case_rag(
    tenant_id: str,
    case_id: str,
    query: str = Query(default=""),
    document_ids: str = Query(default=""),
    include_analysis: bool = Query(default=True),
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Preview the RAG context exactly as a debate on this case would receive it.

    Mirrors ``resolve_rag_context`` (the same code path a debate run
    takes), bound to the canonical case scope so the preview is immune
    to the historical scope-id split-brain (§2.5 of the 2026-08-31
    review: the frontend shipped a preview panel that called this
    route before it existed — guaranteed 404).

    Query params match ``frontend/src/lib/api/document.js`` ``getRagPreview``:
    ``query``, ``document_ids`` (comma-separated), ``include_analysis``
    (frontend sends it only when false).
    """
    _check_tenant_access(user, tenant_id)
    from backend.services.debate.debate_rag import resolve_rag_context

    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    ids = [d for d in document_ids.split(",") if d] or None
    rag_context, document_count = resolve_rag_context(
        project_id=dms._project_id,
        case_text=query,
        document_ids=ids,
        rag_auto_retrieve=bool(query.strip()),
        include_document_analysis=include_analysis,
    )
    return {
        "rag_context": rag_context,
        "document_count": document_count,
        "stats": {
            "document_count": document_count,
            "rag_chars": len(rag_context),
            "rag_tokens_approx": len(rag_context) // 4,
        },
    }


# ---------------------------------------------------------------------------
# DMS Analysis
# ---------------------------------------------------------------------------


@router.post("/tenants/{tenant_id}/cases/{case_id}/dms/analyze")
async def analyze_case_documents(
    tenant_id: str,
    case_id: str,
    language: str = Query("de", description="Language for analysis content"),
    mode: str = Query("full", description="Analysis mode: 'full' or 'update'"),
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Analyze all documents in the case DMS."""
    _check_tenant_access(user, tenant_id)
    import asyncio

    from backend.services.dms.document_analyzer import (
        analyze_documents as run_document_analysis,
        load_analysis,
        save_analysis,
        update_analysis,
    )

    dms = _get_dms_for_case(tenant_id, case_id, case_store)
    project_id = dms._project_id
    case_dir = case_store.get_case_dir(tenant_id, case_id)

    documents = dms.list_documents(project_id)
    if not documents:
        raise HTTPException(status_code=400, detail="No documents to analyze")

    # §4.8: global ProfileService singleton (analysis reads profiles
    # only — no project override merge needed here, matching the old
    # bare ``ProfileService()`` semantics without per-request rebuild).
    from backend.api.deps import get_profile_service

    profile_service = get_profile_service()

    if mode == "update":
        existing = load_analysis(case_dir)
        if not existing:
            raise HTTPException(
                status_code=400,
                detail="No existing analysis found. Run full analysis first.",
            )

        known_filenames = {d.get("filename", "") for d in existing.get("documents", [])}
        new_documents = [d for d in documents if d.get("filename", "") not in known_filenames]

        if not new_documents:
            return {"status": "ok", "message": "No new documents to analyze", "analysis": existing}

        document_texts = []
        for doc in new_documents:
            content = dms.get_document_content(doc["id"])
            text = (content or {}).get("text_content", "")
            if text:
                document_texts.append({"filename": doc.get("filename", "unknown"), "text": text})

        if not document_texts:
            return {"status": "ok", "message": "No extractable text in new documents", "analysis": existing}

        analysis = await asyncio.to_thread(update_analysis, existing, document_texts, profile_service=profile_service, language=language)
        if "error" in analysis:
            raise HTTPException(status_code=500, detail=analysis["error"])

        try:
            save_analysis(case_dir, analysis)
        except OSError as e:
            raise HTTPException(status_code=500, detail=f"Failed to save analysis: {e}")
        return {"status": "ok", "mode": "update", "analysis": analysis}

    # full mode
    document_texts = []
    for doc in documents:
        content = dms.get_document_content(doc["id"])
        text = (content or {}).get("text_content", "")
        if text:
            document_texts.append({"filename": doc.get("filename", "unknown"), "text": text})

    if not document_texts:
        raise HTTPException(status_code=400, detail="No extractable text found in documents")

    analysis = await asyncio.to_thread(run_document_analysis, document_texts, profile_service=profile_service, language=language)
    if "error" in analysis:
        raise HTTPException(status_code=500, detail=analysis["error"])

    try:
        save_analysis(case_dir, analysis)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Failed to save analysis: {e}")

    return {"status": "ok", "mode": "full", "analysis": analysis}


@router.get("/tenants/{tenant_id}/cases/{case_id}/dms/analyze")
def get_case_analysis(
    tenant_id: str,
    case_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Get the latest analysis for the case DMS."""
    _check_tenant_access(user, tenant_id)
    from backend.services.dms.document_analyzer import load_analysis

    case_dir = case_store.get_case_dir(tenant_id, case_id)
    analysis = load_analysis(case_dir)
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis found. Run analysis first.")
    return {"status": "ok", "analysis": analysis}


class AnalysisExportRequest(BaseModel):
    """Request body for analysis export."""

    format: str


@router.post("/tenants/{tenant_id}/cases/{case_id}/dms/analyze/export")
async def export_case_analysis(
    tenant_id: str,
    case_id: str,
    body: AnalysisExportRequest,
    case_store: CaseStore = Depends(get_case_store),
    project_store: ProjectStore = Depends(get_project_store),
    user: User = Depends(get_current_user),
):
    """Export the document analysis as PDF, ODT, or Markdown."""
    _check_tenant_access(user, tenant_id)
    from fastapi.responses import FileResponse

    case_dir = case_store.get_case_dir(tenant_id, case_id)
    project = project_store.get(case_id)
    project_name = getattr(project, "name", case_id) if project else case_id

    from backend.services.dms.document_analyzer import load_analysis

    analysis = load_analysis(case_dir)
    if not analysis:
        raise HTTPException(status_code=404, detail="No analysis found. Run analysis first.")

    fmt = body.format.lower()
    if fmt not in ("pdf", "odt", "md"):
        raise HTTPException(status_code=422, detail=f"Unsupported format: {fmt}")

    import tempfile as _tf

    from jinja2 import Environment, FileSystemLoader

    templates_dir = Path(__file__).resolve().parent.parent.parent.parent / "templates" / "print"
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
    template = env.get_template("document_analysis.html")
    now = datetime.now(UTC)

    i18n = _load_analysis_i18n("de")
    html = template.render(
        project_name=project_name,
        analysis=analysis,
        language="de",
        generated=now.strftime("%Y-%m-%d %H:%M UTC"),
        i18n=i18n,
    )

    stem = f"analysis_{case_id[:8]}_{now.strftime('%Y%m%d_%H%M')}"

    if fmt == "pdf":
        from weasyprint import HTML

        tmp = _tf.NamedTemporaryFile(suffix=".pdf", delete=False)
        HTML(string=html).write_pdf(tmp.name)
        media_type = "application/pdf"
        filename = f"{stem}.pdf"
    elif fmt == "odt":
        tmp = _tf.NamedTemporaryFile(suffix=".odt", delete=False)
        try:
            import pypandoc

            pypandoc.convert_text(html, "odt", format="html", outputfile=tmp.name)
        except ImportError:
            tmp.write(html.encode("utf-8"))
        media_type = "application/vnd.oasis.opendocument.text"
        filename = f"{stem}.odt"
    elif fmt == "md":
        from backend.services.output.html_to_md import html_to_markdown

        md = html_to_markdown(html)
        tmp = _tf.NamedTemporaryFile(suffix=".md", delete=False)
        tmp.write(md.encode("utf-8"))
        media_type = "text/markdown"
        filename = f"{stem}.md"

    tmp.close()
    return FileResponse(tmp.name, media_type=media_type, filename=filename)


# ---------------------------------------------------------------------------
# Audit endpoints — /tenants/{tid}/cases/{cid}/audit
# ---------------------------------------------------------------------------


@router.get("/tenants/{tenant_id}/cases/{case_id}/audit/{debate_id_or_title}")
def list_case_audit_events(
    tenant_id: str,
    case_id: str,
    debate_id_or_title: str,
    limit: int = 100,
    offset: int = 0,
    audit: AuditService = Depends(get_audit_service),
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """List audit events for a debate within a case.

    Falls back to workflow audit_log table for MVP debates (same logic as
    the legacy ``/api/v1/audit`` endpoint).
    """
    _check_tenant_access(user, tenant_id)
    from backend.api.routers.audit import (
        _enrich_events_with_debate_data,
        _resolve_debate_id,
        _transform_workflow_audit_events,
    )

    debate_id, debate_data = _resolve_debate_id(debate_id_or_title, case_id)
    events = audit.get_events(debate_id=debate_id)
    if events:
        return _enrich_events_with_debate_data(events, debate_data)

    # Fallback: check workflow audit_log table for MVP debates
    if debate_data and debate_data.get("session_id"):
        from backend.workflow.audit_logger import get_audit_logger

        session_id = debate_data["session_id"]
        al = get_audit_logger()
        wf_events = al.get_audit_log(session_id)
        if wf_events:
            return _transform_workflow_audit_events(wf_events, session_id)

    return []


# ---------------------------------------------------------------------------
# Workflow endpoints (case-scoped)
# ---------------------------------------------------------------------------


@router.post("/tenants/{tenant_id}/cases/{case_id}/workflows/{workflow_id}/start")
async def start_case_workflow(
    tenant_id: str,
    case_id: str,
    workflow_id: str,
    body: dict,
    background_tasks: BackgroundTasks,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Start a workflow within a case context.

    Delegates to the workflow_exec router but resolves the project_id
    from the tenant/case path.
    """
    _check_tenant_access(user, tenant_id)
    case = case_store.get(tenant_id, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    # Import and delegate to the existing start_workflow logic
    from backend.api.routers.workflow_exec import StartWorkflowRequest, start_workflow

    project_id = case_id
    req = StartWorkflowRequest(
        context=body.get("context", ""),
        language=body.get("language"),
        project_id=project_id,
        max_rounds=body.get("max_rounds", 10),
        threshold=body.get("threshold", 0.7),
        document_ids=body.get("document_ids", []),
        rag_auto_retrieve=body.get("rag_auto_retrieve", False),
        include_document_analysis=body.get("include_document_analysis", False),
    )
    return await start_workflow(workflow_id, req, background_tasks, project_id=project_id)


@router.get("/tenants/{tenant_id}/cases/{case_id}/workflows/{session_id}/state")
async def get_case_workflow_state(
    tenant_id: str,
    case_id: str,
    session_id: str,
    case_store: CaseStore = Depends(get_case_store),
    user: User = Depends(get_current_user),
):
    """Get workflow execution state within a case context."""
    _check_tenant_access(user, tenant_id)
    case = case_store.get(tenant_id, case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found")

    from backend.api.routers.workflow_exec import get_session_state

    return await get_session_state(session_id)


def _load_analysis_i18n(language: str) -> dict:
    """Load i18n labels for the document analysis template."""
    labels = {
        "case_summary_label": ("Fallzusammenfassung" if language == "de" else "Case Summary"),
        "key_facts_label": ("Wichtige Fakten" if language == "de" else "Key Facts"),
        "parties_label": ("Parteien" if language == "de" else "Parties"),
        "timeline_label": ("Zeitstrahl" if language == "de" else "Timeline"),
        "key_issues_label": ("Hauptstreitpunkte" if language == "de" else "Key Issues"),
        "documents_label": ("Dokumentübersichten" if language == "de" else "Document Summaries"),
    }
    return labels
