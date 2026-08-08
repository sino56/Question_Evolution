import json
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.budgeting import BudgetLedger, build_budget_replan, write_budget_artifacts
from agent_runtime.state import initialize_state, write_plan_revision


def test_approved_budget_replan_creates_new_plan_and_preserves_completed_steps(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    state = initialize_state(run_dir, run_id="budget-run", mode="run")
    plan = {"plan_id": "old", "steps": [{"step_id": "done", "tool": "check_environment"}, {"step_id": "future", "tool": "run_full_loop"}], "assumptions": []}
    original = deepcopy(plan)
    first = write_plan_revision(run_dir, state, plan)
    ledger = BudgetLedger.create({"generation": 4})
    ledger.allocations["generation"] = {"operator:O16": 2, "operator:O18": 2}
    proposal = {"proposal_id": "p1", "changes": [
        {"target": "operator:O16", "action": "reduce", "budget_type": "generation", "from": 2, "to": 0, "reason": "failed", "evidence_refs": [{"branch_id": "b1"}]},
        {"target": "operator:O18", "action": "increase", "budget_type": "generation", "from": 2, "to": 4, "reason": "gain", "evidence_refs": [{"branch_id": "b2"}]},
    ]}
    decision = {"decision_id": "d1", "status": "approved", "approved_changes": proposal["changes"]}
    ledger.apply_changes(decision["approved_changes"], proposal_id="p1")
    replan = build_budget_replan(first, proposal=proposal, decision=decision, ledger=ledger, completed_step_ids=["done"])
    second = write_plan_revision(run_dir, state, replan, trigger_reason="approved_budget_reallocation:p1")

    assert plan == original
    assert first["steps"][0] == second["steps"][0]
    assert second["plan_revision"] == 2
    assert second["budget_reallocation"]["applies_to_step_ids"] == ["future"]
    assert json.loads((run_dir / "plans" / "plan_r001.json").read_text(encoding="utf-8"))["plan_id"] == "old"


def test_budget_report_persists_before_after_decision_and_evidence(tmp_path):
    ledger = BudgetLedger.create({"generation": 1})
    proposal = {"proposal_id": "p2", "analysis_status": "no_change", "trigger": "round_completed", "summary": "none", "changes": []}
    decision = {"decision_id": "d2", "status": "no_change", "approved_changes": [], "rejection_reasons": [], "review_reasons": []}
    paths = write_budget_artifacts(tmp_path, ledger=ledger, proposal=proposal, decision=decision)

    assert all(path.exists() for path in paths.values())
    report = paths["report"].read_text(encoding="utf-8")
    assert "Dynamic budget reallocation" in report and "Before" in report and "Evidence refs" in report
