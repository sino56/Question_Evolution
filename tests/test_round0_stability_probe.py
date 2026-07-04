import sys
import argparse
import asyncio
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from round0_stability_probe import (  # noqa: E402
    BORDERLINE_PROBE,
    REVIEW_NEEDED,
    STABLE_HIGH,
    STABLE_LOW,
    UNSTABLE_HIGH,
    UNCERTAIN_LOW,
    build_stability_report,
    compute_score_summary,
    needs_extra_trials,
    process_item_with_stability_probe,
)
from select_evolution_candidates import (  # noqa: E402
    EVOLVE_HIGH_SCORE_OVERSCORE,
    PASS_THROUGH_OR_SCORING_NOISE,
    RECONSTRUCT_LOW_SCORE_BOUNDARY,
    STOP_EVOLUTION,
    process_records,
)
from question_evolution import QuestionEvolutionProcessor  # noqa: E402
from question_evolution import should_evolve  # noqa: E402


def trials(scores):
    return [
        {
            "trial_id": index + 1,
            "score_rate": score,
            "candidate_answer": f"answer {index}",
            "scoring_result": {
                "total_awarded": score * 10,
                "total_possible": 10,
                "item_scores": [{"title": "核心", "weight": 10, "awarded": round(score * 10)}],
            },
        }
        for index, score in enumerate(scores)
    ]


def profiled_item(summary, worth=True):
    return {
        "sample_id": "sample",
        "prompt": "题目",
        "score_rate": 0.4,
        "round0_score_summary": summary,
        "sample_profile": {
            "core_capability": "证据链补强",
            "claim_level": "可疑线索",
            "problem_shape": "候选项区分",
        },
        "overscore_diagnosis": {
            "is_worth_evolving": worth,
            "candidate_overscore_cause": "漏最小关键事实",
            "target_failure_mode": "选错最关键缺口",
            "why_high_score_is_suspicious": "存在高分覆盖能力。",
        },
    }


def test_stable_high_does_not_need_extra_trials():
    summary = compute_score_summary(trials([0.82, 0.86, 0.84]))

    assert summary["stable_score"] == 0.84
    assert summary["admission_status"] == STABLE_HIGH
    assert needs_extra_trials(summary)[0] is False


def test_low_score_outlier_triggers_extra_trials():
    summary = compute_score_summary(trials([0.91, 0.86, 0.73]))
    extra, reasons = needs_extra_trials(summary)

    assert extra is True
    assert "score_range_large" in reasons


def test_volatile_pressable_sample_is_unstable_high():
    summary = compute_score_summary(trials([0.77, 0.91, 0.74, 0.86, 0.79]))

    assert summary["volatility_level"] in {"medium", "high"}
    assert summary["admission_status"] == UNSTABLE_HIGH


def test_stable_low_is_not_admitted():
    summary = compute_score_summary(trials([0.55, 0.59, 0.61]))

    assert summary["admission_status"] == STABLE_LOW
    assert summary["recommended_evolution_budget"] == 0


def test_borderline_probe_uses_low_budget():
    summary = compute_score_summary(trials([0.71, 0.78, 0.82, 0.76, 0.74]))

    assert summary["admission_status"] == BORDERLINE_PROBE
    assert summary["recommended_evolution_budget"] == 1


def test_insufficient_trials_requires_review():
    summary = compute_score_summary(trials([0.90, 0.70]))

    assert summary["volatility_level"] == "insufficient_trials"
    assert summary["admission_status"] == REVIEW_NEEDED
    assert summary["recommended_evolution_budget"] == 0
    assert summary["needs_manual_review"] is True


def test_single_high_score_low_median_is_uncertain_low():
    summary = compute_score_summary(trials([0.62, 0.68, 0.81, 0.66, 0.64]))

    assert summary["admission_status"] == UNCERTAIN_LOW
    assert summary["recommended_evolution_budget"] == 0
    assert summary["needs_manual_review"] is True


def test_selector_prefers_round0_summary_over_legacy_score_rate():
    summary = compute_score_summary(trials([0.77, 0.91, 0.74, 0.86, 0.79]))
    selected = process_records([profiled_item(summary)], high_score_threshold=0.8)

    assert selected[0]["evolution_action"] == EVOLVE_HIGH_SCORE_OVERSCORE
    assert "round0 admission_status=unstable_high" in selected[0]["evolution_action_reason"]
    assert selected[0]["evolution_budget"]["recommended_num_candidates"] == 2


def test_selector_maps_round0_borderline_to_boundary_probe():
    summary = compute_score_summary(trials([0.71, 0.78, 0.82, 0.76, 0.74]))
    selected = process_records([profiled_item(summary)], high_score_threshold=0.8)

    assert selected[0]["evolution_action"] == RECONSTRUCT_LOW_SCORE_BOUNDARY
    assert selected[0]["evolution_budget"]["recommended_num_candidates"] == 1


def test_selector_stops_review_needed():
    summary = compute_score_summary(trials([0.90, 0.70]))
    selected = process_records([profiled_item(summary)], high_score_threshold=0.8)

    assert selected[0]["evolution_action"] == STOP_EVOLUTION


def test_selector_keeps_legacy_fallback_without_round0_summary():
    item = profiled_item({}, worth=True)
    item.pop("round0_score_summary")
    item["score_rate"] = 0.4
    selected = process_records([item], high_score_threshold=0.8)

    assert selected[0]["evolution_action"] == PASS_THROUGH_OR_SCORING_NOISE


def test_question_evolution_honors_round0_low_candidate_budget():
    processor = QuestionEvolutionProcessor(
        client=object(),
        model="mock",
        num_candidates=3,
        max_candidate_budget=0,
    )
    item = profiled_item(
        {
            "admission_status": "borderline_probe",
            "recommended_evolution_budget": 1,
            "stable_score": 0.76,
            "admission_score": 0.78,
        },
        worth=True,
    )
    item["evolution_action"] = EVOLVE_HIGH_SCORE_OVERSCORE
    item["operator_route"] = {
        "is_high_value_sample": True,
        "should_use_local_tree_search": True,
    }

    assert processor.recommended_candidate_count(item) == 1


def test_question_evolution_reads_evolution_budget_field():
    processor = QuestionEvolutionProcessor(
        client=object(),
        model="mock",
        num_candidates=3,
        max_candidate_budget=0,
    )
    item = profiled_item({}, worth=True)
    item.pop("round0_score_summary")
    item["evolution_action"] = EVOLVE_HIGH_SCORE_OVERSCORE
    item["operator_route"] = {
        "is_high_value_sample": True,
        "should_use_local_tree_search": True,
    }
    item["evolution_budget"] = {
        "recommended_num_candidates": 1,
        "source": "round0_score_summary",
        "admission_status": "borderline_probe",
    }

    assert processor.recommended_candidate_count(item) == 1


def test_question_evolution_zero_budget_overrides_evolve_action():
    item = profiled_item(
        {
            "admission_status": "stable_low",
            "recommended_evolution_budget": 0,
            "stable_score": 0.60,
        },
        worth=True,
    )
    item["evolution_action"] = EVOLVE_HIGH_SCORE_OVERSCORE

    assert should_evolve(item, 0.8) is False


class DummyProcessor:
    def __init__(self, scores):
        self.semaphore = asyncio.Semaphore(10)
        self.scores = list(scores)
        self.generated = 0

    async def generate_candidate_answer_with_retry(self, item):
        self.generated += 1
        return f"answer {self.generated}"

    async def score_candidate_answer(self, item, candidate_answer):
        index = int(candidate_answer.split()[-1]) - 1
        score = self.scores[index]
        return {
            "candidate_answer": candidate_answer,
            "total_awarded": score * 100,
            "total_possible": 100,
            "item_scores": [{"title": "核心", "weight": 100, "awarded": round(score * 100)}],
        }


def config(**overrides):
    values = {
        "initial_trials": 3,
        "extra_trials": 0,
        "max_trials": 3,
        "score_threshold": 0.8,
        "strong_high_threshold": 0.85,
        "borderline_low": 0.70,
        "edge_low": 0.72,
        "edge_high": 0.83,
        "answer_model": "qwen-test",
        "answer_mode": "llm",
        "answer_temperature": 0.7,
        "answer_top_p": 0.95,
        "answer_seed_base": 20260704,
        "judge_model": "judge-test",
        "judge_temperature": 0.0,
        "cache_dir": None,
        "force": True,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_process_item_rewrites_top_level_to_representative_trial():
    item = {
        "sample_id": "sample",
        "prompt": "题目",
        "score_prompt": "{{answer}}",
        "rubric": [{"title": "核心", "weight": 100}],
        "score_rate": 0.1,
        "scoring_result": {"candidate_answer": "old answer", "total_awarded": 10, "total_possible": 100},
    }
    result = asyncio.run(
        process_item_with_stability_probe(
            item,
            DummyProcessor([0.72, 0.82, 0.91]),
            config(),
        )
    )

    assert result["score_rate"] == 0.82
    assert result["candidate_answer"] == "answer 2"
    assert result["scoring_result"]["candidate_answer"] == "answer 2"
    assert result["representative_round0_answer"]["trial_id"] == 2
    assert result["representative_round0_answer"]["candidate_answer"] == "answer 2"
    assert result["round0_score_summary"]["representative_trial_id"] == 2
    assert result["rubric_item_stability"] == result["round0_score_summary"]["rubric_item_stability"]
    assert result["meta_info"]["pre_stability_score_rate"] == 0.1
    assert result["round0_score_trials"][0]["force_generate_answer"] is True
    assert result["round0_score_trials"][0]["answer_seed"] == 20260705
    assert result["round0_score_trials"][0]["judge_temperature"] == 0.0


def test_stability_report_contains_cost_and_shift_metrics():
    item = profiled_item(compute_score_summary(trials([0.82, 0.86, 0.84])))
    item["meta_info"] = {"pre_stability_score_rate": 0.70}
    report = build_stability_report([item], 0.8)

    assert report["total_samples"] == 1
    assert report["average_trial_count"] == 3
    assert report["estimated_cost_per_100_samples"]["answer_calls"] == 300
    assert report["classification_distribution"]["stable_high"] == 1
    assert report["legacy_vs_stable_admission"]["rescued_by_stability"] == 1


if __name__ == "__main__":
    test_stable_high_does_not_need_extra_trials()
    test_low_score_outlier_triggers_extra_trials()
    test_volatile_pressable_sample_is_unstable_high()
    test_stable_low_is_not_admitted()
    test_borderline_probe_uses_low_budget()
    test_insufficient_trials_requires_review()
    test_single_high_score_low_median_is_uncertain_low()
    test_selector_prefers_round0_summary_over_legacy_score_rate()
    test_selector_maps_round0_borderline_to_boundary_probe()
    test_selector_stops_review_needed()
    test_selector_keeps_legacy_fallback_without_round0_summary()
    test_question_evolution_honors_round0_low_candidate_budget()
    test_question_evolution_reads_evolution_budget_field()
    test_question_evolution_zero_budget_overrides_evolve_action()
    test_process_item_rewrites_top_level_to_representative_trial()
    test_stability_report_contains_cost_and_shift_metrics()
    print("round0 stability probe checks passed")
