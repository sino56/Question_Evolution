"""Derive deterministic budget signals from published experiment observations."""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Mapping

from .budget_state import BudgetLedger


def _count(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def build_budget_observation(observation: Mapping[str, Any], ledger: BudgetLedger) -> Dict[str, Any]:
    """Build the Stage-10 observation contract without changing pipeline state.

    The function intentionally accepts the Agent observer's compact aggregate;
    optional per-operator detail improves recommendations but is never invented.
    """

    status_counts = {str(key): _count(value) for key, value in dict(observation.get("status_counts") or {}).items()}
    by_operator: Dict[str, Counter[str]] = defaultdict(Counter)
    raw_operator_counts = observation.get("operator_status_counts")
    if isinstance(raw_operator_counts, Mapping):
        for operator, counts in raw_operator_counts.items():
            if isinstance(counts, Mapping):
                by_operator[str(operator)].update({str(key): _count(value) for key, value in counts.items()})
    for ref in observation.get("evidence_refs") or []:
        if not isinstance(ref, Mapping):
            continue
        operator = str(ref.get("operator_id") or "").strip()
        status = str(ref.get("status") or "").strip()
        if operator and status and not by_operator[operator][status]:
            # Evidence may be truncated; do not double count records already
            # present in the full per-operator aggregate.
            by_operator[operator][status] += 1

    variance = observation.get("scoring_variance_summary")
    variance = dict(variance) if isinstance(variance, Mapping) else {}
    eligible_scoring_targets = [str(item) for item in observation.get("eligible_scoring_targets") or [] if isinstance(item, str) and item]
    required_scoring_targets = [str(item) for item in observation.get("required_scoring_targets") or [] if isinstance(item, str) and item]
    for candidate in variance.get("candidates") or []:
        if isinstance(candidate, Mapping) and candidate.get("validated") and str(candidate.get("target") or ""):
            eligible_scoring_targets.append(str(candidate["target"]))
    return {
        "observation_version": "budget-observation-v1",
        "branch_status_counts": status_counts,
        "operator_plan_status": dict(observation.get("operator_plan_status") or {}),
        "operator_attempt_count": {operator: sum(counts.values()) for operator, counts in sorted(by_operator.items())},
        "operator_status_counts": {operator: dict(counts) for operator, counts in sorted(by_operator.items())},
        "validation_failed_count": _count(observation.get("validation_failed_count")) or status_counts.get("validation_failed", 0),
        "not_applicable_count": _count(observation.get("not_applicable_count")) or status_counts.get("not_applicable", 0),
        "score_increased_count": _count(observation.get("score_increased_count")) or status_counts.get("score_increased", 0),
        "score_decreased_count": status_counts.get("score_decreased", 0) + status_counts.get("boundary_candidate", 0),
        "scoring_variance_summary": variance,
        "eligible_scoring_targets": sorted(set(eligible_scoring_targets)),
        "required_scoring_targets": sorted(set(required_scoring_targets)),
        "remaining_budget": ledger.remaining_by_type(),
        "termination_reason": observation.get("termination_reason"),
        "evidence_refs": [dict(item) for item in observation.get("evidence_refs") or [] if isinstance(item, Mapping)],
        "manifest_status": observation.get("manifest_status"),
        "snapshot_consistent": observation.get("manifest_status") != "damaged",
    }
