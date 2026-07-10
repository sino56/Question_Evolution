import asyncio
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gen_rubric
from collect_answers import AnswerCollector
from question_evolution import QuestionEvolutionProcessor
from scoring import ScoringProcessor


def write_jsonl(path: Path, records):
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def read_jsonl(path: Path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_question_evolution_stage_raises_when_any_record_fails(tmp_path):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "candidates.jsonl"
    write_jsonl(input_path, [{"index": 1, "prompt": "question", "score_rate": 1.0}])
    processor = QuestionEvolutionProcessor(object(), model="mock", max_concurrent=1)

    async def fail_item(item):
        raise RuntimeError("mock evolution failure")

    processor.process_item = fail_item

    with pytest.raises(RuntimeError, match="question evolution 阶段有 1/1 条记录失败"):
        asyncio.run(processor.process_file(str(input_path), str(output_path)))

    failed = read_jsonl(Path(str(output_path) + ".failed"))
    assert failed[0]["question_evolution_error"] == "mock evolution failure"


def test_scoring_stage_raises_when_any_record_fails(tmp_path):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "scored.jsonl"
    write_jsonl(input_path, [{"index": 1, "prompt": "question"}])
    processor = ScoringProcessor(object(), "mock", "llm", max_concurrent=1)

    async def fail_item(item):
        raise RuntimeError("mock scoring failure")

    processor.process_item = fail_item

    with pytest.raises(RuntimeError, match="scoring 阶段有 1/1 条记录失败"):
        asyncio.run(processor.process_file(str(input_path), str(output_path)))

    failed = read_jsonl(Path(str(output_path) + ".failed"))
    assert failed[0]["scoring_error"] == "mock scoring failure"


def test_answer_collection_stage_raises_when_any_record_fails(tmp_path):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "with_answers.jsonl"
    write_jsonl(input_path, [{"index": 1, "prompt": "question", "question_evolved": True}])

    async def run_failure():
        collector = object.__new__(AnswerCollector)
        collector.write_lock = asyncio.Lock()
        collector.load_processed_prompts = lambda _: set()

        async def fail_item(item, num_samples):
            raise RuntimeError("mock answer failure")

        collector.process_item = fail_item
        await collector.process_file(str(input_path), str(output_path), 1, 1)

    with pytest.raises(RuntimeError, match="answer collection 阶段有 1/1 条记录失败"):
        asyncio.run(run_failure())

    failed = read_jsonl(Path(str(output_path) + ".failed"))
    assert failed[0]["answer_collection_error"] == "mock answer failure"


class FakeRubricClient:
    def __init__(self, **kwargs):
        pass

    async def close(self):
        pass


def test_rubric_stage_raises_when_any_record_fails(tmp_path, monkeypatch):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "rubric.jsonl"
    write_jsonl(input_path, [{"sample_id": "failed", "prompt": "question"}])
    monkeypatch.setattr(gen_rubric, "RotatingAPIClient", FakeRubricClient)

    async def fail_item(item, client, model, writer_queue, failed_queue, progress_bar, prompt_version="v3"):
        failed = dict(item)
        failed["rubric_generation_error"] = "mock rubric failure"
        await failed_queue.put(failed)
        progress_bar.update(1)

    monkeypatch.setattr(gen_rubric, "process_item", fail_item)

    with pytest.raises(RuntimeError, match="rubric generation 阶段有 1/1 条记录失败"):
        asyncio.run(
            gen_rubric.main(
                str(input_path),
                str(output_path),
                concurrency=1,
                model="mock",
                api_keys=["dummy"],
            )
        )

    failed = read_jsonl(Path(str(output_path) + ".failed"))
    assert failed[0]["rubric_generation_error"] == "mock rubric failure"


def test_rubric_stage_preserves_distinct_samples_with_same_prompt(tmp_path, monkeypatch):
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "rubric.jsonl"
    write_jsonl(
        input_path,
        [
            {"sample_id": "a", "prompt": "same prompt"},
            {"sample_id": "b", "prompt": "same prompt"},
        ],
    )
    monkeypatch.setattr(gen_rubric, "RotatingAPIClient", FakeRubricClient)

    async def succeed_item(item, client, model, writer_queue, failed_queue, progress_bar, prompt_version="v3"):
        result = dict(item)
        result["rubric"] = [{"title": "criterion", "description": "description", "weight": 1}]
        result["score_prompt"] = "score prompt"
        await writer_queue.put(result)
        progress_bar.update(1)

    monkeypatch.setattr(gen_rubric, "process_item", succeed_item)
    asyncio.run(
        gen_rubric.main(
            str(input_path),
            str(output_path),
            concurrency=1,
            model="mock",
            api_keys=["dummy"],
        )
    )

    output = read_jsonl(output_path)
    assert len(output) == 2
    assert {record["sample_id"] for record in output} == {"a", "b"}
