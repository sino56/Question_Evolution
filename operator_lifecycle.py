"""Evidence-gated lifecycle decisions for qualification-only operators."""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, Mapping, Optional

from operator_contracts import (
    ENABLED,
    QUALIFICATION_ONLY,
    RETIRED,
    SHADOW_ROUTING,
    SUSPENDED,
    get_operator_contract,
)
from operator_qualification import CONFIRMED


ALLOWED_TRANSITIONS = {
    QUALIFICATION_ONLY: {SHADOW_ROUTING, SUSPENDED, RETIRED},
    SHADOW_ROUTING: {ENABLED, QUALIFICATION_ONLY, SUSPENDED, RETIRED},
    ENABLED: {SHADOW_ROUTING, QUALIFICATION_ONLY, SUSPENDED, RETIRED},
    SUSPENDED: {QUALIFICATION_ONLY, SHADOW_ROUTING, RETIRED},
    RETIRED: set(),
}


def evaluate_lifecycle_transition(
    operator_id: str,
    *,
    current_status: str,
    target_status: str,
    forced_report: Optional[Mapping[str, Any]] = None,
    natural_report: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Evaluate, but never apply, an operator status transition."""

    contract = get_operator_contract(operator_id)
    reasons = []
    if target_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
        reasons.append(
            f"transition {current_status} -> {target_status} is not allowed"
        )
    if target_status in {SHADOW_ROUTING, ENABLED}:
        if not isinstance(forced_report, Mapping):
            reasons.append("forced qualification report is required")
        else:
            if forced_report.get("operator_id") != operator_id:
                reasons.append("forced report operator_id mismatch")
            if forced_report.get("semantic_version") != contract.semantic_version:
                reasons.append("forced report semantic_version mismatch")
            if forced_report.get("qualification_decision") != CONFIRMED:
                reasons.append("forced qualification is not confirmed")
    if target_status == ENABLED:
        if not isinstance(natural_report, Mapping):
            reasons.append("natural routing report is required")
        else:
            per_operator = natural_report.get("per_operator")
            per_operator = (
                per_operator if isinstance(per_operator, Mapping) else {}
            )
            counts = per_operator.get(operator_id)
            if not isinstance(counts, Mapping):
                reasons.append("natural routing report has no operator evidence")
            elif int(counts.get("correct_route", 0) or 0) <= 0:
                reasons.append("natural routing report has no correct route")
    return {
        "operator_id": operator_id,
        "semantic_version": contract.semantic_version,
        "prompt_version": contract.prompt_version,
        "applicability_version": contract.applicability_version,
        "validation_policy_version": contract.validation_policy_version,
        "current_status": current_status,
        "target_status": target_status,
        "allowed": not reasons,
        "blocking_reasons": reasons,
        "applied": False,
    }

def build_rollback_record(
    operator_id: str,
    *,
    from_status: str,
    to_status: str,
    failure_reason: str,
    rollback_date: Optional[str] = None,
) -> Dict[str, Any]:
    if to_status not in {SHADOW_ROUTING, QUALIFICATION_ONLY, SUSPENDED}:
        raise ValueError("rollback target must stop or reduce production traffic")
    decision = evaluate_lifecycle_transition(
        operator_id,
        current_status=from_status,
        target_status=to_status,
    )
    if not decision["allowed"]:
        raise ValueError("; ".join(decision["blocking_reasons"]))
    return {
        **decision,
        "failure_reason": str(failure_reason).strip(),
        "rollback_date": rollback_date or date.today().isoformat(),
        "history_preserved": True,
    }
