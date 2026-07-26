import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from search_coordinator import (
    build_dispatch_records,
    initialize_search_state,
    merge_decision_result,
)
from search_performance import aggregate_performance_runs, build_comparison_report


OPS = [
    "O10_evidence_sufficiency_ladder",
    "O11_unobserved_state_attribution",
    "O12_conjunctive_necessity",
    "O13_minimal_disqualifier",
]
SCORES = dict(zip(OPS, [0.8, 1.0, 1.1, 0.7]))


def parent():
    return {
        "sample_id": "equivalence",
        "prompt": "parent",
        "score_rate": 1.0,
        "evolution_action": "evolve_high_score_overscore",
        "operator_route": {
            "primary_operator": OPS[0],
            "backup_operators": OPS[1:],
            "avoid_operators": [],
        },
    }


def run_fixed_search(window):
    record = parent()
    state = initialize_search_state(record, branch_window=window)
    full_branches = {}
    memory = {}
    while state["status"] != "completed":
        state, dispatch = build_dispatch_records(record, state)
        if not dispatch:
            break
        for branch in dispatch:
            operator = branch["candidate_operator"]
            decision = {
                **branch,
                "prompt": f"candidate::{operator}",
                "validation_result": {"passed": True},
                "qwen_answer_trials": [
                    {"trial_index": trial, "candidate_answer": f"answer-{trial}"}
                    for trial in range(1, 4)
                ],
                "qwen_judge_repeats": [1, 2],
                "score_rate": SCORES[operator],
                "decision_evaluation_status": "completed",
                "experimental_evaluation_status": "pending",
            }
            state = merge_decision_result(state, decision)
            complete = {
                **decision,
                "experimental_evaluation_status": "completed",
                "gpt_trial_order": [(trial, repeat) for trial in range(1, 4) for repeat in (1, 2)],
            }
            full_branches[branch["branch_id"]] = {
                "branch_id": complete["branch_id"],
                "parent_node_id": complete["parent_node_id"],
                "candidate_operator": complete["candidate_operator"],
                "prompt": complete["prompt"],
                "score_rate": complete["score_rate"],
                "qwen_answer_trials": complete["qwen_answer_trials"],
                "qwen_judge_repeats": complete["qwen_judge_repeats"],
                "gpt_trial_order": complete["gpt_trial_order"],
                "experimental_evaluation_status": complete[
                    "experimental_evaluation_status"
                ],
            }
            status = state["branch_summaries"][branch["branch_id"]]["branch_status"]
            memory[f"{branch['branch_id']}::effect"] = {
                "branch_id": branch["branch_id"],
                "branch_status": status,
            }
    projection = {
        "attempted": sorted(state["attempted_selected_operator_ids"]),
        "boundaries": sorted(
            branch_id
            for branch_id, summary in state["branch_summaries"].items()
            if summary["branch_status"] == "boundary_candidate"
        ),
        "termination_reason": state["termination_reason"],
        "memory": memory,
        "branches": full_branches,
    }
    return state, projection


def test_window_one_and_three_have_identical_fixed_response_business_projection():
    state_one, projection_one = run_fixed_search(1)
    state_three, projection_three = run_fixed_search(3)
    assert projection_three == projection_one
    assert state_one["boundary_candidate_count"] == state_three["boundary_candidate_count"] == 2
    assert state_one["termination_reason"] == state_three["termination_reason"] == "candidate_list_exhausted"


def test_replaying_a_persisted_decision_does_not_double_count_boundary():
    record = parent()
    state = initialize_search_state(record, branch_window=1)
    state, dispatch = build_dispatch_records(record, state)
    decision = {
        **dispatch[0],
        "score_rate": 0.5,
        "decision_evaluation_status": "completed",
        "experimental_evaluation_status": "pending",
    }
    merged = merge_decision_result(state, decision)
    replayed = merge_decision_result(deepcopy(merged), decision)
    assert replayed["boundary_candidate_count"] == 1
    assert replayed["decision_completed_count"] == 1


def test_performance_report_requires_three_runs_and_reports_median_and_range():
    runs = [
        {
            "branches_completed_per_wall_clock_hour": value,
            "decision_evaluations_completed_per_wall_clock_hour": value + 1,
            "model_error_rate": error,
        }
        for value, error in ((10, 0.02), (15, 0.01), (12, 0.03))
    ]
    report = aggregate_performance_runs(runs)
    throughput = report["metrics"]["branches_completed_per_wall_clock_hour"]
    assert throughput == {
        "median": 12.0,
        "min": 10.0,
        "max": 15.0,
        "values": [10.0, 15.0, 12.0],
    }
    assert report["model_error_rate"]["median"] == 0.02


def test_performance_comparison_reports_baseline_optimized_and_speedup():
    baseline = [
        {
            "branches_completed_per_wall_clock_hour": value,
            "decision_evaluations_completed_per_wall_clock_hour": value,
            "boundary_candidates_per_wall_clock_hour": value / 2,
            "model_error_rate": 0.02,
        }
        for value in (10, 12, 14)
    ]
    optimized = [
        {
            "branches_completed_per_wall_clock_hour": value,
            "decision_evaluations_completed_per_wall_clock_hour": value,
            "boundary_candidates_per_wall_clock_hour": value / 2,
            "model_error_rate": 0.01,
        }
        for value in (20, 24, 28)
    ]

    report = build_comparison_report(baseline, optimized)

    assert report["baseline"]["run_count"] == 3
    assert report["optimized"]["run_count"] == 3
    assert (
        report["median_speedup"]["branches_completed_per_wall_clock_hour"]
        == 2.0
    )
