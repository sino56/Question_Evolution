import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.budgeting import BudgetLedger, validate_reallocation
from schema_validation import load_schema, validate_instance


def _ledger():
    ledger = BudgetLedger.create({"generation": 10, "scoring": 3, "vertical_depth": 2})
    ledger.allocations["generation"] = {"operator:O16": 4, "operator:O18": 3, "pool:unallocated": 3}
    ledger.allocations["scoring"] = {"candidate:c1": 1, "pool:unallocated": 2}
    ledger.allocations["vertical_depth"] = {"operator:O18": 1, "pool:unallocated": 1}
    ledger.validate()
    return ledger


def _proposal(changes, **extra):
    value = {
        "proposal_id": "proposal-1", "current_budget": _ledger().remaining_by_type(), "changes": changes,
        "forbidden_actions_requested": [],
    }
    value.update(extra)
    return value


def _observation(**extra):
    value = {
        "manifest_status": "not_checked", "snapshot_consistent": True,
        "operator_status_counts": {"O16": {"validation_failed": 2}, "O18": {"score_decreased": 2}},
        "eligible_scoring_targets": ["candidate:c1"],
    }
    value.update(extra)
    return value


def test_validator_approves_evidence_bound_conserving_transfer():
    ledger = _ledger()
    changes = [
        {"target": "operator:O16", "action": "reduce", "budget_type": "generation", "from": 4, "to": 0, "reason": "failed", "evidence_refs": [{"branch_id": "b16"}]},
        {"target": "operator:O18", "action": "increase", "budget_type": "generation", "from": 3, "to": 7, "reason": "drop", "evidence_refs": [{"branch_id": "b18"}]},
    ]
    decision = validate_reallocation(_proposal(changes), ledger, _observation())
    assert decision["status"] == "approved"
    schema_path = ROOT / "schemas" / "budget_reallocation_decision.schema.json"
    validate_instance(decision, load_schema(schema_path), schema_dir=schema_path.parent)


@pytest.mark.parametrize("change,reason", [
    ({"target": "operator:O18", "action": "increase", "budget_type": "generation", "from": 3, "to": 4, "reason": "again", "evidence_refs": [{"id": "x"}]}, "score_increased"),
    ({"target": "candidate:unvalidated", "action": "increase", "budget_type": "scoring", "from": 0, "to": 1, "reason": "score", "evidence_refs": [{"id": "x"}]}, "unvalidated"),
    ({"target": "operator:O18", "action": "increase", "budget_type": "vertical_depth", "from": 1, "to": 2, "reason": "deeper", "evidence_refs": [{"id": "x"}]}, "vertical_parent_not_answerable"),
])
def test_validator_rejects_prohibited_continuation_requests(change, reason):
    ledger = _ledger()
    observation = _observation(operator_status_counts={"O18": {"score_increased": 1}}, vertical_parent_answerable=False, vertical_first_layer_negative_gain=True)
    decision = validate_reallocation(_proposal([change]), ledger, observation)
    assert decision["status"] == "rejected_by_budget_validator"
    assert any(reason in item for item in decision["rejection_reasons"])


def test_validator_rejects_consumed_budget_mutation_and_hard_budget_overflow():
    ledger = _ledger()
    overflow = _proposal([
        {"target": "operator:O18", "action": "increase", "budget_type": "generation", "from": 3, "to": 8, "reason": "drop", "evidence_refs": [{"id": "x"}]},
    ], consumed={"generation": {"operator:O18": 0}})
    decision = validate_reallocation(overflow, ledger, _observation())
    assert decision["status"] == "rejected_by_budget_validator"
    assert any("historical" in item or "conserve" in item for item in decision["rejection_reasons"])


def test_missing_evidence_requires_human_review_not_automatic_execution():
    ledger = _ledger()
    changes = [
        {"target": "operator:O16", "action": "reduce", "budget_type": "generation", "from": 4, "to": 0, "reason": "failed", "evidence_refs": []},
        {"target": "operator:O18", "action": "increase", "budget_type": "generation", "from": 3, "to": 7, "reason": "drop", "evidence_refs": [{"id": "b18"}]},
    ]
    assert validate_reallocation(_proposal(changes), ledger, _observation())["status"] == "needs_human_review"


def test_direct_search_state_mutation_is_rejected():
    ledger = _ledger()
    decision = validate_reallocation(_proposal([], forbidden_actions_requested=["modify search_state directly"]), ledger, _observation())
    assert decision["status"] == "rejected_by_budget_validator"


def test_validator_cannot_take_required_scoring_from_a_valid_candidate_or_edit_frozen_plan():
    ledger = _ledger()
    scoring = _proposal([
        {"target": "candidate:c1", "action": "reduce", "budget_type": "scoring", "from": 1, "to": 0, "reason": "move", "evidence_refs": [{"id": "c1"}]},
        {"target": "pool:unallocated", "action": "increase", "budget_type": "scoring", "from": 2, "to": 3, "reason": "move", "evidence_refs": [{"id": "c1"}]},
    ], operator_plan_revision=99)
    decision = validate_reallocation(scoring, ledger, _observation(required_scoring_targets=["candidate:c1"]))
    assert decision["status"] == "rejected_by_budget_validator"
    assert any("required_scoring" in item for item in decision["rejection_reasons"])
    assert any("frozen_pipeline" in item for item in decision["rejection_reasons"])
