"""Blueprint Canvas — Prompt Templates (read-only view over module prompts).

Prompt Templates are **not** stored in the database. They live as modules
in the ``danwa-modules`` repository (``agent-prompt-modifiers`` etc.) and
are loaded via :func:`backend.services.module_profile_sync.get_prompt_templates_from_modules`.

The frontend (``danwa-studio`` / ``PromptsView``) calls these endpoints,
so we expose them here as **read-only** to keep the URL contract stable.
Any mutation request returns ``405 Method Not Allowed`` — modules are the
source of truth. Editing happens in the module's profile file / manifest.

Extracted from ``backend.api.routers.blueprints`` to follow the
focused-router pattern (cf. ``llm_profiles.py``, ``workflow_definitions.py``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from backend.services.module_profile_sync import (
    get_prompt_templates_from_modules,
)

router = APIRouter()


def _load_all() -> list[dict]:
    """Load all prompt templates from enabled modules (delegates to modules/)."""
    return get_prompt_templates_from_modules()


@router.get("/prompt-templates", response_model=list[dict])
def list_prompt_templates(
    role: str | None = None,
    variant: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List prompt templates from enabled modules.

    Optional filters:
    - ``role``:    e.g. ``"strategist"``, ``"critic"``
    - ``variant``: e.g. ``"default"``, ``"kantian"``
    - ``limit``/``offset``: pagination (default limit 100)

    Items are marked ``_readonly=true`` because they originate from
    versioned module files; edits happen in the module's profile file.
    """
    items = _load_all()
    if role:
        items = [t for t in items if t.get("role") == role]
    if variant:
        items = [t for t in items if t.get("variant") == variant]
    return items[offset : offset + limit]


@router.get("/prompt-templates/{template_id}", response_model=dict)
def get_prompt_template(template_id: str) -> dict:
    """Get a single prompt template by ID (must exist in a module)."""
    for tpl in _load_all():
        if tpl.get("id") == template_id:
            return tpl
    raise HTTPException(status_code=404, detail=f"PromptTemplate '{template_id}' not found in any enabled module")


# ---------------------------------------------------------------------------
# Mutations are NOT supported — modules are the source of truth.
# We define explicit stub endpoints so the path returns 405 (not 404) for
# write methods, and include an ``Allow`` header per RFC 7231 §6.5.5.
# ---------------------------------------------------------------------------

_METHODS_GET = "GET"


@router.post("/prompt-templates", include_in_schema=True)
def create_prompt_template() -> JSONResponse:
    """Not supported. Prompt Templates live in danwa-modules; edit there."""
    return JSONResponse(
        status_code=405,
        content={
            "detail": "Prompt Templates are read-only. "
                      "Edit the module's profile in danwa-modules to change a prompt.",
        },
        headers={"Allow": _METHODS_GET},
    )


@router.put("/prompt-templates/{template_id}")
def update_prompt_template(template_id: str) -> JSONResponse:
    """Not supported. Prompt Templates live in danwa-modules; edit there."""
    return JSONResponse(
        status_code=405,
        content={
            "detail": "Prompt Templates are read-only. "
                      "Edit the module's profile in danwa-modules to change a prompt.",
            "template_id": template_id,
        },
        headers={"Allow": _METHODS_GET},
    )


@router.delete("/prompt-templates/{template_id}")
def delete_prompt_template(template_id: str) -> JSONResponse:
    """Not supported. Prompt Templates live in danwa-modules; remove there."""
    return JSONResponse(
        status_code=405,
        content={
            "detail": "Prompt Templates are read-only. "
                      "Remove the module from danwa-modules to delete a prompt.",
            "template_id": template_id,
        },
        headers={"Allow": _METHODS_GET},
    )
