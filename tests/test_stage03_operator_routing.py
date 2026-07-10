import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operator_router import route_records
from prompts.operators import OPERATOR_SPECS
from question_evolution import QuestionEvolutionProcessor


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


if __name__ == "__main__":
    test_operator_registry_covers_o10_to_o18()
    test_router_covers_representative_stage03_paths()
    test_question_evolution_uses_route_and_skips_passthrough()
    test_candidate_generation_falls_back_when_no_operator_available()
    test_candidate_generation_failure_returns_passthrough_candidate()
    test_state_recommendation_precedes_memory_and_failed_operator_is_avoided()
    print("stage03 operator routing checks passed")
