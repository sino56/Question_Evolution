import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.budgeting import BudgetLedger, build_budget_observation, build_reallocation_proposal
from schema_validation import load_schema, validate_instance


def _ledger():
    ledger = BudgetLedger.create({"generation": 10, "scoring": 4, "repeat_scoring": 2})
    ledger.allocations["generation"] = {"operator:O16": 4, "operator:O18": 3, "pool:unallocated": 3}
    ledger.validate()
    return ledger


def test_reallocator_moves_low_yield_operator_budget_to_stable_score_drop_operator():
    ledger = _ledger()
    observation = build_budget_observation({
        "status_counts": {"validation_failed": 2, "score_decreased": 2},
        "operator_status_counts": {"O16": {"validation_failed": 2}, "O18": {"score_decreased": 2}},
        "evidence_refs": [
            {"operator_id": "O16", "branch_id": "b16", "status": "validation_failed"},
            {"operator_id": "O18", "branch_id": "b18", "status": "score_decreased"},
        ],
    }, ledger)
    proposal = build_reallocation_proposal(observation, ledger, trigger="round_completed")

    assert proposal["analysis_status"] == "proposed"
    assert {item["target"] for item in proposal["changes"]} == {"operator:O16", "operator:O18"}
    assert next(item for item in proposal["changes"] if item["target"] == "operator:O18")["to"] == 7
    schema_path = ROOT / "schemas" / "budget_reallocation_proposal.schema.json"
    validate_instance(proposal, load_schema(schema_path), schema_dir=schema_path.parent)


def test_score_increased_never_receives_new_budget():
    ledger = _ledger()
    observation = build_budget_observation({
        "operator_status_counts": {"O16": {"score_increased": 1}, "O18": {"score_increased": 1}},
        "evidence_refs": [{"operator_id": "O16", "status": "score_increased"}],
    }, ledger)
    proposal = build_reallocation_proposal(observation, ledger)

    assert all(not (change["action"] == "increase" and change["target"] == "operator:O18") for change in proposal["changes"])


def test_validated_high_variance_candidate_receives_repeat_scoring_budget():
    ledger = _ledger()
    observation = build_budget_observation({
        "scoring_variance_summary": {"candidates": [{"target": "candidate:c1", "validated": True, "score_range": 0.22, "evidence_refs": [{"candidate_id": "c1"}]}]},
    }, ledger)
    proposal = build_reallocation_proposal(observation, ledger)

    scoring = [change for change in proposal["changes"] if change["budget_type"] == "repeat_scoring"]
    assert {(change["target"], change["action"]) for change in scoring} == {("pool:unallocated", "reduce"), ("candidate:c1", "increase")}
