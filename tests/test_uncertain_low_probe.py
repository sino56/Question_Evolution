import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from select_evolution_candidates import (
    RECONSTRUCT_LOW_SCORE_BOUNDARY,
    STOP_EVOLUTION,
    process_records,
)


def make_uncertain_low():
    return {
        "sample_id": "uncertain-low",
        "prompt": "原题",
        "score_rate": 0.62,
        "sample_profile": {"core_capability": "证据链补强"},
        "overscore_diagnosis": {
            "is_worth_evolving": True,
            "candidate_overscore_cause": "主线抓偏",
            "target_failure_mode": "真实边界",
        },
        "round0_score_summary": {
            "admission_status": "uncertain_low",
            "stable_score": 0.58,
            "recommended_evolution_budget": 0,
        },
    }


def test_uncertain_low_probe_is_off_by_default():
    selected = process_records([make_uncertain_low()], enable_uncertain_low_probe=False)

    assert selected[0]["evolution_action"] == STOP_EVOLUTION
    assert selected[0]["evolution_budget"]["recommended_num_candidates"] == 0


def test_uncertain_low_probe_admits_low_budget_when_enabled():
    selected = process_records(
        [make_uncertain_low()],
        enable_uncertain_low_probe=True,
        uncertain_low_probe_min_score=0.55,
    )

    assert selected[0]["evolution_action"] == RECONSTRUCT_LOW_SCORE_BOUNDARY
    assert selected[0]["evolution_budget"]["recommended_num_candidates"] == 1
    assert "uncertain_low_probe enabled" in selected[0]["evolution_action_reason"]


if __name__ == "__main__":
    test_uncertain_low_probe_is_off_by_default()
    test_uncertain_low_probe_admits_low_budget_when_enabled()
    print("uncertain low probe checks passed")
