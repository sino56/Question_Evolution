import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from candidate_selection import select_candidates


def make_candidate(
    sample_id,
    candidate_suffix,
    label,
    *,
    passed=False,
    score=0.66,
    risk_tags=None,
    recommended_action="reject_candidate",
    weak_probe=None,
):
    return {
        "sample_id": sample_id,
        "candidate_group_id": sample_id,
        "candidate_id": f"{sample_id}::{candidate_suffix}",
        "candidate_operator": "O1_gap_choice",
        "prompt": f"{sample_id} candidate {candidate_suffix}",
        "question_evolved": True,
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
            "difficulty_gain_score": score,
            "no_leakage_score": 0.9,
            "competitive_judgment_score": 0.8,
            "risk_tags": risk_tags or [],
            "recommended_action": recommended_action,
            "weak_probe": weak_probe or {"enabled": False},
        },
        "meta_info": {
            "prompt_old": f"{sample_id} original",
            "question_evolution_metadata": {
                "question_evolved": True,
                "operator_used": "O1_gap_choice",
                "expected_evaluation_focus": ["target boundary"],
            },
        },
    }


def selection(record):
    return record["candidate_selection"]


def test_main_chain_labels_keep_legacy_selection_status():
    selected, invalid_cases = select_candidates(
        [
            make_candidate("flow-main", "clear", "clear_gain", passed=True, score=0.86),
            make_candidate("flow-main", "weak", "weak_gain", score=0.64),
        ]
    )

    result = selection(selected[0])
    assert result["selected"] is True
    assert result["selected_candidate_id"] == "flow-main::clear"
    assert result["selection_status"] == "selected_after_difficulty_gain_validation"
    assert result["candidate_flow"] == "main_chain_candidate"
    assert result["selected_for_exploration"] is False
    assert invalid_cases == []


def test_weak_and_manual_review_enter_exploration_without_hard_risk():
    weak, weak_invalid = select_candidates([make_candidate("flow-weak", "cand", "weak_gain", score=0.64)])
    manual, manual_invalid = select_candidates(
        [make_candidate("flow-manual", "cand", "needs_manual_review", score=0.63)]
    )

    weak_selection = selection(weak[0])
    manual_selection = selection(manual[0])
    assert weak_selection["candidate_flow"] == "exploration_candidate"
    assert weak_selection["selection_status"] == "selected_for_exploration"
    assert weak_selection["selected_for_exploration"] is True
    assert weak_selection["selection_score"] <= 0.60
    assert manual_selection["candidate_flow"] == "exploration_candidate"
    assert manual_selection["selected_for_exploration"] is True
    assert manual_selection["selection_score"] <= 0.58
    assert weak_invalid == []
    assert manual_invalid == []


def test_no_gain_passes_through_without_invalid_memory_when_it_has_no_exploration_value():
    selected, invalid_cases = select_candidates(
        [make_candidate("flow-nogain-pass", "cand", "no_gain", score=0.30)]
    )

    result = selection(selected[0])
    assert result["selected"] is False
    assert result["selection_status"] == "not_selected_no_exploration_value"
    assert result["candidate_flow"] == "pass_through_candidate"
    assert result["selected_for_exploration"] is False
    assert invalid_cases == []


def test_no_gain_with_value_can_enter_exploration_but_hard_risk_still_rejects():
    valued, valued_invalid = select_candidates(
        [
            make_candidate(
                "flow-nogain-value",
                "cand",
                "no_gain",
                score=0.62,
                recommended_action="admit_if_no_better_candidate",
            )
        ]
    )
    hard, hard_invalid = select_candidates(
        [
            make_candidate(
                "flow-nogain-hard",
                "cand",
                "no_gain",
                score=0.62,
                risk_tags=["missing_premise_named"],
                recommended_action="admit_if_no_better_candidate",
            )
        ]
    )

    valued_selection = selection(valued[0])
    hard_selection = selection(hard[0])
    assert valued_selection["candidate_flow"] == "exploration_candidate"
    assert valued_selection["selected_for_exploration"] is True
    assert valued_selection["selection_score"] <= 0.55
    assert valued_invalid == []
    assert hard_selection["selected"] is False
    assert hard_selection["selection_status"] == "no_candidate_passed_difficulty_gain"
    assert hard_invalid[0]["candidate_flow"] == "hard_reject"


def test_hard_labels_and_template_simplification_are_rejected():
    hard_label = make_candidate("flow-hard-label", "cand", "axis_shift", score=0.70)
    template = make_candidate("flow-template", "cand", "weak_gain", score=0.70)
    template["difficulty_gain_validation"]["generic_answer_success_likelihood"] = 0.90
    template["difficulty_gain_validation"]["specific_fact_dependency_score"] = 0.20

    selected, invalid_cases = select_candidates([hard_label, template])

    assert selection(selected[0])["selected"] is False
    assert selection(selected[0])["selection_status"] == "no_candidate_passed_difficulty_gain"
    assert {case["candidate_flow"] for case in invalid_cases} == {"hard_reject"}
    assert len(invalid_cases) == 2


def test_light_factual_fatal_errors_hard_reject_and_warnings_only_penalize():
    fatal = make_candidate("flow-factual-fatal", "cand", "clear_gain", passed=True, score=0.86)
    fatal["light_factual_check"] = {
        "passed": False,
        "fatal_errors": ["新增 30分钟 条件"],
        "warnings": [],
        "risk_tags": ["numeric_fact_added_or_conflicted"],
    }
    warning = make_candidate("flow-factual-warning", "cand", "clear_gain", passed=True, score=0.86)
    warning["light_factual_check"] = {
        "passed": True,
        "fatal_errors": [],
        "warnings": ["场景收窄"],
        "risk_tags": ["scenario_narrowing_warning"],
    }

    fatal_selected, fatal_invalid = select_candidates([fatal])
    warning_selected, warning_invalid = select_candidates([warning])

    assert selection(fatal_selected[0])["selected"] is False
    assert selection(fatal_selected[0])["selection_status"] == "no_candidate_passed_difficulty_gain"
    assert fatal_invalid[0]["candidate_flow"] == "hard_reject"
    assert selection(warning_selected[0])["candidate_flow"] == "main_chain_candidate"
    assert selection(warning_selected[0])["light_factual_warning_count"] == 1
    assert selection(warning_selected[0])["selection_score"] < 0.86
    assert warning_invalid == []


def test_template_risk_is_soft_penalty_and_rubric_risk_is_report_only():
    base = make_candidate("flow-template-soft", "base", "clear_gain", passed=True, score=0.86)
    high = make_candidate("flow-template-soft", "high", "clear_gain", passed=True, score=0.86)
    high["difficulty_gain_validation"]["template_affordance_risk"] = "high"
    high["difficulty_gain_validation"]["rubric_shortcut_risk"] = "high"

    selected, invalid_cases = select_candidates([base, high])
    chosen = selection(selected[0])
    rejected = chosen["rejected_candidates"][0]

    assert chosen["selected_candidate_id"] == "flow-template-soft::base"
    assert rejected["template_affordance_risk"] == "high"
    assert rejected["rubric_shortcut_risk"] == "high"
    assert invalid_cases == []


def test_exploration_budget_is_one_per_group_and_five_per_round_by_default():
    records = [
        make_candidate(f"flow-budget-{i}", "cand", "weak_gain", score=0.64)
        for i in range(6)
    ]
    selected, invalid_cases = select_candidates(records, max_exploration_candidates_per_round=5)
    selections = [selection(record) for record in selected]

    assert sum(item["selected_for_exploration"] for item in selections) == 5
    assert [item["selection_status"] for item in selections].count("exploration_budget_exhausted") == 1
    assert invalid_cases == []

    same_group, _ = select_candidates(
        [
            make_candidate("flow-one-group", "low", "weak_gain", score=0.61),
            make_candidate("flow-one-group", "high", "weak_gain", score=0.66),
        ]
    )
    assert len(same_group) == 1
    assert selection(same_group[0])["selected_for_exploration"] is True
    assert selection(same_group[0])["selected_candidate_id"] == "flow-one-group::high"


if __name__ == "__main__":
    test_main_chain_labels_keep_legacy_selection_status()
    test_weak_and_manual_review_enter_exploration_without_hard_risk()
    test_no_gain_passes_through_without_invalid_memory_when_it_has_no_exploration_value()
    test_no_gain_with_value_can_enter_exploration_but_hard_risk_still_rejects()
    test_hard_labels_and_template_simplification_are_rejected()
    test_light_factual_fatal_errors_hard_reject_and_warnings_only_penalize()
    test_template_risk_is_soft_penalty_and_rubric_risk_is_report_only()
    test_exploration_budget_is_one_per_group_and_five_per_round_by_default()
    print("candidate flow split checks passed")
