import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from search_coordinator import (
    build_dispatch_records,
    claim_branches,
    initialize_search_state,
    mark_branch_terminal,
    merge_decision_result,
    register_generated_prompt,
)
from candidate_selection import select_candidates


OPS = [
    "O10_evidence_sufficiency_ladder",
    "O11_unobserved_state_attribution",
    "O12_conjunctive_necessity",
    "O13_minimal_disqualifier",
]


def sample(operators=None):
    operators = OPS if operators is None else operators
    return {
        "sample_id": "sample-window",
        "prompt": "parent prompt",
        "score_rate": 1.0,
        "evolution_action": "evolve_high_score_overscore",
        "operator_route": {
            "primary_operator": operators[0] if operators else None,
            "backup_operators": operators[1:],
            "avoid_operators": [],
        },
    }


def test_remaining_boundary_slot_caps_window_dispatch():
    state = initialize_search_state(sample(), branch_window=3, boundary_target=5)
    state["boundary_candidate_count"] = 4
    updated, claimed = claim_branches(state)
    assert len(claimed) == 1
    assert len(updated["in_flight_branch_ids"]) == 1


def test_existing_in_flight_branches_reduce_available_window_capacity():
    state = initialize_search_state(sample(), branch_window=3)
    state, first_claim = claim_branches(state)
    assert len(first_claim) == 3
    state = mark_branch_terminal(
        state,
        branch_id=first_claim[0]["branch_id"],
        branch_status="validation_failed",
    )
    updated, next_claim = claim_branches(state)
    assert len(next_claim) == 1
    assert len(updated["in_flight_branch_ids"]) == 3


def test_natural_plan_contains_only_explicit_candidate_list():
    record = sample(OPS[:2])
    record["operator_route"]["avoid_operators"] = [OPS[1]]
    state = initialize_search_state(record, branch_window=3)
    assert state["selected_operator_ids"] == [OPS[0]]
    assert [entry["operator_id"] for entry in state["operator_plan"]] == [OPS[0]]
    assert OPS[2] in state["omitted_registered_operator_ids"]


def test_explicit_empty_candidate_list_does_not_fall_back_to_primary():
    record = sample(OPS[:2])
    record["operator_route"]["selected_operator_ids"] = []
    state = initialize_search_state(record, branch_window=3)

    assert state["selected_operator_ids"] == []
    assert state["operator_plan"] == []
    assert state["termination_reason"] == "candidate_list_exhausted"


def test_stable_branch_cannot_be_claimed_twice():
    state = initialize_search_state(sample(OPS[:1]), branch_window=3)
    state, first = claim_branches(state)
    state, second = claim_branches(state)
    assert len(first) == 1
    assert second == []


def test_sibling_duplicate_registration_is_deterministic():
    state = initialize_search_state(sample(OPS[:2]), branch_window=2)
    state, dispatch = claim_branches(state)
    first_id, second_id = dispatch[0]["branch_id"], dispatch[1]["branch_id"]

    state, first_action = register_generated_prompt(
        state,
        branch_id=first_id,
        prompt="same   evolved\nprompt",
    )
    state, second_action = register_generated_prompt(
        state,
        branch_id=second_id,
        prompt="same evolved prompt",
    )
    state, exhausted_action = register_generated_prompt(
        state,
        branch_id=second_id,
        prompt="same evolved prompt",
    )

    assert first_action == "accepted"
    assert second_action == "retry_duplicate"
    assert exhausted_action == "duplicate_exhausted"
    second_entry = state["operator_plan"][1]
    assert second_entry["generation_attempt_count"] == 2
    assert second_entry["duplicate_retry_count"] == 1


def test_multi_branch_merge_never_overflows_boundary_target():
    record = sample(OPS[:3])
    state = initialize_search_state(record, branch_window=3, boundary_target=5)
    state["boundary_candidate_count"] = 2
    state, dispatch_records = build_dispatch_records(record, state)
    assert [row["search_dispatch"]["generation_sequence"] for row in dispatch_records] == [1, 2, 3]
    assert all(row["search_dispatch"]["sibling_generation_serial"] for row in dispatch_records)

    for row in dispatch_records:
        decision = dict(row)
        decision["score_rate"] = 0.5
        decision["decision_evaluation_status"] = "completed"
        decision["experimental_evaluation_status"] = "pending"
        state = merge_decision_result(state, decision)

    assert state["boundary_candidate_count"] == 5
    assert state["termination_reason"] == "boundary_target_reached"
    assert state["status"] == "completed"
    assert claim_branches(state)[1] == []


def test_branch_candidate_selection_keeps_independent_siblings():
    records = []
    for index, operator in enumerate(OPS[:2], start=1):
        records.append(
            {
                "sample_id": "same-sample",
                "candidate_group_id": "same-sample::root",
                "candidate_id": f"same-sample::root::{operator}",
                "branch_id": f"same-sample::root::{operator}",
                "candidate_operator": operator,
                "prompt": f"candidate-{index}",
                "question_evolved": True,
                "validation_result": {"passed": True},
                "difficulty_gain_validation": {
                    "gain_label": "clear_gain",
                    "difficulty_gain_score": 0.9,
                    "risk_tags": [],
                },
                "candidate_generation": {},
                "meta_info": {"parent_snapshot": {"prompt": "parent"}},
            }
        )
    selected, _ = select_candidates(records, branch_mode=True)
    assert len(selected) == 2
    assert {row["branch_id"] for row in selected} == {
        row["branch_id"] for row in records
    }
