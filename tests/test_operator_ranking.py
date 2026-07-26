import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operator_ranking import build_operator_statistics, rank_selected_operators


def row(operator_id, status, duration, scene="video"):
    return {
        "operator_id": operator_id,
        "branch_status": status,
        "branch_duration_seconds": duration,
        "sample_profile": {"scene": scene},
        "decision_evaluation_status": "completed",
        "validation_result": {"passed": True},
    }


def test_statistics_report_boundary_validation_duration_and_error_rates():
    stats = build_operator_statistics(
        [
            row("O12", "boundary_candidate", 10),
            row("O12", "no_score_change", 30),
            row("O13", "branch_error", 5),
        ]
    )
    o12 = stats["operators"]["O12"]
    assert o12["attempt_count"] == 2
    assert o12["boundary_candidate_rate"] == 0.5
    assert o12["average_duration_seconds"] == 20
    assert stats["operators"]["O13"]["error_rate"] == 1.0


def test_statistics_read_append_only_artifact_envelopes():
    stats = build_operator_statistics(
        [
            {
                "format_version": 1,
                "artifact_id": "p::O12::complete_branch",
                "artifact_type": "complete_branch",
                "branch_id": "p::O12",
                "record": row("O12", "boundary_candidate", 5),
            }
        ]
    )

    assert stats["operators"]["O12"]["boundary_candidate_count"] == 1
    assert stats["operators"]["O12"]["average_duration_seconds"] == 5


def test_ranking_preserves_primary_and_backup_and_only_reorders_remaining_members():
    selected = ["primary", "backup", "slow", "fast"]
    statistics = {
        "operators": {
            "slow": {
                "attempt_count": 10,
                "boundary_candidate_count": 5,
                "average_duration_seconds": 100,
                "error_rate": 0,
            },
            "fast": {
                "attempt_count": 10,
                "boundary_candidate_count": 4,
                "average_duration_seconds": 10,
                "error_rate": 0,
            },
        },
        "groups": [],
    }
    ranked = rank_selected_operators(
        selected,
        primary_operator="primary",
        backup_operators=["backup"],
        statistics=statistics,
        exploration_ratio=0,
    )
    assert ranked[:2] == ["primary", "backup"]
    assert ranked[2:] == ["fast", "slow"]
    assert set(ranked) == set(selected)


def test_yield_order_improves_fixed_budget_expected_boundary_output():
    selected = ["slow-low", "fast-high", "medium"]
    statistics = {
        "operators": {
            "slow-low": {
                "attempt_count": 20,
                "boundary_candidate_count": 2,
                "average_duration_seconds": 100,
                "error_rate": 0,
            },
            "fast-high": {
                "attempt_count": 20,
                "boundary_candidate_count": 14,
                "average_duration_seconds": 10,
                "error_rate": 0,
            },
            "medium": {
                "attempt_count": 20,
                "boundary_candidate_count": 8,
                "average_duration_seconds": 20,
                "error_rate": 0,
            },
        },
        "groups": [],
    }
    ranked = rank_selected_operators(
        selected,
        statistics=statistics,
        exploration_ratio=0,
    )
    expected_rates = {"slow-low": 0.1, "fast-high": 0.7, "medium": 0.4}
    baseline_first_two = sum(expected_rates[operator] for operator in selected[:2])
    ranked_first_two = sum(expected_rates[operator] for operator in ranked[:2])
    assert ranked_first_two > baseline_first_two
