"""CQRS Projectors for Interactive Debate Mode.

Projectors subscribe to the event stream and build materialized views
(Read Models) that the UI and LLMs query. They never read the full event log.

Projectors:
    - TreeProjector:     Lightweight graph for SvelteFlow UI
    - ContextProjector:  Structured facts + vectors for ContextSynthesizer
    - BudgetProjector:   Token tracking and cost control
    - SynthesisProjector: Final Markdown report generation

See ADR-001: Thin Events with CQRS Projectors.
"""

from backend.services.interactive.projectors.base import BaseProjector
from backend.services.interactive.projectors.budget_projector import BudgetProjector
from backend.services.interactive.projectors.context_projector import ContextProjector
from backend.services.interactive.projectors.synthesis_projector import SynthesisProjector
from backend.services.interactive.projectors.tree_projector import TreeProjector

__all__ = [
    "BaseProjector",
    "TreeProjector",
    "ContextProjector",
    "BudgetProjector",
    "SynthesisProjector",
    "ProjectorManager",
]


class ProjectorManager:
    """Orchestrates all projectors and dispatches events to them.

    Usage::

        manager = ProjectorManager(db_conn, embedding_store)
        manager.handle_event(event)  # Dispatches to all projectors
    """

    def __init__(self, conn, embedding_store=None):
        self.projectors: list[BaseProjector] = [
            TreeProjector(conn),
            ContextProjector(conn, embedding_store),
            BudgetProjector(conn),
            SynthesisProjector(conn),
        ]

    def handle_event(self, event) -> None:
        """Dispatch an event to all registered projectors."""
        for projector in self.projectors:
            projector.safe_handle(event)

    def get_tree_projector(self) -> TreeProjector:
        return self.projectors[0]  # type: ignore[return-value]

    def get_context_projector(self) -> ContextProjector:
        return self.projectors[1]  # type: ignore[return-value]

    def get_budget_projector(self) -> BudgetProjector:
        return self.projectors[2]  # type: ignore[return-value]

    def get_synthesis_projector(self) -> SynthesisProjector:
        return self.projectors[3]  # type: ignore[return-value]
