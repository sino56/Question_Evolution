"""Runtime glue for budget sidecars; it never writes pipeline search state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from ..events import append_event
from .budget_observer import build_budget_observation
from .budget_reallocator import build_reallocation_proposal
from .budget_report import write_budget_artifacts
from .budget_state import BudgetLedger
from .budget_validator import validate_reallocation


def hard_limits_from_task(task: Any) -> Dict[str, float]:
    configured = dict(getattr(task, "budget_limits", {}) or {})
    # Existing task fields remain the legacy source for these two controls.
    configured.setdefault("search_steps", getattr(task, "max_search_steps", 0))
    configured.setdefault("candidate", getattr(task, "boundary_target", 0))
    return {str(kind): float(amount) for kind, amount in configured.items()}


def load_or_create_ledger(run_dir: str | Path, *, task: Any, state: Mapping[str, Any]) -> BudgetLedger:
    root = Path(run_dir)
    path = Path(str(state.get("budget_ledger_path") or (root / "budget_ledger.json")))
    if path.is_file():
        return BudgetLedger.from_dict(json.loads(path.read_text(encoding="utf-8")))
    return BudgetLedger.create(hard_limits_from_task(task))


def save_ledger(run_dir: str | Path, ledger: BudgetLedger) -> Path:
    path = Path(run_dir) / "budget_ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(ledger.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def assess_budget_reallocation(
    run_dir: str | Path,
    *,
    task: Any,
    state: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> tuple[BudgetLedger, Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    """Write proposal/decision audit records, but never apply them here."""

    ledger = load_or_create_ledger(run_dir, task=task, state=state)
    budget_observation = build_budget_observation(observation, ledger)
    proposal = build_reallocation_proposal(budget_observation, ledger)
    decision = validate_reallocation(proposal, ledger, budget_observation)
    write_budget_artifacts(run_dir, ledger=ledger, proposal=proposal, decision=decision)
    append_event(Path(run_dir) / "agent_events.jsonl", "budget_reallocation_assessed", {
        "proposal_id": proposal["proposal_id"], "decision_id": decision["decision_id"], "status": decision["status"],
        "change_count": len(proposal.get("changes") or []),
    })
    return ledger, budget_observation, proposal, decision
