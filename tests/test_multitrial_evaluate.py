import asyncio
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from multitrial_evaluate import (  # noqa: E402
    DEFAULT_INPUT,
    allocate_experiment_dir,
    evaluate_item,
    find_resumable_output,
    summarize_trials,
)


class DummyProcessor:
    def __init__(self, scores):
        self.scores = scores
        self.answer_calls = 0
        self.score_calls = 0

    async def generate_candidate_answer_with_retry(self, item):
        self.answer_calls += 1
        return f"answer-{self.answer_calls}"

    async def score_candidate_answer(self, item, answer, trial_index, **kwargs):
        self.score_calls += 1
        score = self.scores[trial_index - 1]
        return {"candidate_answer": answer, "total_awarded": score * 100, "total_possible": 100}


def item(evolved=True):
    return {
        "sample_id": "sample-1",
        "prompt": "题目",
        "question_evolved": evolved,
        "rubric": [{"title": "核心", "weight": 100}],
        "score_prompt": "评分 <<<待评答案>>",
    }


def test_default_input_is_the_requested_four_scenario_dataset():
    assert Path(DEFAULT_INPUT) == Path("data") / "四大场景测试样本.jsonl"


def test_experiment_directory_uses_exp_then_incrementing_suffixes(tmp_path):
    assert Path(allocate_experiment_dir(str(tmp_path), "2026-08-01")) == tmp_path / "2026-08-01" / "exp"
    assert Path(allocate_experiment_dir(str(tmp_path), "2026-08-01")) == tmp_path / "2026-08-01" / "exp1"


def test_latest_incomplete_experiment_is_selected_for_resume(tmp_path):
    day = tmp_path / "2026-08-01"
    output = day / "exp1" / "四大场景测试样本_multitrial_scored.jsonl"
    output.parent.mkdir(parents=True)
    Path(str(output) + ".partial").write_text("{}\n", encoding="utf-8")
    Path(str(output) + ".checkpoint.jsonl").write_text("{}\n", encoding="utf-8")

    resumed = find_resumable_output(
        str(Path("data") / "四大场景测试样本.jsonl"),
        str(tmp_path),
        "2026-08-01",
    )

    assert Path(resumed) == output


def test_evaluate_item_keeps_trials_and_uses_median_projection():
    processor = DummyProcessor([0.2, 0.8, 0.6])
    result = asyncio.run(evaluate_item(item(), processor, trials=3, configuration={"answer_model": "weak"}))

    assert processor.answer_calls == processor.score_calls == 3
    assert result["score_rate"] == 0.6
    assert result["candidate_answer"] == "answer-3"
    assert len(result["multi_trial_evaluation"]["trials"]) == 3


def test_pass_through_item_is_evaluated_again():
    processor = DummyProcessor([0.4, 0.6])
    source = item(evolved=False)
    source["candidate_answer"] = "old answer"
    result = asyncio.run(evaluate_item(source, processor, trials=2, configuration={}))

    assert result["question_evolved"] is False
    assert processor.answer_calls == 2
    assert result["candidate_answer"] != "old answer"


def test_summary_rejects_unscored_trial():
    with pytest.raises(ValueError, match="未完成评分"):
        summarize_trials([{"trial_id": 1, "scoring_result": {}}])
