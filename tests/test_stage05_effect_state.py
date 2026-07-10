import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from analyze_evolution_effect import analyze_records, build_effect_matrix
from update_sample_state import classify_preselection_invalid_cases, update_records


def load_jsonl(path: Path):
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def test_effect_analysis_labels_drop_full_invalid_and_review_cases():
    previous = load_jsonl(ROOT / "tests" / "fixtures" / "stage05_previous_scored.jsonl")
    current = load_jsonl(ROOT / "tests" / "fixtures" / "stage05_scored.jsonl")
    analyzed = analyze_records(current, previous_records=previous)
    effects = {record["sample_id"]: record["effect_analysis"] for record in analyzed}

    assert effects["stage05-drop"]["effect_label"] == "effective_boundary_probe"
    assert effects["stage05-drop"]["lightweight_boundary_hit"] is True
    assert round(effects["stage05-drop"]["delta_score_rate"], 2) == -0.38

    assert effects["stage05-full"]["effect_label"] == "full_score_no_drop"
    assert effects["stage05-full"]["is_full_score"] is True

    assert effects["stage05-invalid"]["effect_label"] == "invalid_complexity"
    assert effects["stage05-invalid"]["complexity_passed"] is False

    assert effects["stage05-review"]["effect_label"] == "needs_manual_review"
    assert effects["stage05-review"]["hit_confidence"] == "low"
    assert effects["stage05-review"]["needs_manual_review"] is True
    assert effects["stage05-review"]["focus_answer_alignment"]["matches"] is True


def test_score_rate_after_prefers_current_scoring_over_round0_summary():
    previous = [
        {
            "sample_id": "score-source",
            "prompt": "previous prompt",
            "scoring_result": {"candidate_answer": "old answer", "total_awarded": 10, "total_possible": 10},
            "score_rate": 1.0,
        }
    ]
    current = [
        {
            "sample_id": "score-source",
            "prompt": "current prompt",
            "question_evolved": True,
            "round0_score_summary": {"stable_score": 1.0},
            "meta_info": {
                "question_evolution_metadata": {
                    "question_evolved": True,
                    "operator_used": "O1_gap_choice",
                    "expected_evaluation_focus": ["minimal missing premise"],
                }
            },
            "validation_result": {"passed": True, "repeat_pattern_risk": "low"},
            "scoring_result": {
                "candidate_answer": "new answer misses the target premise",
                "total_awarded": 4,
                "total_possible": 10,
            },
            "score_rate": 0.4,
        }
    ]

    analyzed = analyze_records(current, previous_records=previous)
    effect = analyzed[0]["effect_analysis"]

    assert effect["score_rate_after"] == 0.4
    assert effect["score_after_source"] == "score_rate"
    assert round(effect["delta_score_rate"], 2) == -0.6


def test_score_rate_after_uses_scoring_result_before_round0_summary():
    previous = [
        {
            "sample_id": "score-result-source",
            "prompt": "previous prompt",
            "scoring_result": {"candidate_answer": "old answer", "total_awarded": 10, "total_possible": 10},
            "score_rate": 1.0,
        }
    ]
    current = [
        {
            "sample_id": "score-result-source",
            "prompt": "current prompt",
            "question_evolved": True,
            "round0_score_summary": {"stable_score": 1.0},
            "meta_info": {
                "question_evolution_metadata": {
                    "question_evolved": True,
                    "operator_used": "O1_gap_choice",
                    "expected_evaluation_focus": ["minimal missing premise"],
                }
            },
            "validation_result": {"passed": True, "repeat_pattern_risk": "low"},
            "scoring_result": {
                "candidate_answer": "new answer misses the target premise",
                "total_awarded": 4,
                "total_possible": 10,
            },
        }
    ]

    analyzed = analyze_records(current, previous_records=previous)
    effect = analyzed[0]["effect_analysis"]

    assert effect["score_rate_after"] == 0.4
    assert effect["score_after_source"] == "scoring_result.total_awarded/total_possible"


def test_focus_mismatch_does_not_become_effective_boundary_probe():
    previous = [
        {
            "sample_id": "focus-mismatch",
            "prompt": "上一轮题。",
            "scoring_result": {"candidate_answer": "上一轮答案。", "total_awarded": 10, "total_possible": 10},
            "score_rate": 1.0,
        }
    ]
    current = [
        {
            "sample_id": "focus-mismatch",
            "prompt": "请区分判据内和判据外信息。",
            "question_evolved": True,
            "meta_info": {
                "question_evolution_metadata": {
                    "question_evolved": True,
                    "operator_used": "O4_near_level_ranking",
                    "expected_evaluation_focus": ["是否排除判据外信息"],
                }
            },
            "validation_result": {"passed": True, "repeat_pattern_risk": "low"},
            "scoring_result": {
                "candidate_answer": "候选答案只讨论时间连续性缺口和人员轨迹跳步。",
                "total_awarded": 6,
                "total_possible": 10,
            },
            "score_rate": 0.6,
        }
    ]

    analyzed = analyze_records(current, previous_records=previous)
    effect = analyzed[0]["effect_analysis"]
    _, operator_memory, _, _ = update_records(analyzed)

    assert effect["lightweight_boundary_hit"] is False
    assert effect["effect_label"] == "needs_manual_review"
    assert effect["needs_manual_review"] is True
    assert effect["focus_answer_alignment"]["matches"] is False
    assert operator_memory == []


def test_effect_matrix_summarizes_sample_type_by_operator():
    previous = load_jsonl(ROOT / "tests" / "fixtures" / "stage05_previous_scored.jsonl")
    current = load_jsonl(ROOT / "tests" / "fixtures" / "stage05_scored.jsonl")
    analyzed = analyze_records(current, previous_records=previous)
    matrix = build_effect_matrix(analyzed)

    o1_rows = [row for row in matrix if row["operator_used"] == "O1_gap_choice"]
    assert sum(row["sample_count"] for row in o1_rows) == 2
    assert sum(row["lightweight_boundary_hit_count"] for row in o1_rows) == 1
    assert sum(row["full_score_count"] for row in o1_rows) == 1

    invalid_row = next(row for row in matrix if row["operator_used"] == "O4_near_level_ranking")
    assert invalid_row["invalid_complexity_count"] == 1


def test_state_update_and_memory_entries_cover_success_failure_invalid_review():
    previous = load_jsonl(ROOT / "tests" / "fixtures" / "stage05_previous_scored.jsonl")
    current = load_jsonl(ROOT / "tests" / "fixtures" / "stage05_scored.jsonl")
    analyzed = analyze_records(current, previous_records=previous)
    updated, operator_memory, failure_memory, invalid_memory = update_records(analyzed)
    states = {record["sample_id"]: record["evolution_state"] for record in updated}

    assert states["stage05-drop"]["stop_status"] == "effective_boundary_sample"
    assert states["stage05-full"]["stop_status"] == "local_tree_search_needed"
    assert states["stage05-full"]["consecutive_full_score_count"] == 2
    assert states["stage05-invalid"]["stop_status"] == "invalid_complexity_sample"
    assert states["stage05-review"]["stop_status"] == "continue_with_new_operator"
    assert states["stage05-drop"]["round"] == 3

    assert {entry["sample_id"] for entry in operator_memory} == {"stage05-drop"}

    assert {entry["sample_id"] for entry in failure_memory} == {"stage05-full"}
    assert failure_memory[0]["failure_type"] == "full_score_no_drop"

    assert {entry["sample_id"] for entry in invalid_memory} == {"stage05-invalid"}
    assert invalid_memory[0]["invalid_type"] == "format_difficulty_dominant"


def test_preselection_difficulty_gain_failures_feed_memory_entries():
    invalid_case = {
        "sample_id": "dg-memory",
        "round": 1,
        "candidate_id": "dg-memory::cand_1",
        "operator_used": "O1_gap_choice",
        "invalid_type": "leakage_or_simplification",
        "reason": "候选题直接泄漏关键缺口。",
        "sample_signature": {"core_capability": "证据链补强"},
        "risk_tags": ["missing_premise_named"],
        "difficulty_gain_validation": {
            "passed": False,
            "difficulty_gain_label": "leakage_or_simplification",
            "difficulty_gain_score": 0.3,
            "risk_tags": ["missing_premise_named"],
            "recommended_action": "reject_candidate",
            "reject_reason": "候选题直接泄漏关键缺口。",
        },
        "failure_memory_candidate": {
            "operator_id": "O1_gap_choice",
            "failure_type": "leakage_or_simplification",
            "risk_tags": ["missing_premise_named"],
            "reject_reason": "候选题直接泄漏关键缺口。",
            "recommended_retry_strategy": "switch_operator_family",
        },
    }

    failure_entries, invalid_entries = classify_preselection_invalid_cases([invalid_case])

    assert invalid_entries[0]["source_stage"] == "candidate_selection"
    assert invalid_entries[0]["risk_tags"] == ["missing_premise_named"]
    assert failure_entries[0]["source_stage"] == "difficulty_gain_validation"
    assert failure_entries[0]["score_rate_before"] is None
    assert failure_entries[0]["failure_type"] == "leakage_or_simplification"
    assert failure_entries[0]["risk_tags"] == ["missing_premise_named"]


def test_full_score_after_operator_switch_enters_local_tree_search():
    record = {
        "sample_id": "stable-stop",
        "question_evolved": True,
        "evolution_state": {
            "previous_operator": "O13_minimal_disqualifier",
            "consecutive_full_score_count": 1,
            "consecutive_same_operator_count": 1,
            "recommended_next_methods": ["O15_counterfactual_threshold_shift"],
            "stop_status": "continue_with_new_operator",
        },
        "effect_analysis": {
            "effect_label": "full_score_no_drop",
            "operator_used": "O15_counterfactual_threshold_shift",
            "is_full_score": True,
            "score_rate_after": 1.0,
            "complexity_passed": True,
        },
    }

    updated, _, failure_memory, _ = update_records([record])
    state = updated[0]["evolution_state"]

    assert state["consecutive_full_score_count"] == 2
    assert state["stop_status"] == "local_tree_search_needed"
    assert failure_memory[0]["failure_type"] == "full_score_no_drop"


def test_score_increased_rolls_back_parent_and_reroutes():
    record = {
        "sample_id": "rollback",
        "prompt": "进化后但更容易的新题",
        "question_evolved": True,
        "rubric": [{"title": "new"}],
        "score_prompt": "new score prompt",
        "scoring_result": {"total_awarded": 9, "total_possible": 10},
        "score_rate": 0.9,
        "meta_info": {
            "references": ["新题参考答案"],
            "parent_snapshot": {
                "prompt": "父题",
                "rubric": [{"title": "old"}],
                "score_prompt": "old score prompt",
                "scoring_result": {"total_awarded": 6, "total_possible": 10},
                "score_rate": 0.6,
                "question_evolved": False,
                "references": ["父题参考答案"],
                "prompt_old": None,
                "question_evolution_metadata": None,
            },
        },
        "effect_analysis": {
            "effect_label": "score_increased",
            "operator_used": "O13_minimal_disqualifier",
            "is_full_score": False,
            "score_rate_before": 0.6,
            "score_rate_after": 0.9,
            "complexity_passed": True,
            "lightweight_hit_reason": "分数升高",
        },
    }

    updated, _, failure_memory, _ = update_records([record])
    rolled_back = updated[0]

    assert rolled_back["prompt"] == "父题"
    assert rolled_back["rubric"] == [{"title": "old"}]
    assert rolled_back["score_prompt"] == "old score prompt"
    assert rolled_back["scoring_result"]["total_awarded"] == 6
    assert rolled_back["score_rate"] == 0.6
    assert rolled_back["meta_info"]["references"] == ["父题参考答案"]
    assert rolled_back["question_evolved"] is False
    assert rolled_back["evolution_state"]["stop_status"] == "rollback_and_reroute"
    assert rolled_back["evolution_state"]["recommended_next_methods"]
    assert failure_memory[0]["operator_used"] == "O13_minimal_disqualifier"


def test_effect_analysis_prefers_selected_candidate_operator():
    previous = [{"sample_id": "selected-op", "score_rate": 1.0}]
    current = [{
        "sample_id": "selected-op",
        "prompt": "题目",
        "question_evolved": True,
        "candidate_selection": {"selected_operator": "O17_action_vs_fact_threshold"},
        "candidate_operator": "O17_action_vs_fact_threshold",
        "meta_info": {"question_evolution_metadata": {
            "operator_used": "O10_evidence_sufficiency_ladder",
            "expected_evaluation_focus": ["处置触发与事实定性"],
        }},
        "validation_result": {"passed": True, "repeat_pattern_risk": "low"},
        "scoring_result": {
            "candidate_answer": "处置触发和事实定性混淆。",
            "total_awarded": 6,
            "total_possible": 10,
        },
        "score_rate": 0.6,
    }]

    effect = analyze_records(current, previous_records=previous)[0]["effect_analysis"]
    assert effect["operator_used"] == "O17_action_vs_fact_threshold"


def test_score_increase_takes_precedence_when_new_score_reaches_full():
    previous = [{"sample_id": "increase-to-full", "score_rate": 0.8}]
    current = [{
        "sample_id": "increase-to-full",
        "prompt": "新题",
        "question_evolved": True,
        "candidate_selection": {"selected_operator": "O10_evidence_sufficiency_ladder"},
        "meta_info": {"question_evolution_metadata": {"expected_evaluation_focus": ["业务判断竞争"]}},
        "validation_result": {"passed": True, "repeat_pattern_risk": "low"},
        "scoring_result": {"candidate_answer": "完整正确", "total_awarded": 10, "total_possible": 10},
        "score_rate": 1.0,
    }]
    effect = analyze_records(current, previous_records=previous)[0]["effect_analysis"]
    assert effect["effect_label"] == "score_increased"


if __name__ == "__main__":
    test_effect_analysis_labels_drop_full_invalid_and_review_cases()
    test_score_rate_after_prefers_current_scoring_over_round0_summary()
    test_score_rate_after_uses_scoring_result_before_round0_summary()
    test_focus_mismatch_does_not_become_effective_boundary_probe()
    test_effect_matrix_summarizes_sample_type_by_operator()
    test_state_update_and_memory_entries_cover_success_failure_invalid_review()
    test_preselection_difficulty_gain_failures_feed_memory_entries()
    test_full_score_after_operator_switch_enters_local_tree_search()
    test_score_increased_rolls_back_parent_and_reroutes()
    test_effect_analysis_prefers_selected_candidate_operator()
    test_score_increase_takes_precedence_when_new_score_reaches_full()
    print("stage05 effect analysis and state update checks passed")
