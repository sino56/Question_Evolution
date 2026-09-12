import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.budgeting import BudgetLedger, build_budget_observation, build_reallocation_proposal
from agent_runtime.task import parse_agent_task
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


# --------------------------------------------------------------------------- operator allocation bootstrap


def test_fresh_session_bootstraps_operator_allocations_so_reallocation_can_fire():
    from agent_runtime.budgeting.runtime import bootstrap_operator_allocations, registered_operator_ids

    ledger = bootstrap_operator_allocations(BudgetLedger.create({"generation": 10, "scoring": 4, "repeat_scoring": 2}))
    operators = registered_operator_ids()
    assert operators
    for operator in operators:
        assert ledger.remaining_for("generation", f"operator:{operator}") > 0
    assert ledger.remaining_for("generation", "pool:unallocated") > 0

    # The bootstrap is exactly what makes the reducer reachable on a fresh ledger.
    observation = build_budget_observation({
        "operator_status_counts": {operators[0]: {"validation_failed": 2}},
        "evidence_refs": [{"operator_id": operators[0], "status": "validation_failed"}],
    }, ledger)
    proposal = build_reallocation_proposal(observation, ledger)
    reductions = [item for item in proposal["changes"] if item["action"] == "reduce" and item["target"] == f"operator:{operators[0]}"]
    assert reductions and reductions[0]["to"] == 0.0

    # A second bootstrap on the same ledger must not re-split the pool.
    pooled_before = ledger.remaining_for("generation", "pool:unallocated")
    bootstrap_operator_allocations(ledger)
    assert ledger.remaining_for("generation", "pool:unallocated") == pooled_before


def test_load_or_create_ledger_bootstraps_only_a_fresh_session(tmp_path, monkeypatch):
    from agent_runtime.budgeting.runtime import load_or_create_ledger, registered_operator_ids, save_ledger

    monkeypatch.chdir(ROOT)
    task = parse_agent_task(
        {"goal": "find boundaries", "input_file": "data/data.jsonl", "budget_limits": {"generation": 10}},
        project_root=ROOT,
    )
    ledger = load_or_create_ledger(tmp_path / "run", task=task, state={})
    first_operator = registered_operator_ids()[0]
    assert ledger.remaining_for("generation", f"operator:{first_operator}") > 0

    # A persisted ledger is loaded as-is; the bootstrap never runs twice.
    save_ledger(tmp_path / "run", ledger)
    reloaded = load_or_create_ledger(tmp_path / "run", task=task, state={})
    assert reloaded.remaining_for("generation", f"operator:{first_operator}") == ledger.remaining_for("generation", f"operator:{first_operator}")
