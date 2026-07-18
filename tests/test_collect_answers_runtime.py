import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collect_answers import AnswerCollector
from pipeline_runtime import FairRequestPool


class _Message:
    content = (
        "这是一个长度足够、内容正常且不会触发质量过滤的参考答案，"
        "用于验证受控并发和阶段产物发布，并补充必要的事实解释与结论依据。"
    )


class _Choice:
    message = _Message()


class TrackingClient:
    def __init__(self):
        self.active = 0
        self.peak = 0
        self.choices = [_Choice()]
        self.chat = self
        self.completions = self

    async def create(self, **kwargs):
        self.active += 1
        self.peak = max(self.peak, self.active)
        await asyncio.sleep(0.002)
        self.active -= 1
        return self


def make_collector(limit=2):
    collector = object.__new__(AnswerCollector)
    collector.api_keys = ["dummy"]
    collector.current_key_index = 0
    collector.model = "mock"
    collector.client = TrackingClient()
    collector.request_pool = FairRequestPool(limit, "reference_answer")
    collector.semaphore = asyncio.Semaphore(limit)
    collector.write_lock = asyncio.Lock()
    collector.key_lock = asyncio.Lock()
    collector.max_retries = 0
    collector.script_gt_guide = False
    collector.empty_retry_base_sleep = 0
    return collector


def test_answer_samples_share_global_request_pool_across_samples():
    async def scenario():
        collector = make_collector(limit=2)
        await asyncio.gather(
            collector.process_item({"sample_id": "a", "prompt": "问题甲"}, 3),
            collector.process_item({"sample_id": "b", "prompt": "问题乙"}, 3),
        )
        assert collector.client.peak == 2
        assert collector.request_pool.peak_active == 2

    asyncio.run(scenario())


def test_answer_file_stage_streams_and_publishes_manifest(tmp_path):
    input_path = tmp_path / "evolved.jsonl"
    output_path = tmp_path / "answers.jsonl"
    performance_path = tmp_path / "performance_events.jsonl"
    records = [
        {"sample_id": "a", "index": 1, "prompt": "问题甲", "question_evolved": True},
        {"sample_id": "b", "index": 2, "prompt": "问题乙", "question_evolved": True},
    ]
    input_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    collector = make_collector(limit=2)
    asyncio.run(
        collector.process_file(
            str(input_path),
            str(output_path),
            2,
            2,
            performance_path=str(performance_path),
        )
    )
    published = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    assert len(published) == 2
    assert all(len(item["meta_info"]["references"]) == 2 for item in published)
    manifest = json.loads(Path(str(output_path) + ".manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage"] == "collect_answers"
    event = json.loads(performance_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["request_pool_peaks"]["reference_answer"] <= 2


def test_incomplete_passthrough_is_failed_instead_of_silently_reused(tmp_path):
    input_path = tmp_path / "evolved.jsonl"
    output_path = tmp_path / "answers.jsonl"
    input_path.write_text(
        json.dumps({"sample_id": "a", "prompt": "问题", "question_evolved": False}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    collector = make_collector(limit=1)
    with pytest.raises(RuntimeError, match="answer collection 阶段有 1/1"):
        asyncio.run(collector.process_file(str(input_path), str(output_path), 1, 1))
    assert not output_path.exists()
    failed = json.loads(Path(str(output_path) + ".failed").read_text(encoding="utf-8").splitlines()[0])
    assert "reusable artifacts are incomplete" in failed["answer_collection_error"]
