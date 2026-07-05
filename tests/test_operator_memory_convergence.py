import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from operator_router import route_records


def make_item(round_number=4):
    return {
        "sample_id": "memory-route",
        "round": round_number,
        "prompt": "原题",
        "score_rate": 0.9,
        "evolution_action": "evolve_high_score_overscore",
        "sample_profile": {
            "core_capability": "证据链补强",
            "claim_level": "可疑线索",
            "problem_shape": "候选项区分",
            "external_knowledge_risk": "low",
        },
        "overscore_diagnosis": {
            "is_worth_evolving": True,
            "candidate_overscore_cause": "漏最小关键事实",
            "target_failure_mode": "选错最关键缺口",
        },
    }


def failure(round_number, suffix=""):
    return {
        "sample_id": f"memory-route-{round_number}{suffix}",
        "round": round_number,
        "sample_signature": {
            "core_capability": "证据链补强",
            "claim_level": "可疑线索",
            "problem_shape": "候选项区分",
            "candidate_overscore_cause": "漏最小关键事实",
        },
        "operator_used": "O1_gap_choice",
        "surface_form_family": "evidence_relation_comparison",
        "failure_type": "score_increased",
        "failure_reason": "negative gain",
    }


def test_one_recent_same_failure_warns_only():
    routed = route_records([make_item()], failure_memory=[failure(4)], failure_memory_window_rounds=3)
    route = routed[0]["operator_route"]

    assert route["primary_operator"] == "O1_gap_choice"
    assert route["memory_warnings"][0]["action"] == "warn_only"
    assert route["downrank_operator_surface_forms"] == []
    assert route["avoid_operator_surface_forms"] == []
    assert "O1_gap_choice" not in route["avoid_operators"]


def test_two_recent_same_failures_downrank_operator_surface_form():
    routed = route_records(
        [make_item()],
        failure_memory=[failure(3), failure(4)],
        failure_memory_window_rounds=3,
    )
    route = routed[0]["operator_route"]

    assert route["primary_operator"] == "O2_subclaim_localization"
    assert route["downrank_operator_surface_forms"][0]["action"] == "downrank"
    assert "O1_gap_choice" in route["backup_operators"]
    assert "O1_gap_choice" not in route["avoid_operators"]


def test_three_recent_same_failures_avoid_surface_form_without_coarse_operator_ban():
    routed = route_records(
        [make_item()],
        failure_memory=[failure(2), failure(3), failure(4)],
        failure_memory_window_rounds=3,
    )
    route = routed[0]["operator_route"]

    assert route["primary_operator"] == "O2_subclaim_localization"
    assert route["avoid_operator_surface_forms"][0]["action"] == "avoid"
    assert route["avoid_operator_surface_forms"][0]["operator_used"] == "O1_gap_choice"
    assert "O1_gap_choice" not in route["avoid_operators"]


def test_old_failures_and_missing_surface_form_do_not_count():
    old = failure(1)
    missing_surface = failure(4, "missing")
    missing_surface.pop("surface_form_family")

    routed = route_records(
        [make_item()],
        failure_memory=[old, missing_surface],
        failure_memory_window_rounds=3,
    )
    route = routed[0]["operator_route"]

    assert route["primary_operator"] == "O1_gap_choice"
    assert route["memory_warnings"] == []
    assert route["downrank_operator_surface_forms"] == []
    assert route["avoid_operator_surface_forms"] == []


if __name__ == "__main__":
    test_one_recent_same_failure_warns_only()
    test_two_recent_same_failures_downrank_operator_surface_form()
    test_three_recent_same_failures_avoid_surface_form_without_coarse_operator_ban()
    test_old_failures_and_missing_surface_form_do_not_count()
    print("operator memory convergence checks passed")
