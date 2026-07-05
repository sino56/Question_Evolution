import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from analyze_evolution_effect import analyze_records
from candidate_selection import select_candidates
from update_sample_state import update_records


def make_candidate(sample_id, *, candidate_operator, metadata_operator, label="clear_gain", passed=True):
    return {
        "sample_id": sample_id,
        "candidate_group_id": sample_id,
        "candidate_id": f"{sample_id}::cand_1",
        "candidate_operator": candidate_operator,
        "prompt": f"{sample_id} evolved prompt",
        "question_evolved": True,
        "score_rate": 0.80,
        "validation_result": {
            "passed": True,
            "main_axis_count": 1,
            "new_facts_count": 1,
            "output_tasks_count": 1,
            "candidate_options_count": 1,
            "counterfactual_count": 0,
            "estimated_prompt_chars": 180,
            "external_knowledge_risk": "low",
            "format_difficulty_risk": "low",
            "repeat_pattern_risk": "low",
        },
        "difficulty_gain_validation": {
            "passed": passed,
            "difficulty_gain_label": label,
            "difficulty_gain_score": 0.88,
            "no_leakage_score": 0.90,
            "competitive_judgment_score": 0.86,
            "risk_tags": [],
            "recommended_action": "admit_candidate",
        },
        "scoring_result": {
            "candidate_answer": "new answer",
            "total_awarded": 8,
            "total_possible": 10,
        },
        "meta_info": {
            "prompt_old": f"{sample_id} original prompt",
            "question_evolution_metadata": {
                "question_evolved": True,
                "operator_used": metadata_operator,
                "expected_evaluation_focus": ["target boundary"],
            },
        },
    }


def previous_record(sample_id):
    return {
        "sample_id": sample_id,
        "prompt": f"{sample_id} previous prompt",
        "score_rate": 0.60,
        "scoring_result": {
            "candidate_answer": "old answer",
            "total_awarded": 6,
            "total_possible": 10,
        },
    }


def test_selected_candidate_operator_is_authoritative_for_metadata_effect_and_memory():
    selected, invalid_cases = select_candidates(
        [
            make_candidate(
                "operator-sync",
                candidate_operator="O1_gap_choice",
                metadata_operator="O2_subclaim_localization",
            )
        ]
    )
    record = selected[0]
    selection = record["candidate_selection"]
    metadata = record["meta_info"]["question_evolution_metadata"]

    assert invalid_cases == []
    assert selection["selected_operator"] == "O1_gap_choice"
    assert metadata["operator_used"] == "O1_gap_choice"

    analyzed = analyze_records(selected, previous_records=[previous_record("operator-sync")])
    effect = analyzed[0]["effect_analysis"]
    updated, operator_memory, failure_memory, invalid_memory = update_records(analyzed)

    assert effect["effect_label"] == "score_increased"
    assert effect["operator_used"] == "O1_gap_choice"
    assert updated[0]["evolution_state"]["previous_operator"] == "O1_gap_choice"
    assert operator_memory == []
    assert invalid_memory == []
    assert failure_memory[0]["operator_used"] == "O1_gap_choice"


def test_fallback_pass_through_does_not_keep_rejected_candidate_operator_attribution():
    rejected = make_candidate(
        "operator-pass-through",
        candidate_operator="O1_gap_choice",
        metadata_operator="O2_subclaim_localization",
        label="leakage_or_simplification",
        passed=False,
    )

    selected, invalid_cases = select_candidates([rejected])
    record = selected[0]
    selection = record["candidate_selection"]
    metadata = record["meta_info"]["question_evolution_metadata"]

    assert invalid_cases[0]["candidate_flow"] == "hard_reject"
    assert selection["selected"] is False
    assert selection["selected_operator"] == ""
    assert metadata["question_evolved"] is False
    assert metadata["operator_used"] == ""
    assert "candidate_operator" not in record

    analyzed = analyze_records(selected, previous_records=[previous_record("operator-pass-through")])
    effect = analyzed[0]["effect_analysis"]
    assert effect["effect_label"] == "pass_through"
    assert effect["operator_used"] == ""


if __name__ == "__main__":
    test_selected_candidate_operator_is_authoritative_for_metadata_effect_and_memory()
    test_fallback_pass_through_does_not_keep_rejected_candidate_operator_attribution()
    print("operator attribution sync checks passed")
