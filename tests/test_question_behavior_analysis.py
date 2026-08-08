import asyncio
import json
import sys

import pytest

import question_behavior_analysis as behavior


def _judge(rate, items=None):
    return {
        "score_rate": rate,
        "item_scores": items or [
            {"title": "core", "awarded": round(rate * 4)},
            {"title": "penalty", "awarded": 0},
            {"title": "observe", "awarded": 0},
        ],
    }


def _trial(index, qwen_rates, *, gpt_rates=None, items=None):
    return {
        "trial_index": index,
        "candidate_answer": f"answer {index}",
        "qwen_judge_results": [_judge(rate, items) for rate in qwen_rates],
        "qwen_score_rate_mean": sum(qwen_rates) / len(qwen_rates),
        "gpt_judge_results": [] if gpt_rates is None else [_judge(rate, items) for rate in gpt_rates],
    }


def _item(trials, *, rubric=None):
    return {
        "sample_id": "sample-1",
        "prompt": "Which conclusion follows?",
        "reference_answer": "A supported conclusion.",
        "rubric": rubric or [
            {"title": "core", "weight": 4},
            {"title": "penalty", "weight": -2},
            {"title": "observe", "weight": 0},
        ],
        "decision_evaluation_status": "completed",
        "scoring_result": {"answer_trials": trials, "answer_model": "qwen", "judge_model": "qwen-judge"},
    }


def test_group_statistics_are_order_independent_and_include_repeat_range():
    source = _item([_trial(2, [0.2, 0.3]), _trial(1, [0.8, 0.9])])
    record = behavior.analyze_item(source)

    trials = record["group_statistics"]["trials"]
    assert [trial["trial_index"] for trial in trials] == [1, 2]
    assert trials[0]["qwen_score_min"] == 0.8
    assert trials[0]["qwen_score_max"] == 0.9
    assert trials[0]["qwen_score_range"] == pytest.approx(0.1)
    assert trials[0]["relative_advantage"] > 0
    assert behavior.validate_shadow_record(record) == (True, "ok")


def test_unstable_judge_blocks_observer_eligibility():
    record = behavior.analyze_item(_item([_trial(1, [0.1, 0.8]), _trial(2, [0.2, 0.3])]))

    assert "judge_unstable" in record["behavior_labels"]
    assert not record["qualification"]["observer_eligible"]
    assert record["observer_result"] == {}


def test_near_group_and_missing_gpt_do_not_create_dispute():
    record = behavior.analyze_item(_item([_trial(1, [0.5, 0.5]), _trial(2, [0.55, 0.55])]))

    assert "near_group" in record["behavior_labels"]
    assert "cross_judge_disputed" not in record["behavior_labels"]
    assert record["group_statistics"]["gpt_incomplete"] is True


def test_reversed_complete_gpt_order_is_cross_judge_disputed():
    record = behavior.analyze_item(_item([
        _trial(1, [0.9, 0.9], gpt_rates=[0.2, 0.2]),
        _trial(2, [0.2, 0.2], gpt_rates=[0.8, 0.8]),
    ]))

    assert "cross_judge_disputed" in record["behavior_labels"]
    assert not record["qualification"]["observer_eligible"]


def test_positive_penalty_and_zero_weight_statistics_are_separated():
    high_items = [
        {"title": "core", "awarded": 4}, {"title": "penalty", "awarded": 0}, {"title": "observe", "awarded": 0},
    ]
    low_items = [
        {"title": "core", "awarded": 0}, {"title": "penalty", "awarded": -2}, {"title": "observe", "awarded": 0},
    ]
    record = behavior.analyze_item(_item([_trial(1, [0.9, 0.9], items=high_items), _trial(2, [0.2, 0.2], items=low_items)]))

    differences = {row["title"]: row for row in record["group_statistics"]["item_differences"]}
    assert differences["core"]["kind"] == "positive"
    assert differences["penalty"]["kind"] == "penalty"
    assert "observe" not in differences
    assert record["qualification"]["observer_eligible"]


def test_unaligned_rubric_item_rejects_strong_diagnosis():
    bad_items = [{"title": "not-in-rubric", "awarded": 1}]
    record = behavior.analyze_item(_item([_trial(1, [0.9, 0.9], items=bad_items), _trial(2, [0.2, 0.2], items=bad_items)]))

    assert "rubric_or_question_risk" in record["behavior_labels"]
    assert not record["qualification"]["observer_eligible"]


def test_changed_question_or_rubric_makes_prior_analysis_stale():
    source = _item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])])
    record = behavior.analyze_item(source)
    changed = _item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])])
    changed["prompt"] = "A changed question version"

    assert behavior.analysis_is_stale(record, changed)


def test_diagnosis_cli_is_idempotent_and_never_rewrites_scored_input(tmp_path, monkeypatch):
    source = _item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])])
    input_path = tmp_path / "scored.jsonl"
    output_path = tmp_path / "behavior_analysis.jsonl"
    report_path = tmp_path / "behavior_analysis_report.json"
    original = json.dumps(source, ensure_ascii=False, sort_keys=True) + "\n"
    input_path.write_text(original, encoding="utf-8")
    argv = ["question_behavior_analysis.py", "diagnose", "--input", str(input_path), "--output", str(output_path), "--report-output", str(report_path)]

    monkeypatch.setattr(sys, "argv", argv)
    behavior.main()
    monkeypatch.setattr(sys, "argv", argv)
    behavior.main()

    assert input_path.read_text(encoding="utf-8") == original
    record = json.loads(output_path.read_text(encoding="utf-8"))
    assert record["analysis_status"] == "shadow"
    assert report_path.is_file()


def test_observer_validation_rejects_unlocatable_evidence():
    record = behavior.analyze_item(_item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])]))
    result = {
        "analysis_status": "completed", "confidence": "medium", "behavior_labels": [],
        "high_answer_strengths": [], "low_answer_failures": [], "candidate_mechanisms": [],
        "difference_summary": "unsupported", "question_or_rubric_risk": None,
        "rubric_evidence": [{"trial_index": 99, "rubric_title": "missing", "answer_fragment_id": "x"}],
    }

    assert behavior.validate_observer_result(result, record)[0] is False


def test_observer_failure_is_recorded_without_partial_conclusion(monkeypatch):
    record = behavior.analyze_item(_item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])]))
    source = _item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])])

    async def fail(*_args, **_kwargs):
        raise TimeoutError("observer timeout")

    monkeypatch.setattr(behavior, "call_observer", fail)
    observed = asyncio.run(behavior.observe_records(
        [record], {record["analysis_id"]: source}, model="fake", base_url="", api_key="", timeout=1, concurrency=1, prior={},
    ))[0]

    assert observed["observer_status"] == "failed"
    assert observed["observer_result"]["analysis_status"] == "failed"
    assert "candidate_mechanisms" not in observed["observer_result"]


def test_ineligible_record_never_calls_observer(monkeypatch):
    record = behavior.analyze_item(_item([_trial(1, [0.1, 0.8]), _trial(2, [0.2, 0.3])]))
    calls = []

    async def unexpected(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("observer must not be called")

    monkeypatch.setattr(behavior, "call_observer", unexpected)
    observed = asyncio.run(behavior.observe_records(
        [record], {}, model="fake", base_url="", api_key="", timeout=1, concurrency=1, prior={},
    ))[0]

    assert calls == []
    assert observed["observer_status"] == "skipped"


def test_batch_coverage_gate_skips_all_observer_calls(monkeypatch):
    record = behavior.analyze_item(_item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])]))
    unstable = behavior.analyze_item(_item([_trial(3, [0.1, 0.8]), _trial(4, [0.2, 0.3])]))
    calls = []

    async def unexpected(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("batch gate must run before observer calls")

    monkeypatch.setattr(behavior, "call_observer", unexpected)
    observed = asyncio.run(behavior.observe_records(
        [record, unstable], {}, model="fake", base_url="", api_key="", timeout=1, concurrency=1,
        prior={}, min_eligible_coverage=0.75,
    ))

    assert calls == []
    assert all(row["observer_result"]["reason"] == "eligible_coverage_below_threshold" for row in observed)


def test_observer_publishes_only_valid_evidence_bound_result(monkeypatch):
    record = behavior.analyze_item(_item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])]))
    source = _item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])])

    async def completed(*_args, **_kwargs):
        return {
            "analysis_status": "completed", "confidence": "medium", "behavior_labels": ["informative_answer_gap"],
            "difference_summary": "The high trial covered the core criterion.", "high_answer_strengths": [],
            "low_answer_failures": [], "candidate_mechanisms": [], "question_or_rubric_risk": None,
            "rubric_evidence": [{"trial_index": 1, "rubric_title": "core", "answer_fragment_id": "trial_1_full"}],
        }

    monkeypatch.setattr(behavior, "call_observer", completed)
    observed = asyncio.run(behavior.observe_records(
        [record], {record["analysis_id"]: source}, model="fake", base_url="", api_key="", timeout=1, concurrency=1, prior={},
    ))[0]

    assert observed["observer_status"] == "completed"
    assert observed["observer_result"]["confidence"] == "medium"
    assert observed["observer_call"]["status"] == "completed"


def test_observer_prompt_excludes_profile_routing_and_judge_prose():
    source = _item([_trial(1, [0.9, 0.9]), _trial(2, [0.2, 0.2])])
    source["sample_profile"] = {"secret_profile": "must not leak"}
    source["overscore_diagnosis"] = "must not leak"
    source["operator_route"] = {"operator_candidates": ["must not leak"]}
    source["scoring_result"]["answer_trials"][0]["qwen_judge_results"][0]["overall_comment"] = "must not leak"
    record = behavior.analyze_item(source)
    prompt = behavior.observer_prompt(record, source)

    assert "must not leak" not in prompt
