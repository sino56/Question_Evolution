from copy import deepcopy
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from search_coordinator import (
    claim_branches,
    initialize_search_state,
    make_branch_id,
    merge_decision_result,
    recover_in_flight_branches,
)
from question_evolution import should_evolve


def sample(sample_id, operators):
    return {
        "sample_id": sample_id,
        "prompt": f"prompt-{sample_id}",
        "evolution_action": "evolve_high_score_overscore",
        "operator_route": {
            "primary_operator": operators[0] if operators else None,
            "backup_operators": operators[1:],
            "avoid_operators": [],
        },
    }


def test_completed_sample_is_preserved_while_another_sample_keeps_running():
    completed_record = sample(
        "completed",
        ["O10_evidence_sufficiency_ladder"],
    )
    completed_state = initialize_search_state(completed_record)
    completed_state["status"] = "completed"
    completed_state["termination_reason"] = "candidate_list_exhausted"
    completed_state["coverage_status"] = "complete"
    completed_state["operator_plan"][0]["status"] = "completed"
    completed_record["search_state"] = deepcopy(completed_state)

    resumed = initialize_search_state(completed_record, branch_window=3)
    after_claim, claimed = claim_branches(resumed)

    assert claimed == []
    assert after_claim["termination_reason"] == "candidate_list_exhausted"
    assert after_claim["status"] == "completed"
    assert after_claim["operator_plan"][0]["status"] == "completed"

    running_record = sample(
        "running",
        [
            "O10_evidence_sufficiency_ladder",
            "O11_unobserved_state_attribution",
        ],
    )
    running_state = initialize_search_state(running_record, branch_window=1)
    running_after_claim, running_claimed = claim_branches(running_state)
    assert len(running_claimed) == 1
    assert running_after_claim["status"] == "running"


def test_recovery_does_not_regenerate_a_confirmed_candidate():
    record = sample(
        "recover",
        ["O10_evidence_sufficiency_ladder"],
    )
    state, claimed = claim_branches(initialize_search_state(record))
    branch_id = claimed[0]["branch_id"]

    recovered = recover_in_flight_branches(state, {branch_id: "candidate_generated"})
    assert recovered["operator_plan"][0]["status"] == "running"
    assert recovered["operator_plan"][0]["branch_stage"] == "candidate_generated"
    assert recovered["operator_plan"][0]["resume_from_stage"] == "candidate_generated"

    recovered_again = recover_in_flight_branches(
        recovered,
        {branch_id: "candidate_generated"},
    )
    assert recovered_again["operator_plan"][0]["status"] == "running"
    assert claim_branches(recovered_again)[1] == []


def test_unconfirmed_claim_returns_to_pending_and_keeps_stable_branch_id():
    record = sample(
        "pending-recovery",
        ["O12_conjunctive_necessity"],
    )
    state, claimed = claim_branches(initialize_search_state(record))
    expected = make_branch_id("pending-recovery::root", "O12_conjunctive_necessity")
    assert claimed[0]["branch_id"] == expected

    recovered = recover_in_flight_branches(state, {})
    assert recovered["operator_plan"][0]["status"] == "pending"
    reclaimed, second_claim = claim_branches(recovered)
    assert second_claim[0]["branch_id"] == expected
    assert reclaimed["operator_plan"][0]["generation_attempt_count"] == 0


def test_question_evolution_skips_completed_and_aborted_search_samples():
    completed = sample(
        "completed-entry",
        ["O10_evidence_sufficiency_ladder"],
    )
    completed["search_state"] = {
        "status": "completed",
        "termination_reason": "candidate_list_exhausted",
    }
    aborted = sample(
        "aborted-entry",
        ["O10_evidence_sufficiency_ladder"],
    )
    aborted["search_state"] = {
        "status": "aborted",
        "termination_reason": "aborted",
    }

    assert should_evolve(completed, 0.8) is False
    assert should_evolve(aborted, 0.8) is False


def test_merge_entry_preserves_partial_terminal_state():
    record = sample("partial", ["O10_evidence_sufficiency_ladder"])
    state, claimed = claim_branches(initialize_search_state(record))
    state["status"] = "partial"
    state["termination_reason"] = "partial_coverage"

    merged = merge_decision_result(
        state,
        {
            "branch_id": claimed[0]["branch_id"],
            "parent_score_rate": 1.0,
            "score_rate": 0.5,
        },
    )

    assert merged["status"] == "partial"
    assert merged["termination_reason"] == "partial_coverage"
    assert merged["decision_completed_count"] == 0
