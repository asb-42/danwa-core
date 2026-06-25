"""Celery task for generic workflow execution.

The full ``initial_state`` and ``compiled_workflow`` are NOT serialized
to Celery (they contain non-serializable LangGraph objects). Instead,
the worker re-loads them from the project store and re-compiles the
workflow definition.  This requires that the ``workflow_id`` points to
a resolvable ``WorkflowDefinition`` in the BlueprintRepository and that
the session's initial state was already persisted to the snapshot store.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _get_celery_app():
    """Return (or lazily create) celery app."""
    from backend.tasks.celery_app import get_celery_app

    app = get_celery_app()
    if app is None:
        raise RuntimeError("Celery not configured")
    return app


def _create_task():
    """Lazily create the Celery task (only if Celery is available)."""
    try:
        app = _get_celery_app()

        @app.task(
            bind=True,
            name="backend.tasks.workflow.run_workflow_task",
            max_retries=2,
            soft_time_limit=1800,
            time_limit=3600,
        )
        def run_workflow_task(self, session_id: str, workflow_id: str, project_id: str):
            """Execute a compiled workflow in a Celery worker.

            The worker re-loads the workflow definition from the
            BlueprintRepository, re-compiles it, and loads the initial
            state from the snapshot store.  This avoids serializing
            complex LangGraph objects through Celery's pickle/JSON layer.
            """
            logger.info("Celery worker executing workflow %s for session %s (project %s)", workflow_id, session_id, project_id)

            asyncio.run(_run_workflow_async(session_id, workflow_id, project_id))

        return run_workflow_task
    except Exception as e:
        logger.warning("Cannot create Celery workflow task: %s", e)
        return None


async def _run_workflow_async(session_id: str, workflow_id: str, project_id: str):
    """Async workflow execution — re-loads state from stores.

    1. Load the latest snapshot from StateSnapshotStore to get initial_state.
    2. Load the WorkflowDefinition from BlueprintRepository.
    3. Re-compile via WorkflowCompiler.
    4. Delegate to run_workflow_background with the reconstructed objects.
    """
    from backend.api.deps import get_case_dir
    from backend.blueprints.repository import BlueprintRepository
    from backend.workflow.state_snapshot import StateSnapshotStore
    from backend.workflow.workflow_compiler import WorkflowCompiler

    # 1. Load initial state from snapshot store (the most recent snapshot)
    snapshot_store = StateSnapshotStore()
    latest = snapshot_store.get_latest(session_id)

    if latest is None:
        # No snapshot saved yet — try loading from the session directory
        project_dir = get_case_dir(project_id)
        session_file = project_dir / "workflows" / f"{session_id}.json"
        if session_file.exists():
            import json

            initial_state = json.loads(session_file.read_text())
        else:
            logger.error("No initial state found for session %s — cannot execute via Celery", session_id)
            return
    else:
        initial_state = latest.get("state", {})

    # Ensure required fields
    initial_state.setdefault("session_id", session_id)
    initial_state.setdefault("workflow_id", workflow_id)

    # 2. Load WorkflowDefinition from BlueprintRepository
    blueprint_repo = BlueprintRepository()
    workflow_def = blueprint_repo.get_workflow_definition(workflow_id)

    if workflow_def is None:
        logger.error("Workflow definition %s not found in BlueprintRepository", workflow_id)
        return

    # 3. Re-compile the workflow
    compiler = WorkflowCompiler(blueprint_repo)
    compiled_workflow = compiler.compile(workflow_def)

    # 4. Execute via the standard background runner
    from backend.workflow.workflow_runner import run_workflow_background

    await run_workflow_background(
        session_id=session_id,
        workflow_id=workflow_id,
        project_id=project_id,
        initial_state=initial_state,
        compiled_workflow=compiled_workflow,
        snapshot_store=snapshot_store,
    )


# Module-level reference — None if Celery is not available
run_workflow_task = _create_task()
