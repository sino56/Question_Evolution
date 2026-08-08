"""Candidate-plan helpers; output is always submitted to the normal validator."""

from __future__ import annotations

from typing import Mapping

from ..policy import PolicyViolation, validate_plan
from ..task import AgentTask


def planning_advice(advisor_id: str, context: Mapping) -> dict:
    refs = list(context["evidence_pack_slice"].get("evidence_refs") or [])[:3]
    return {"summary": f"{advisor_id} produced a non-executable candidate plan suggestion.", "findings": [{"type": "plan_candidate", "severity": "medium", "claim": "Candidate plans require AgentPlan validation before any executor use.", "evidence_refs": refs, "recommended_action": "needs_human_review"}], "forbidden_actions_requested": []}


def validate_candidate_plan(task: AgentTask, candidate: Mapping) -> tuple[bool, str]:
    """The only admission path for an advisor-produced plan candidate."""

    try:
        validate_plan(task, candidate)
    except (PolicyViolation, TypeError, KeyError) as exc:
        return False, str(exc)
    return True, "validated"
