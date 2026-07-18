import asyncio
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operator_router import MemoryMatchIndex, find_memory_matches, route_records
from prompts.operators import OPERATOR_SPECS
import question_evolution as question_evolution_module
import operator_router as operator_router_module
from pipeline_runtime import AtomicJsonlStageWriter
from question_evolution import QuestionEvolutionProcessor, get_item_key


def load_jsonl(path: Path):
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class FakeEvolutionClient:
    def __init__(self):
        self.calls = []

    async def chat_completions_create(self, **kwargs):
        self.calls.append(kwargs)
        content = json.dumps(
            {
                "evolved_prompt": (
                    "请在原题基础上判断两个候选依据中哪一个才真正决定结论能否成立，"
                    "并说明另一个依据为什么不能单独支撑结论。"
                ),
                "evolution_strategy": "使用指定 operator 生成单主轴问题。",
            },
            ensure_ascii=False,
        )
        return FakeResponse(content)


class FailingEvolutionClient:
    def __init__(self):
        self.calls = []

    async def chat_completions_create(self, **kwargs):
        self.calls.append(kwargs)
        raise RuntimeError("mock generation failed")


def test_operator_registry_covers_o10_to_o18():
    assert len(OPERATOR_SPECS) == 9
    for index in range(10, 19):
        assert any(operator_id.startswith(f"O{index}_") for operator_id in OPERATOR_SPECS)


def test_router_covers_representative_stage03_paths():
    records = load_jsonl(ROOT / "tests" / "fixtures" / "stage03_routing_input.jsonl")
    routed = route_records(records)
    routes = {record["sample_id"]: record["operator_route"] for record in routed}

    assert routes["stage03-o1"]["primary_operator"] == "O13_minimal_disqualifier"
    assert "O15_counterfactual_threshold_shift" in routes["stage03-o1"]["backup_operators"]

    assert routes["stage03-o2"]["primary_operator"] == "O15_counterfactual_threshold_shift"
    assert "O13_minimal_disqualifier" in routes["stage03-o2"]["avoid_operators"]

    assert routes["stage03-o4"]["primary_operator"] == "O10_evidence_sufficiency_ladder"
    assert routes["stage03-o8"]["primary_operator"] == "O17_action_vs_fact_threshold"
    assert routes["stage03-o9"]["primary_operator"] == "O10_evidence_sufficiency_ladder"

    assert routes["stage03-pass"]["primary_operator"] is None


def test_question_evolution_uses_route_and_skips_passthrough():
    records = load_jsonl(ROOT / "tests" / "fixtures" / "stage03_routing_input.jsonl")
    routed = route_records(records)
    by_id = {record["sample_id"]: record for record in routed}
    fake_client = FakeEvolutionClient()
    processor = QuestionEvolutionProcessor(
        fake_client,
        model="mock-evolution-model",
        max_concurrent=1,
        max_retries=0,
    )

    evolved = asyncio.run(processor.process_item(by_id["stage03-o1"]))
    metadata = evolved["meta_info"]["question_evolution_metadata"]
    assert evolved["question_evolved"] is True
    assert metadata["operator_used"] == "O13_minimal_disqualifier"
    assert metadata["ability_axis"] == "同一判断内的层级改变事实识别"
    assert metadata["expected_qwen_failure"] == "选错最关键缺口"
    assert metadata["expected_evaluation_focus"]
    assert len(fake_client.calls) == 1
    assert "O13_minimal_disqualifier" in fake_client.calls[0]["messages"][0]["content"]

    passed = asyncio.run(processor.process_item(by_id["stage03-pass"]))
    assert passed["question_evolved"] is False
    assert len(fake_client.calls) == 1


def test_question_evolution_file_stage_checkpoints_candidate_group_and_externalizes_trace(tmp_path):
    records = load_jsonl(ROOT / "tests" / "fixtures" / "stage03_routing_input.jsonl")
    item = route_records(records)[0]
    item["operator_route"] = dict(item["operator_route"])
    item["operator_route"]["is_high_value_sample"] = True
    input_path = tmp_path / "routed.jsonl"
    output_path = tmp_path / "candidates.jsonl"
    input_path.write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
    processor = QuestionEvolutionProcessor(
        FakeEvolutionClient(),
        model="mock-evolution-model",
        max_concurrent=2,
        max_retries=0,
        num_candidates=2,
    )
    asyncio.run(processor.process_file(str(input_path), str(output_path)))

    candidates = load_jsonl(output_path)
    assert len(candidates) == 2
    assert len({candidate["candidate_group_id"] for candidate in candidates}) == 1
    for candidate in candidates:
        metadata = candidate["meta_info"]["question_evolution_metadata"]
        assert "question_evolution_raw_response" not in metadata
        assert metadata["question_evolution_raw_response_trace_id"]
    assert Path(str(output_path) + ".evolution_traces.jsonl.gz").exists()
    manifest = json.loads(Path(str(output_path) + ".manifest.json").read_text(encoding="utf-8"))
    assert manifest["artifact"]["record_count"] == 2
    assert manifest["sidecars"][0]["record_count"] == 2


def test_question_evolution_failed_record_is_retried_instead_of_checkpointed(tmp_path):
    item = route_records(load_jsonl(ROOT / "tests" / "fixtures" / "stage03_routing_input.jsonl"))[0]
    input_path = tmp_path / "routed.jsonl"
    output_path = tmp_path / "candidates.jsonl"
    input_path.write_text(json.dumps(item, ensure_ascii=False) + "\n", encoding="utf-8")
    processor = QuestionEvolutionProcessor(
        FakeEvolutionClient(),
        model="mock-evolution-model",
        max_concurrent=1,
        max_retries=0,
    )

    calls = []

    async def fail_once(record):
        calls.append("failed")
        raise RuntimeError("isolated failure")

    processor.process_item = fail_once
    with pytest.raises(RuntimeError, match="question evolution 阶段有 1/1 条记录失败"):
        asyncio.run(processor.process_file(str(input_path), str(output_path)))

    assert not output_path.exists()
    checkpoint_path = Path(str(output_path) + ".checkpoint.jsonl")
    checkpoint_rows = [
        json.loads(line)
        for line in checkpoint_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert [row["type"] for row in checkpoint_rows] == ["header"]

    async def succeed(record):
        calls.append("succeeded")
        return {**record, "question_evolved": False, "question_evolution_status": "not_required"}

    processor.process_item = succeed
    asyncio.run(processor.process_file(str(input_path), str(output_path)))

    published = load_jsonl(output_path)
    assert calls == ["failed", "succeeded"]
    assert published[0]["question_evolution_status"] == "not_required"
    assert not Path(str(output_path) + ".failed").exists()


def test_question_evolution_resume_preserves_round_candidate_budget(tmp_path):
    items = [
        {"sample_id": "sample-a", "index": 1, "prompt": "same", "score_rate": 0.9},
        {"sample_id": "sample-b", "index": 1, "prompt": "same", "score_rate": 0.9},
    ]
    input_path = tmp_path / "routed.jsonl"
    output_path = tmp_path / "candidates.jsonl"
    input_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items),
        encoding="utf-8",
    )
    processor = QuestionEvolutionProcessor(
        FakeEvolutionClient(),
        model="mock-evolution-model",
        max_concurrent=1,
        max_retries=0,
        num_candidates=3,
        max_candidate_budget=3,
    )
    processor.recommended_candidate_count = lambda _item: 3
    config = {
        "model": processor.model,
        "min_score_rate": processor.min_score_rate,
        "num_candidates": processor.num_candidates,
        "max_candidate_budget": processor.max_candidate_budget,
        "validation_retries": processor.max_validation_retries,
    }
    writer = AtomicJsonlStageWriter(
        str(output_path),
        stage="question_evolution",
        input_path=str(input_path),
        config=config,
        code_paths=[
            question_evolution_module.__file__,
            question_evolution_module.validation_stage.__file__,
        ],
        flush_records=1,
    )
    writer.add_group(get_item_key(items[0]), [{**items[0], "question_evolved": False}])
    writer.close()

    requested = []

    async def generate(record, requested_candidates):
        requested.append((record["sample_id"], requested_candidates))
        return [{**record, "question_evolved": False}]

    processor.process_item_candidates = generate
    asyncio.run(processor.process_file(str(input_path), str(output_path)))

    assert requested == [("sample-b", 1)]
    assert {row["sample_id"] for row in load_jsonl(output_path)} == {"sample-a", "sample-b"}


def test_question_evolution_record_key_prefers_sample_id():
    first = {"sample_id": "sample-a", "index": 1, "prompt": "same"}
    second = {"sample_id": "sample-b", "index": 1, "prompt": "same"}
    assert get_item_key(first) != get_item_key(second)


def test_candidate_generation_falls_back_when_no_operator_available():
    records = load_jsonl(ROOT / "tests" / "fixtures" / "stage03_routing_input.jsonl")
    routed = route_records(records)
    by_id = {record["sample_id"]: record for record in routed}
    item = dict(by_id["stage03-o1"])
    route = dict(item["operator_route"])
    route["avoid_operators"] = [route["primary_operator"]] + list(route.get("backup_operators", []))
    route["backup_operators"] = []
    item["operator_route"] = route
    fake_client = FakeEvolutionClient()
    processor = QuestionEvolutionProcessor(
        fake_client,
        model="mock-evolution-model",
        max_concurrent=1,
        max_retries=0,
        num_candidates=2,
    )

    candidates = asyncio.run(processor.process_item_candidates(item, requested_candidates=2))
    fallback = candidates[0]

    assert len(candidates) == 1
    assert fallback["question_evolved"] is False
    assert fallback["question_evolution_status"] == "no_available_operator"
    assert fallback["candidate_generation"]["generation_status"] == "no_available_operator"
    assert fallback["candidate_id"].endswith("::no_available_operator")
    assert len(fake_client.calls) == 0


def test_candidate_generation_failure_returns_passthrough_candidate():
    records = load_jsonl(ROOT / "tests" / "fixtures" / "stage03_routing_input.jsonl")
    routed = route_records(records)
    by_id = {record["sample_id"]: record for record in routed}
    fake_client = FailingEvolutionClient()
    processor = QuestionEvolutionProcessor(
        fake_client,
        model="mock-evolution-model",
        max_concurrent=1,
        max_retries=0,
        num_candidates=2,
    )

    candidates = asyncio.run(processor.process_item_candidates(by_id["stage03-o1"], requested_candidates=2))
    fallback = candidates[0]

    assert len(candidates) == 1
    assert fallback["question_evolved"] is False
    assert fallback["question_evolution_status"] == "generation_failed_pass_through"
    assert fallback["candidate_generation"]["generation_status"] == "generation_failed_pass_through"
    assert fallback["candidate_group_id"] == "stage03-o1"
    assert len(fake_client.calls) >= 1

def test_state_recommendation_precedes_memory_and_failed_operator_is_avoided():
    item = {
        "sample_id": "route-priority",
        "prompt": "题目",
        "score_rate": 0.7,
        "evolution_action": "probe_middle_score_boundary",
        "sample_profile": {
            "core_capability": "边界判断",
            "claim_level": "可疑线索",
            "problem_shape": "候选项区分",
            "external_knowledge_risk": "low",
        },
        "overscore_diagnosis": {
            "is_worth_evolving": True,
            "candidate_overscore_cause": "处置触发与事实定性混淆",
            "target_failure_mode": "报告表述越界",
        },
        "evolution_state": {
            "previous_operator": "O13_minimal_disqualifier",
            "previous_effect_status": "score_increased",
            "recommended_next_methods": ["O18_baseline_scope_mismatch"],
            "stop_status": "rollback_and_reroute",
        },
    }
    signature = {
        "core_capability": "边界判断",
        "claim_level": "可疑线索",
        "problem_shape": "候选项区分",
        "candidate_overscore_cause": "处置触发与事实定性混淆",
    }
    operator_memory = [{
        "sample_signature": signature,
        "operator_used": "O16_close_alternative_normalization",
    }]

    route = route_records([item], operator_memory=operator_memory)[0]["operator_route"]
    assert route["primary_operator"] == "O18_baseline_scope_mismatch"
    assert "O13_minimal_disqualifier" in route["avoid_operators"]
    assert "O16_close_alternative_normalization" in route["backup_operators"]


def test_memory_index_preserves_linear_match_order_and_caches_signature():
    signature = {
        "core_capability": "边界判断",
        "claim_level": "可疑线索",
        "problem_shape": "候选项区分",
        "candidate_overscore_cause": "处置触发混淆",
    }
    records = [
        {"sample_signature": dict(signature), "operator_used": "O17_action_vs_fact_threshold"},
        {
            "sample_signature": {**signature, "problem_shape": "事实承接"},
            "operator_used": "O10_evidence_sufficiency_ladder",
        },
        {
            "sample_signature": {field: "不匹配" for field in signature},
            "operator_used": "O18_baseline_scope_mismatch",
        },
    ]
    expected = find_memory_matches(signature, records)
    index = MemoryMatchIndex(records)
    assert find_memory_matches(signature, records, index=index) == expected
    assert find_memory_matches(signature, records, index=index) == expected
    assert index.cache_hits == 1


def test_operator_router_performance_event_covers_parse_compute_and_windows_rss(tmp_path, monkeypatch):
    input_path = ROOT / "tests" / "fixtures" / "stage03_routing_input.jsonl"
    output_path = tmp_path / "routed.jsonl"
    performance_path = tmp_path / "performance_events.jsonl"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "operator_router.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--memory-dir",
            str(tmp_path / "memory"),
            "--performance-events",
            str(performance_path),
        ],
    )

    operator_router_module.main()

    event = json.loads(performance_path.read_text(encoding="utf-8").splitlines()[-1])
    assert event["stage"] == "operator_router"
    assert event["parse_seconds"] > 0
    assert event["compute_seconds"] > 0
    assert event["rss_peak_bytes"] > 0


if __name__ == "__main__":
    test_operator_registry_covers_o10_to_o18()
    test_router_covers_representative_stage03_paths()
    test_question_evolution_uses_route_and_skips_passthrough()
    test_candidate_generation_falls_back_when_no_operator_available()
    test_candidate_generation_failure_returns_passthrough_candidate()
    test_state_recommendation_precedes_memory_and_failed_operator_is_avoided()
    print("stage03 operator routing checks passed")
