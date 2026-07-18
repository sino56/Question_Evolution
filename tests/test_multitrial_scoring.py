import asyncio
import gzip
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scoring import EVALUATION_PROTOCOL, ScoringProcessor  # noqa: E402
from round0_stability_probe import process_item_with_stability_probe  # noqa: E402


def response(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class FakeAnswerClient:
    def __init__(self):
        self.calls = 0

    async def generate_answer(self, question):
        self.calls += 1
        call_index = self.calls
        await asyncio.sleep(0)
        return f"answer-{call_index}"


class FakeJudgeClient:
    def __init__(self, scores=None, fail=False):
        self.scores = scores or {}
        self.fail = fail
        self.prompts = []
        self.calls = 0

    async def chat_completions_create(self, **kwargs):
        self.calls += 1
        prompt = kwargs["messages"][0]["content"]
        self.prompts.append(prompt)
        await asyncio.sleep(0)
        if self.fail:
            raise RuntimeError("judge unavailable")
        answer = next((name for name in self.scores if name in prompt), "")
        awarded = self.scores.get(answer, 5)
        return response(json.dumps({
            "item_scores": [
                {"title": "核心", "awarded": awarded, "brief_reason": f"score={awarded}"}
            ],
            "overall_comment": f"overall={awarded}",
        }, ensure_ascii=False))


def sample(index=1, evolved=True):
    return {
        "sample_id": f"sample-{index}",
        "index": index,
        "prompt": f"question-{index}",
        "question_evolved": evolved,
        "rubric": [{"title": "核心", "weight": 10}],
        "score_prompt": "题目评分材料\n回答：<<<待评答案>>>",
    }


def processor(*, gpt_fail=False, qwen_fail=False, qwen_limit=20, gpt_limit=20, qwen_scores=None):
    answer_client = FakeAnswerClient()
    qwen_client = FakeJudgeClient(
        qwen_scores or {"answer-1": 2, "answer-2": 6, "answer-3": 10},
        fail=qwen_fail,
    )
    gpt_client = FakeJudgeClient(
        {"answer-1": 9, "answer-2": 9, "answer-3": 9},
        fail=gpt_fail,
    )
    instance = ScoringProcessor(
        judge_client=qwen_client,
        judge_model="qwen-judge",
        answer_mode="llm",
        max_concurrent=10,
        max_retries=0,
        answer_client=answer_client,
        answer_model_name="qwen-answer",
        force_generate_answer=True,
        gpt_judge_client=gpt_client,
        gpt_judge_model="gpt-judge",
        answer_trials=3,
        qwen_judge_repeats=2,
        gpt_judge_repeats=2,
        qwen_max_concurrent=qwen_limit,
        gpt_max_concurrent=gpt_limit,
    )
    return instance, answer_client, qwen_client, gpt_client


def test_qwen_aggregate_drives_online_score_and_representative_trial():
    instance, answer_client, qwen_client, gpt_client = processor()
    result = asyncio.run(instance.process_item(sample()))

    assert answer_client.calls == 3
    assert qwen_client.calls == 6
    assert gpt_client.calls == 6
    assert result["evaluation_protocol"] == EVALUATION_PROTOCOL
    assert result["score_rate"] == 0.6
    assert result["qwen_score_summary"]["score_mean"] == 0.6
    assert result["gpt_score_summary"]["score_mean"] == 0.9
    assert result["representative_trial_index"] == 2
    assert result["scoring_result"]["candidate_answer"] == "answer-2"
    assert result["scoring_result"]["item_scores"][0]["awarded"] == 6
    assert result["scoring_result"]["total_awarded"] == 6
    assert "judge_raw_response" not in result["scoring_result"]
    assert len(result["scoring_result"]["answer_trials"]) == 3
    assert [row["repeat_index"] for row in result["scoring_result"]["answer_trials"][0]["qwen_judge_results"]] == [1, 2]
    assert qwen_client.prompts == gpt_client.prompts


def test_gpt_failures_are_recorded_without_blocking_qwen_decision():
    instance, _, _, _ = processor(gpt_fail=True)
    result = asyncio.run(instance.process_item(sample()))

    assert result["score_rate"] == 0.6
    assert result["gpt_score_summary"] == {
        "requested_count": 6,
        "successful_count": 0,
        "failed_count": 6,
        "score_count": 0,
        "score_mean": None,
        "score_min": None,
        "score_max": None,
        "experimental": True,
    }
    gpt_results = result["scoring_result"]["answer_trials"][0]["gpt_judge_results"]
    assert all("error" in row for row in gpt_results)


def test_required_qwen_failure_fails_the_sample():
    instance, _, _, _ = processor(qwen_fail=True)

    try:
        asyncio.run(instance.process_item(sample()))
    except RuntimeError as exc:
        assert "judge unavailable" in str(exc)
    else:
        raise AssertionError("missing mandatory Qwen score should fail")


def test_pass_through_reuses_existing_scoring_without_network_calls():
    instance, answer_client, qwen_client, gpt_client = processor()
    item = sample(evolved=False)
    item["scoring_result"] = {
        "candidate_answer": "old",
        "total_awarded": 8,
        "total_possible": 10,
    }
    result = asyncio.run(instance.process_item(item))

    assert result["score_rate"] == 0.8
    assert answer_client.calls == qwen_client.calls == gpt_client.calls == 0


def test_trace_sidecar_and_manifest_preserve_raw_responses(tmp_path):
    instance, _, _, _ = processor()
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "scored.jsonl"
    input_path.write_text(json.dumps(sample(), ensure_ascii=False) + "\n", encoding="utf-8")

    asyncio.run(instance.process_file(str(input_path), str(output_path)))

    scored = json.loads(output_path.read_text(encoding="utf-8"))
    assert '"judge_raw_response":' not in json.dumps(scored, ensure_ascii=False)
    sidecar_path = Path(str(output_path) + ".judge_traces.jsonl.gz")
    manifest_path = Path(str(output_path) + ".manifest.json")
    with gzip.open(sidecar_path, "rt", encoding="utf-8") as trace_file:
        traces = [json.loads(line) for line in trace_file if line.strip()]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(traces) == 12
    assert all(row["raw_response"] for row in traces)
    assert all(row["raw_text"] == row["raw_response"] for row in traces)
    assert all(row["record_key"] == "sample-1|||question-1" for row in traces)
    assert all(row["stage"] == "scoring" for row in traces)
    assert all(row["trace_kind"] == "judge_model_response" for row in traces)
    assert all(row["encoding"] == "utf-8" for row in traces)
    assert all(
        row["content_sha256"]
        == hashlib.sha256(row["raw_text"].encode("utf-8")).hexdigest()
        for row in traces
    )
    assert manifest["judge_trace_sidecar"]["record_count"] == 12
    assert len(manifest["judge_trace_sidecar"]["sha256"]) == 64


def test_scoring_preserves_distinct_sample_ids_with_same_prompt(tmp_path):
    instance, _, _, _ = processor()
    first = sample(index=1)
    second = sample(index=1)
    first["sample_id"] = "sample-a"
    second["sample_id"] = "sample-b"
    input_path = tmp_path / "same-prompt.jsonl"
    output_path = tmp_path / "same-prompt-scored.jsonl"
    input_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in (first, second)) + "\n",
        encoding="utf-8",
    )

    asyncio.run(instance.process_file(str(input_path), str(output_path)))

    rows = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert {row["sample_id"] for row in rows} == {"sample-a", "sample-b"}


def test_qwen_and_gpt_request_pools_have_independent_limits():
    instance, _, _, _ = processor(qwen_limit=2, gpt_limit=1)

    async def run_all():
        await asyncio.gather(*(instance.process_item(sample(index)) for index in range(1, 4)))

    asyncio.run(run_all())
    assert instance.qwen_request_pool.peak_active <= 2
    assert instance.gpt_request_pool.peak_active <= 1
    assert instance.qwen_request_pool.peak_active == 2
    assert instance.gpt_request_pool.peak_active == 1


def test_round0_uses_all_qwen_repeats_for_online_score():
    instance, _, _, _ = processor(qwen_scores={"answer-1": 2, "answer-2": 4, "answer-3": 10})
    config = SimpleNamespace(
        initial_trials=3,
        extra_trials=0,
        max_trials=3,
        score_threshold=0.8,
        strong_high_threshold=0.85,
        borderline_low=0.70,
        edge_low=0.72,
        edge_high=0.83,
        answer_model="qwen-answer",
        answer_mode="llm",
        answer_temperature=0.7,
        answer_top_p=0.95,
        answer_seed_base=100,
        judge_model="qwen-judge",
        judge_temperature=0.0,
        qwen_judge_repeats=2,
        gpt_judge_model="gpt-judge",
        gpt_judge_temperature=0.0,
        gpt_judge_repeats=2,
        cache_dir=None,
        force=True,
    )

    result = asyncio.run(process_item_with_stability_probe(sample(), instance, config))

    assert result["round0_score_summary"]["stable_score"] == 0.4
    assert result["score_rate"] == 16 / 30
    assert result["qwen_score_summary"]["successful_count"] == 6
    assert result["gpt_score_summary"]["successful_count"] == 6
    assert result["representative_round0_answer"]["trial_id"] == 2
    assert result["representative_round0_answer"]["selection_reason"] == "closest_to_qwen_overall_mean"
