import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from analyze_evolution_effect import analyze_records
from update_sample_state import update_records


def test_score_increased_is_negative_gain_and_continues_with_new_operator():
    previous = [
        {
            "sample_id": "score-up",
            "prompt": "previous prompt",
            "score_rate": 0.70,
            "scoring_result": {"candidate_answer": "old answer", "total_awarded": 7, "total_possible": 10},
        }
    ]
    current = [
        {
            "sample_id": "score-up",
            "prompt": "current evolved prompt",
            "question_evolved": True,
            "score_rate": 0.80,
            "meta_info": {
                "question_evolution_metadata": {
                    "question_evolved": True,
                    "operator_used": "O1_gap_choice",
                    "expected_evaluation_focus": ["boundary"],
                }
            },
            "validation_result": {"passed": True, "repeat_pattern_risk": "low"},
            "scoring_result": {"candidate_answer": "new answer", "total_awarded": 8, "total_possible": 10},
        }
    ]

    analyzed = analyze_records(current, previous_records=previous)
    effect = analyzed[0]["effect_analysis"]
    updated, operator_memory, failure_memory, invalid_memory = update_records(analyzed)
    state = updated[0]["evolution_state"]

    assert effect["effect_label"] == "score_increased"
    assert effect["score_increased_after_evolution"] is True
    assert state["stop_status"] == "continue_with_new_operator"
    assert state["recommended_next_methods"] == [
        "O2_subclaim_localization",
        "O4_near_level_ranking",
        "O8_double_threshold_claim",
    ]
    assert operator_memory == []
    assert invalid_memory == []
    assert len(failure_memory) == 1
    assert failure_memory[0]["failure_type"] == "score_increased"


if __name__ == "__main__":
    test_score_increased_is_negative_gain_and_continues_with_new_operator()
    print("score increased negative-gain checks passed")
