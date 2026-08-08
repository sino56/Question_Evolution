"""Deterministic, conservative BudgetReallocationProposal generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, Mapping

from .budget_state import BudgetLedger, UNALLOCATED_TARGET


LOW_YIELD_STATUSES = ("validation_failed", "not_applicable", "score_increased")


def _refs_for(target: str, evidence_refs: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
    refs = [dict(ref) for ref in evidence_refs if str(ref.get("operator_id") or "") == target.removeprefix("operator:")]
    return refs[:10]


def _proposal_id(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "budget_reallocation_" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def build_reallocation_proposal(
    budget_observation: Mapping[str, Any],
    ledger: BudgetLedger,
    *,
    trigger: str = "stage_boundary_observed",
) -> Dict[str, Any]:
    """Produce a transfer-only proposal from deterministic published evidence.

    It intentionally does not claim high yield from an unscored candidate and
    does not turn a score increase into an exploration recommendation.
    """

    operator_counts = budget_observation.get("operator_status_counts")
    operator_counts = operator_counts if isinstance(operator_counts, Mapping) else {}
    refs = [dict(ref) for ref in budget_observation.get("evidence_refs") or [] if isinstance(ref, Mapping)]
    reductions: list[Dict[str, Any]] = []
    for operator, raw_counts in sorted(operator_counts.items()):
        if not isinstance(raw_counts, Mapping):
            continue
        target = f"operator:{operator}"
        current = ledger.remaining_for("generation", target)
        if current <= 0:
            continue
        counts = {str(key): int(value or 0) for key, value in raw_counts.items()}
        matched = next((status for status in LOW_YIELD_STATUSES if counts.get(status, 0) >= (1 if status == "score_increased" else 2)), None)
        if not matched:
            continue
        reason = {
            "validation_failed": "repeated validation_failed results",
            "not_applicable": "repeated not_applicable results",
            "score_increased": "score_increased is negative gain under the same conditions",
        }[matched]
        reductions.append({
            "target": target, "action": "reduce", "budget_type": "generation", "from": current, "to": 0.0,
            "reason": reason, "evidence_refs": _refs_for(target, refs),
        })

    receivers = []
    for operator, raw_counts in sorted(operator_counts.items()):
        if not isinstance(raw_counts, Mapping):
            continue
        counts = {str(key): int(value or 0) for key, value in raw_counts.items()}
        if counts.get("score_decreased", 0) <= 0 or counts.get("score_increased", 0) > 0:
            continue
        receivers.append((counts.get("score_decreased", 0), f"operator:{operator}"))
    receivers.sort(key=lambda item: (-item[0], item[1]))
    changes = list(reductions)
    recovered = sum(float(item["from"]) for item in reductions)
    if recovered and receivers:
        target = receivers[0][1]
        current = ledger.remaining_for("generation", target)
        changes.append({
            "target": target, "action": "increase", "budget_type": "generation", "from": current, "to": current + recovered,
            "reason": "stable score_decreased evidence on validated path", "evidence_refs": _refs_for(target, refs),
        })
    elif recovered:
        current = ledger.remaining_for("generation", UNALLOCATED_TARGET)
        changes.append({
            "target": UNALLOCATED_TARGET, "action": "increase", "budget_type": "generation", "from": current, "to": current + recovered,
            "reason": "return low-yield allocation to unassigned remaining pool", "evidence_refs": refs[:10],
        })

    # Repeated scoring is a separate remaining budget.  It is only proposed
    # for an already validated candidate whose observed range is high enough
    # to make the score direction unreliable.
    variance = budget_observation.get("scoring_variance_summary")
    candidates = variance.get("candidates") if isinstance(variance, Mapping) else []
    if "repeat_scoring" in ledger.hard_limits and isinstance(candidates, list):
        pool = ledger.remaining_for("repeat_scoring", UNALLOCATED_TARGET)
        for candidate in candidates:
            if not isinstance(candidate, Mapping) or not candidate.get("validated") or pool <= 0:
                continue
            score_range = candidate.get("score_range")
            try:
                unstable = float(score_range) >= 0.15
            except (TypeError, ValueError):
                unstable = False
            target = str(candidate.get("target") or "")
            if not unstable or not target:
                continue
            current = ledger.remaining_for("repeat_scoring", target)
            transfer = min(1.0, pool)
            evidence = [dict(item) for item in candidate.get("evidence_refs") or [] if isinstance(item, Mapping)] or refs[:1]
            changes.extend([
                {"target": UNALLOCATED_TARGET, "action": "reduce", "budget_type": "repeat_scoring", "from": pool, "to": pool - transfer,
                 "reason": "reserve repeat scoring for a validated unstable candidate", "evidence_refs": evidence},
                {"target": target, "action": "increase", "budget_type": "repeat_scoring", "from": current, "to": current + transfer,
                 "reason": "scoring variance requires one additional repeat evaluation", "evidence_refs": evidence},
            ])
            break

    base = {
        "trigger": trigger,
        "summary": "Reallocate only remaining generation budget using published branch evidence.",
        "current_budget": ledger.remaining_by_type(),
        "changes": changes,
        "requires_validator": True,
        "forbidden_actions_requested": [],
        "analysis_status": "proposed" if changes else "no_change",
    }
    base["proposal_id"] = _proposal_id(base)
    return base
