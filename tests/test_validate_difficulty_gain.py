import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from candidate_selection import select_candidates
from validate_difficulty_gain import (
    apply_weak_probe_result,
    compute_difficulty_gain_score,
    generate_report,
    normalize_difficulty_gain_result,
    validate_records_rule_only,
)


def make_candidate(sample_id, candidate_id, prompt, difficulty_validation=None):
    record = {
        "sample_id": sample_id,
        "candidate_group_id": sample_id,
        "candidate_id": f"{sample_id}::{candidate_id}",
        "candidate_operator": "O1_gap_choice",
        "prompt": prompt,
        "question_evolved": True,
        "meta_info": {
            "prompt_old": "原题：根据现有证据判断结论是否成立，并说明还缺什么关键事实。",
            "references": ["参考答案应指出最小关键事实，而不是泛泛说证据不足。"],
        },
        "sample_profile": {"core_capability": "证据链补强"},
        "overscore_diagnosis": {"target_failure_mode": "选错最关键缺口"},
        "validation_result": {
            "passed": True,
            "main_axis_count": 1,
            "new_facts_count": 1,
            "output_tasks_count": 1,
            "candidate_options_count": 2,
            "counterfactual_count": 0,
            "estimated_prompt_chars": len(prompt),
            "external_knowledge_risk": "low",
            "format_difficulty_risk": "low",
            "repeat_pattern_risk": "low",
            "reject_reason": None,
        },
    }
    if difficulty_validation is not None:
        record["difficulty_gain_validation"] = difficulty_validation
    return record


def raw_result(label="clear_gain", scores=None, risk_tags=None):
    base_scores = {
        "axis_consistency_score": 0.9,
        "no_leakage_score": 0.86,
        "competitive_judgment_score": 0.82,
        "anti_clarity_trap_score": 0.78,
        "answerability_score": 0.9,
        "format_complexity_score": 0.95,
    }
    if scores:
        base_scores.update(scores)
    return {
        "difficulty_gain_label": label,
        "dimension_scores": base_scores,
        "risk_tags": risk_tags or [],
        "expected_qwen_failure_match": True,
        "recommended_action": "admit_candidate",
    }


def normalized(raw):
    return normalize_difficulty_gain_result(raw, validator_model="mock-validator")


def test_compute_difficulty_gain_score_uses_configured_weights():
    score = compute_difficulty_gain_score(
        {
            "axis_consistency_score": 0.9,
            "no_leakage_score": 0.8,
            "competitive_judgment_score": 0.7,
            "anti_clarity_trap_score": 0.6,
            "answerability_score": 1.0,
            "format_complexity_score": 1.0,
        }
    )

    assert score == 0.81


def test_clear_gain_passes_with_competitive_judgment():
    result = normalized(raw_result("clear_gain"))

    assert result["passed"] is True
    assert result["difficulty_gain_label"] == "clear_gain"
    assert result["competitive_judgment_score"] >= 0.75
    assert result["risk_tags"] == []


def test_high_risk_leakage_tags_reject_candidate():
    result = normalized(
        raw_result(
            "clear_gain",
            risk_tags=["missing_premise_named"],
        )
    )

    assert result["passed"] is False
    assert result["difficulty_gain_label"] == "leakage_or_simplification"
    assert "missing_premise_named" in result["risk_tags"]


def test_conclusion_hint_and_answer_path_are_hard_rejects():
    conclusion = normalized(raw_result("probable_gain", risk_tags=["conclusion_hint_revealed"]))
    scaffolded = normalized(raw_result("probable_gain", risk_tags=["answer_path_scaffolded"]))

    assert conclusion["passed"] is False
    assert "conclusion_hint_revealed" in conclusion["risk_tags"]
    assert scaffolded["passed"] is False
    assert "answer_path_scaffolded" in scaffolded["risk_tags"]


def test_format_axis_and_external_failures_get_labels():
    format_result = normalized(
        raw_result(
            "probable_gain",
            scores={"format_complexity_score": 0.25},
            risk_tags=["format_difficulty_only"],
        )
    )
    axis_result = normalized(raw_result("probable_gain", scores={"axis_consistency_score": 0.3}))
    external_result = normalized(
        raw_result(
            "probable_gain",
            scores={"answerability_score": 0.3},
            risk_tags=["external_fact_dependency"],
        )
    )

    assert format_result["difficulty_gain_label"] == "format_complexity_only"
    assert axis_result["difficulty_gain_label"] == "axis_shift"
    assert external_result["difficulty_gain_label"] == "unanswerable_or_external"


def test_low_competitive_judgment_is_rejected_as_no_gain():
    result = normalized(
        raw_result(
            "clear_gain",
            scores={"competitive_judgment_score": 0.45},
        )
    )

    assert result["passed"] is False
    assert result["difficulty_gain_label"] == "no_gain"
    assert "competitive_judgment_score" in result["reject_reason"]


def test_weak_probe_miss_lowers_priority_without_soft_risk():
    validation = normalized(raw_result("clear_gain"))
    weak_probe = {
        "enabled": True,
        "mode": "light",
        "probe_answer": "弱模型直接答中了关键点。",
        "probe_failure_detected": False,
        "target_failure_match": False,
        "probe_judgment": "weak probe did not expose target failure",
    }

    result = apply_weak_probe_result(validation, weak_probe)

    assert result["passed"] is True
    assert result["recommended_action"] == "admit_low_priority"
    assert "weak_probe_no_failure" in result["risk_tags"]


def test_weak_probe_miss_with_soft_leakage_risk_rejects_candidate():
    validation = normalized(raw_result("probable_gain", risk_tags=["overclarified_prompt"]))
    weak_probe = {
        "enabled": True,
        "mode": "light",
        "probe_answer": "弱模型直接答中了关键点。",
        "probe_failure_detected": False,
        "target_failure_match": False,
        "probe_judgment": "weak probe did not expose target failure",
    }

    result = apply_weak_probe_result(validation, weak_probe)

    assert result["passed"] is False
    assert result["recommended_action"] == "reject_candidate"
    assert result["difficulty_gain_label"] == "leakage_or_simplification"
    assert "weak_probe_no_failure" in result["risk_tags"]


def test_rule_only_validation_rejects_obvious_missing_premise_leakage():
    candidate = make_candidate(
        "dg-rule",
        "cand_1",
        "为什么缺少排他性证据导致不能定案？",
    )

    validated = validate_records_rule_only([candidate])
    result = validated[0]["difficulty_gain_validation"]

    assert result["passed"] is False
    assert result["difficulty_gain_label"] == "leakage_or_simplification"
    assert "missing_premise_named" in result["risk_tags"]


def test_candidate_selection_filters_failed_difficulty_gain_candidate():
    failed = make_candidate(
        "dg-select",
        "cand_1",
        "为什么缺少排他性证据导致不能定案？",
        normalized(raw_result("clear_gain", risk_tags=["missing_premise_named"])),
    )
    passed = make_candidate(
        "dg-select",
        "cand_2",
        "请比较 A 与 B 两个候选补充事实，判断哪一个才是支撑结论的最小关键事实，并说明另一个为什么不足。",
        normalized(raw_result("probable_gain")),
    )

    selected, invalid_cases = select_candidates([failed, passed])

    assert selected[0]["candidate_selection"]["selected"] is True
    assert selected[0]["candidate_selection"]["selected_candidate_id"] == "dg-select::cand_2"
    assert selected[0]["candidate_selection"]["selection_status"] == "selected_after_difficulty_gain_validation"
    assert invalid_cases[0]["invalid_type"] == "leakage_or_simplification"


def test_candidate_selection_marks_group_when_no_candidate_passes_difficulty_gain():
    failed_a = make_candidate(
        "dg-none",
        "cand_1",
        "为什么缺少排他性证据导致不能定案？",
        normalized(raw_result("clear_gain", risk_tags=["missing_premise_named"])),
    )
    failed_b = make_candidate(
        "dg-none",
        "cand_2",
        "请输出复杂编号表格。",
        normalized(
            raw_result(
                "probable_gain",
                scores={"format_complexity_score": 0.2},
                risk_tags=["format_difficulty_only"],
            )
        ),
    )

    selected, invalid_cases = select_candidates([failed_a, failed_b])
    selection = selected[0]["candidate_selection"]

    assert selection["selected"] is False
    assert selection["selection_status"] == "no_candidate_passed_difficulty_gain"
    assert selection["recommended_next_action"] == "retry_with_backup_operator"
    assert len(invalid_cases) == 2


def test_missing_difficulty_gain_is_rejected_unless_legacy_flag_is_enabled():
    missing = make_candidate(
        "dg-legacy",
        "cand_1",
        "请比较 A 与 B 两个候选事实。",
    )

    selected, _ = select_candidates([missing])
    legacy_selected, _ = select_candidates([missing], allow_missing_difficulty_gain=True)

    assert selected[0]["candidate_selection"]["selected"] is False
    assert selected[0]["candidate_selection"]["selection_status"] == "no_candidate_passed_difficulty_gain"
    assert legacy_selected[0]["candidate_selection"]["selected"] is True
    assert legacy_selected[0]["candidate_selection"]["selection_status"] == "selected_legacy_without_difficulty_gain"


def test_generate_report_counts_labels_risks_and_operator_pass_rate():
    passed = make_candidate("dg-report", "cand_1", "请比较 A 与 B。", normalized(raw_result("clear_gain")))
    failed = make_candidate(
        "dg-report",
        "cand_2",
        "为什么缺少排他性证据导致不能定案？",
        normalized(raw_result("clear_gain", risk_tags=["missing_premise_named"])),
    )

    report = generate_report([passed, failed])

    assert report["total_candidates"] == 2
    assert report["passed_count"] == 1
    assert report["risk_tag_distribution"]["missing_premise_named"] == 1
    assert report["operator_pass_rate"]["O1_gap_choice"]["pass_rate"] == 0.5
    assert report["operator_pass_rate"]["O1_gap_choice"]["main_failure_reasons"]["missing_premise_named"] == 1


if __name__ == "__main__":
    test_compute_difficulty_gain_score_uses_configured_weights()
    test_clear_gain_passes_with_competitive_judgment()
    test_high_risk_leakage_tags_reject_candidate()
    test_conclusion_hint_and_answer_path_are_hard_rejects()
    test_format_axis_and_external_failures_get_labels()
    test_low_competitive_judgment_is_rejected_as_no_gain()
    test_weak_probe_miss_lowers_priority_without_soft_risk()
    test_weak_probe_miss_with_soft_leakage_risk_rejects_candidate()
    test_rule_only_validation_rejects_obvious_missing_premise_leakage()
    test_candidate_selection_filters_failed_difficulty_gain_candidate()
    test_candidate_selection_marks_group_when_no_candidate_passes_difficulty_gain()
    test_missing_difficulty_gain_is_rejected_unless_legacy_flag_is_enabled()
    test_generate_report_counts_labels_risks_and_operator_pass_rate()
    print("difficulty gain validation checks passed")
