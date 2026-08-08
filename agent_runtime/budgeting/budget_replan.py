"""Create a future-only plan revision from an approved budget decision."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Sequence

from .budget_state import BudgetLedger


class BudgetReplanError(ValueError):
    pass


def build_budget_replan(
    plan: Mapping[str, Any],
    *,
    proposal: Mapping[str, Any],
    decision: Mapping[str, Any],
    ledger: BudgetLedger,
    completed_step_ids: Sequence[str],
) -> Dict[str, Any]:
    """Return a new immutable-plan payload; callers persist it via state.py.

    Completed plan steps are retained byte-for-byte (aside from normal JSON
    copying).  The reallocation appears only as a future execution constraint,
    never as a rewrite of pipeline search state or a previous plan revision.
    """

    if decision.get("status") != "approved":
        raise BudgetReplanError("only an approved budget decision can produce a replan")
    previous = deepcopy(dict(plan))
    completed = {str(item) for item in completed_step_ids}
    steps = previous.get("steps")
    if not isinstance(steps, list):
        raise BudgetReplanError("plan.steps must be a list")
    known = {str(step.get("step_id")) for step in steps if isinstance(step, Mapping)}
    if not completed.issubset(known):
        raise BudgetReplanError("completed step is not in the plan")
    revised = deepcopy(previous)
    revised["budget_reallocation"] = {
        "proposal_id": proposal.get("proposal_id"),
        "decision_id": decision.get("decision_id"),
        "approved_changes": deepcopy(decision.get("approved_changes") or []),
        "remaining_allocations": deepcopy(ledger.allocations),
        "applies_to_step_ids": [str(step.get("step_id")) for step in steps if isinstance(step, Mapping) and str(step.get("step_id")) not in completed],
        "execution_rule": "future_unexecuted_steps_only",
    }
    revised.setdefault("assumptions", []).append("budget reallocation is an approved remaining-only control-plane constraint")
    return revised
