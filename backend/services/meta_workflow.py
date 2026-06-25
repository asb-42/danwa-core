"""MetaWorkflowService — LLM-powered meta-workflow reflection engine.

Analyzes DebateArtifacts from completed workflow sessions and generates
concrete OptimizationProposals with node/edge changes, rationale, and
risk assessment.
"""

from __future__ import annotations

import json
import logging

from backend.blueprints.repository import BlueprintRepository
from backend.models.artifact import DebateArtifact
from backend.models.optimization_proposal import (
    OptimizationProposal,
    ProposalCreatedBy,
    ProposalStatus,
)
from backend.repositories.proposal_repo import ProposalRepository

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
Du bist ein Workflow-Optimierungs-Agent. Du analysierst Debate-Artefakte \
und schlägst konkrete Änderungen an Workflows vor.

Antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text davor danach):
{
  "proposed_nodes": [
    {"node_id": "string", "action": "add|modify|remove", "config_changes": {"key": "value"}}
  ],
  "proposed_edges": [
    {"from": "node_a", "to": "node_b", "action": "add|remove"}
  ],
  "rationale": "Kurze Begründung der Änderungen",
  "risk_assessment": "Risikobewertung (Gering/Mittel/Hoch)",
  "estimated_impact": "Erwarteter Nutzen"
}

Regeln:
- Analysiere die Transkript-Qualität, Konsens-Scores und Runden-Verlauf.
- Identifiere schwache Stellen: z.B. fehlende Agenten, zu wenige Runden,
  schlechte Konsenswerte, oder redundanten Output.
- Vorschläge müssen spezifisch und umsetzbar sein.
- Bei Unsicherheit: lieber keine Änderung vorschlagen als eine schlechte.
"""


class MetaWorkflowService:
    """LLM-powered meta-workflow reflection engine.

    Analyzes DebateArtifacts and generates OptimizationProposals with
    concrete workflow improvements.
    """

    def __init__(
        self,
        blueprint_repo: BlueprintRepository,
        proposal_repo: ProposalRepository,
    ) -> None:
        """Initialise MetaWorkflowService."""
        self._blueprint_repo = blueprint_repo
        self._proposal_repo = proposal_repo

    async def generate_proposal(
        self,
        target_workflow_id: str,
        artifact: DebateArtifact | None = None,
    ) -> OptimizationProposal:
        """Generate an optimization proposal for a workflow.

        Uses an LLM to analyze the artifact and propose concrete changes.

        Args:
            target_workflow_id: The workflow to optimize.
            artifact: Optional debate artifact to analyze.

        Returns:
            An ``OptimizationProposal`` with LLM-generated suggestions.

        Raises:
            ValueError: If the workflow does not exist or is locked.
        """
        # Validate workflow exists
        workflow = self._blueprint_repo.get_workflow_definition(target_workflow_id)
        if workflow is None:
            raise ValueError(f"Workflow {target_workflow_id!r} not found")

        if workflow.is_locked:
            raise ValueError(f"Workflow {target_workflow_id!r} is locked and cannot be reflected upon")

        # Build source session reference
        source_session_id = artifact.session_id if artifact else None

        # Try LLM-powered analysis
        llm_result = await self._analyze_with_llm(artifact, workflow)

        if llm_result:
            return self._build_proposal_from_llm(
                target_workflow_id=target_workflow_id,
                source_session_id=source_session_id,
                llm_result=llm_result,
            )

        # Fallback: rule-based analysis without LLM
        return self._build_rule_based_proposal(
            target_workflow_id=target_workflow_id,
            source_session_id=source_session_id,
            artifact=artifact,
        )

    async def _analyze_with_llm(self, artifact: DebateArtifact | None, workflow: Any) -> dict | None:
        """Call the LLM to analyze the artifact and propose changes.

        Returns parsed JSON dict, or None if LLM is unavailable.
        """
        try:
            from backend.services.llm_service import LLMService

            llm = LLMService()
            if not llm.profile:
                logger.debug("No LLM profile available for meta-workflow analysis")
                return None

            # Build the prompt
            prompt = self._build_analysis_prompt(artifact, workflow)

            result = await llm.generate(
                prompt=prompt,
                system_prompt=_SYSTEM_PROMPT,
                temperature=0.3,
                max_tokens=2048,
            )

            content = result.content.strip()
            if not content:
                return None

            # Strip markdown code fences if present
            if content.startswith("```"):
                lines = content.split("\n")
                lines = [l for l in lines if not l.strip().startswith("```")]
                content = "\n".join(lines)

            return json.loads(content)

        except json.JSONDecodeError:
            logger.warning("MetaWorkflow LLM returned invalid JSON, falling back to rule-based")
            return None
        except Exception:
            logger.debug("MetaWorkflow LLM analysis failed, falling back to rule-based", exc_info=True)
            return None

    def _build_analysis_prompt(self, artifact: DebateArtifact | None, workflow: Any) -> str:
        """Build the analysis prompt for the LLM."""
        parts = []

        # Workflow info
        parts.append(f"## Workflow: {workflow.name or workflow.id}")
        if hasattr(workflow, "nodes") and workflow.nodes:
            node_names = [n.id for n in workflow.nodes]
            parts.append(f"Nodes: {', '.join(node_names)}")

        # Artifact info
        if artifact:
            parts.append(f"\n## Artefakt für Session {artifact.session_id}")
            parts.append(f"Topic: {artifact.topic}")
            parts.append(f"Transcript-Länge: {len(artifact.transcript)} Turns")

            consensus = artifact.consensus_result
            if consensus:
                score = consensus.get("score", 0)
                parts.append(f"Konsens-Score: {score:.2f}")

            if artifact.metadata:
                duration = artifact.metadata.get("duration_ms", 0)
                parts.append(f"Dauer: {duration / 1000:.1f}s")

            # Include abbreviated transcript
            if artifact.transcript:
                parts.append("\n### Transcript-Auszug (letzte 3 Turns)")
                for turn in artifact.transcript[-3:]:
                    role = turn.agent_name or turn.role_type
                    content_preview = turn.content[:200] + ("..." if len(turn.content) > 200 else "")
                    parts.append(f"**{role}** (Runde {turn.round}): {content_preview}")
        else:
            parts.append("\nKein Artefakt verfügbar — analysiere nur die Workflow-Struktur.")

        return "\n".join(parts)

    def _build_proposal_from_llm(
        self,
        target_workflow_id: str,
        source_session_id: str | None,
        llm_result: dict,
    ) -> OptimizationProposal:
        """Build an OptimizationProposal from LLM JSON output."""
        proposed_nodes = []
        for node_data in llm_result.get("proposed_nodes", []):
            proposed_nodes.append({
                "node_id": node_data.get("node_id", ""),
                "action": node_data.get("action", "modify"),
                "config_changes": node_data.get("config_changes", {}),
            })

        proposed_edges = []
        for edge_data in llm_result.get("proposed_edges", []):
            proposed_edges.append({
                "from": edge_data.get("from", ""),
                "to": edge_data.get("to", ""),
                "action": edge_data.get("action", "add"),
            })

        proposal = OptimizationProposal(
            target_workflow_id=target_workflow_id,
            source_session_id=source_session_id,
            proposed_nodes=proposed_nodes,
            proposed_edges=proposed_edges,
            rationale=llm_result.get("rationale", "LLM-generierte Optimierungsvorschläge."),
            risk_assessment=llm_result.get("risk_assessment", "Gering"),
            estimated_impact=llm_result.get("estimated_impact", "Wird durch LLM-Analyse bestimmt."),
            status=ProposalStatus.PENDING,
            created_by=ProposalCreatedBy.META_AGENT,
            parent_version_id=target_workflow_id,
        )

        self._proposal_repo.save(proposal)
        logger.info("LLM-generated proposal %s for workflow %s", proposal.id, target_workflow_id)
        return proposal

    def _build_rule_based_proposal(
        self,
        target_workflow_id: str,
        source_session_id: str | None,
        artifact: DebateArtifact | None,
    ) -> OptimizationProposal:
        """Fallback: rule-based proposal when LLM is unavailable."""
        proposed_nodes = []
        rationale_parts = []

        if artifact:
            # Rule 1: Low consensus → suggest more rounds
            consensus = artifact.consensus_result
            score = consensus.get("score", 1.0) if consensus else 1.0
            if score < 0.6:
                rationale_parts.append(
                    f"Konsens-Score ist niedrig ({score:.2f}). "
                    "Erwägen Sie, mehr Runden hinzuzufügen."
                )

            # Rule 2: Too few transcript turns → suggest additional agents
            if len(artifact.transcript) < 4:
                rationale_parts.append(
                    f"Nur {len(artifact.transcript)} Transcript-Einträge. "
                    "Mehr Agenten könnten die Diskussion bereichern."
                )

            # Rule 3: Short content → suggest different model
            avg_content_len = (
                sum(len(t.content) for t in artifact.transcript) / len(artifact.transcript)
                if artifact.transcript
                else 0
            )
            if avg_content_len < 100:
                rationale_parts.append(
                    "Durchschnittliche Antwort-Länge ist kurz. "
                    "Ein Modell mit größerem Kontextfenster könnte helfen."
                )

        if not rationale_parts:
            rationale_parts.append(
                "Keine spezifischen Probleme erkannt. "
                "Das Workflow scheint stabil zu funktionieren."
            )

        proposal = OptimizationProposal(
            target_workflow_id=target_workflow_id,
            source_session_id=source_session_id,
            proposed_nodes=proposed_nodes,
            proposed_edges=[],
            rationale="\n".join(rationale_parts),
            risk_assessment="Gering — regelbasierte Analyse ohne LLM.",
            estimated_impact="Übersichtsbasiert — für detaillierte Analyse LLM konfigurieren.",
            status=ProposalStatus.PENDING,
            created_by=ProposalCreatedBy.META_AGENT,
            parent_version_id=target_workflow_id,
        )

        self._proposal_repo.save(proposal)
        logger.info("Rule-based proposal %s for workflow %s", proposal.id, target_workflow_id)
        return proposal
