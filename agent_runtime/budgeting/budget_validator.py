"""Hard safety checks for remaining-budget reallocation proposals."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Sequence

from .budget_state import BUDGET_TYPES, BudgetLedger


FORBIDDEN_STATE_MARKERS = ("search_state", "operator_plan", "vertical_search_state", "memory", "score_rate")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _decision_id(proposal_id: str, status: str) -> str:
    digest = hashlib.sha256(f"{proposal_id}:{status}".encode("utf-8")).hexdigest()[:16]
    return "budget_decision_" + digest


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 else None


def _operator_from_target(target: str) -> str:
    return target.removeprefix("operator:")


class BudgetValidator:
    """Validate transfer proposals without granting access to formal state fields."""

    def __init__(self, ledger: BudgetLedger, budget_observation: Mapping[str, Any]) -> None:
        self.ledger = ledger
        self.observation = budget_observation

    def validate(self, proposal: Mapping[str, Any]) -> Dict[str, Any]:
        proposal_id = str(proposal.get("proposal_id") or "unknown_proposal")
        hard_reasons: list[str] = []
        review_reasons: list[str] = []
        changes = proposal.get("changes")
        if not isinstance(changes, list):
            hard_reasons.append("changes_must_be_an_array")
            changes = []
        if any(marker in str(action).lower() for action in proposal.get("forbidden_actions_requested") or [] for marker in FORBIDDEN_STATE_MARKERS):
            hard_reasons.append("proposal_requests_direct_protected_state_mutation")
        if "consumed" in proposal or "hard_limits" in proposal:
            hard_reasons.append("proposal_may_not_modify_historical_consumption_or_hard_limits")
        if any(marker in str(key).lower() for key in proposal for marker in FORBIDDEN_STATE_MARKERS):
            hard_reasons.append("proposal_may_not_modify_frozen_pipeline_state_or_plan_revision")
        if dict(proposal.get("current_budget") or {}) != self.ledger.remaining_by_type():
            hard_reasons.append("proposal_current_budget_does_not_match_ledger")
        after = {kind: dict(targets) for kind, targets in self.ledger.allocations.items()}
        operator_counts = self.observation.get("operator_status_counts")
        operator_counts = operator_counts if isinstance(operator_counts, Mapping) else {}
        eligible_scoring = set(str(item) for item in self.observation.get("eligible_scoring_targets") or [])
        required_scoring = set(str(item) for item in self.observation.get("required_scoring_targets") or [])
        for index, change in enumerate(changes):
            if not isinstance(change, Mapping):
                hard_reasons.append(f"change_{index}_must_be_an_object")
                continue
            target = str(change.get("target") or "")
            kind = str(change.get("budget_type") or "")
            action = str(change.get("action") or "")
            before, desired = _number(change.get("from")), _number(change.get("to"))
            evidence = change.get("evidence_refs")
            if not target or kind not in BUDGET_TYPES or kind not in self.ledger.hard_limits:
                hard_reasons.append(f"change_{index}_has_unconfigured_target_or_budget_type")
                continue
            if any(marker in target.lower() for marker in FORBIDDEN_STATE_MARKERS):
                hard_reasons.append(f"change_{index}_targets_protected_state")
            if action not in {"increase", "reduce"} or before is None or desired is None:
                hard_reasons.append(f"change_{index}_has_invalid_action_or_amount")
                continue
            if abs(self.ledger.remaining_for(kind, target) - before) > 1e-9:
                hard_reasons.append(f"change_{index}_does_not_match_current_remaining_allocation")
            if action == "increase" and desired <= before:
                hard_reasons.append(f"change_{index}_increase_must_raise_allocation")
            if action == "reduce" and desired >= before:
                hard_reasons.append(f"change_{index}_reduce_must_lower_allocation")
            if not isinstance(evidence, list) or not evidence:
                review_reasons.append(f"change_{index}_has_no_evidence_refs")
            operator = _operator_from_target(target)
            statuses = operator_counts.get(operator)
            statuses = statuses if isinstance(statuses, Mapping) else {}
            if action == "increase" and int(statuses.get("score_increased", 0) or 0) > 0:
                hard_reasons.append(f"change_{index}_continues_score_increased_path")
            if action == "increase" and kind in {"scoring", "repeat_scoring"} and target not in eligible_scoring:
                hard_reasons.append(f"change_{index}_adds_scoring_to_unvalidated_candidate")
            if action == "reduce" and kind == "scoring" and target in required_scoring:
                hard_reasons.append(f"change_{index}_removes_required_scoring_budget")
            if action == "increase" and kind == "vertical_depth":
                if not bool(self.observation.get("vertical_parent_answerable")):
                    hard_reasons.append(f"change_{index}_vertical_parent_not_answerable")
                if bool(self.observation.get("vertical_first_layer_negative_gain")):
                    hard_reasons.append(f"change_{index}_vertical_first_layer_has_negative_gain")
            after[kind][target] = desired

        for kind, targets in after.items():
            if abs(sum(targets.values()) - self.ledger.remaining_by_type()[kind]) > 1e-9:
                hard_reasons.append(f"reallocation_does_not_conserve_remaining_{kind}")
        allocated_operators = [target for targets in self.ledger.allocations.values() for target in targets if target.startswith("operator:")]
        remaining_operators = [target for targets in after.values() for target, value in targets.items() if target.startswith("operator:") and value > 0]
        if allocated_operators and not remaining_operators:
            review_reasons.append("adjustment_would_eliminate_all_operator_paths")
        if self.observation.get("manifest_status") == "damaged" or self.observation.get("snapshot_consistent") is False:
            hard_reasons.append("published_artifacts_or_snapshots_are_not_consistent")

        if hard_reasons:
            status = "rejected_by_budget_validator"
        elif review_reasons:
            status = "needs_human_review"
        elif not changes:
            status = "no_change"
        else:
            status = "approved"
        return {
            "decision_id": _decision_id(proposal_id, status),
            "proposal_id": proposal_id,
            "status": status,
            "rejection_reasons": hard_reasons,
            "review_reasons": review_reasons,
            "approved_changes": [dict(change) for change in changes] if status == "approved" else [],
            "budget_before": self.ledger.remaining_by_type(),
            "budget_after": {kind: sum(targets.values()) for kind, targets in after.items()} if status == "approved" else self.ledger.remaining_by_type(),
            "created_at": _now(),
        }


def validate_reallocation(proposal: Mapping[str, Any], ledger: BudgetLedger, budget_observation: Mapping[str, Any]) -> Dict[str, Any]:
    return BudgetValidator(ledger, budget_observation).validate(proposal)
