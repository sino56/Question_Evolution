import asyncio
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from answer_contract_resolver import (
    build_blind_solver_prompt,
    resolve_answer_contract_hypotheses,
)
from question_evolution import QuestionEvolutionProcessor
from operator_contract_test_utils import contract_fields_for_prompt


class FakeMessage:
    def __init__(self, content):
        self.content = content


class FakeChoice:
    def __init__(self, content):
        self.message = FakeMessage(content)


class FakeResponse:
    def __init__(self, content):
        self.choices = [FakeChoice(content)]


class GeneratorAndBlindClient:
    def __init__(self):
        self.calls = []

    async def chat_completions_create(self, **kwargs):
        self.calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        if "独立 Blind Solver" in prompt:
            return FakeResponse(
                json.dumps(
                    {
                        "target_claim": {"claim_id": "C1", "text": "目标业务判断"},
                        "conclusion_layer": "overall_claim",
                        "answer_key": {
                            "selected_fact_id": "F_selected",
                            "claim_level_effect": "local_link_broken_overall_supported",
                        },
                        "decisive_fact_ids": ["F_selected", "F_support"],
                        "answer_summary": "F_selected 破坏局部连接，但 F_support 仍支持整体判断。",
                    },
                    ensure_ascii=False,
                )
            )
        payload = {
            "evolved_prompt": "复核 F_selected 后，原目标业务判断是否仍成立？请说明依据。",
            "evolution_strategy": "隐藏连接角色并保留整体结论边界。",
        }
        payload.update(contract_fields_for_prompt(prompt))
        return FakeResponse(json.dumps(payload, ensure_ascii=False))


def strict_item():
    return {
        "sample_id": "blind-o13",
        "prompt": "原题",
        "reference_answer": "参考答案",
        "scoring_result": {"candidate_answer": "候选答案"},
        "score_rate": 1.0,
        "evolution_action": "evolve_high_score_overscore",
        "sample_profile": {
            "core_capability": "必要连接",
            "claim_level": "overall_claim",
            "problem_shape": "复核事实",
            "external_knowledge_risk": "low",
        },
        "overscore_diagnosis": {
            "is_worth_evolving": True,
            "candidate_overscore_cause": "原评价与新增事实",
            "target_failure_mode": "连接失效层级错误",
        },
        "operator_route": {
            "primary_operator": "O13_minimal_disqualifier",
            "backup_operators": [],
            "avoid_operators": [],
            "routing_reason": "fixture",
        },
        "fact_ledger": [
            {"fact_id": "F_selected", "fact_type": "observed", "text": "复核事实"},
            {"fact_id": "F_support", "fact_type": "observed", "text": "替代支持"},
        ],
        "operator_manifest": {
            "human_confirmed": True,
            "target_claim": {"claim_id": "C1", "text": "目标业务判断"},
            "required_link_id": "L1",
            "candidate_fact_ids": ["F_selected", "F_support"],
        },
    }


def test_blind_solver_prompt_excludes_generator_and_operator_secrets():
    prompt = build_blind_solver_prompt(
        evolved_prompt="题目",
        fact_ledger=[{"fact_id": "F1", "fact_type": "observed"}],
    )
    assert "O13_minimal_disqualifier" not in prompt
    assert "expected_qwen_failure" not in prompt
    assert "generator_answer_key" not in prompt
    assert "独立 Blind Solver" in prompt


def test_contract_resolver_requires_exact_structured_agreement():
    generator = {
        "answer_key": {"direction": "decreased"},
        "decisive_fact_ids": ["F1"],
    }
    blind = {
        "target_claim": {"claim_id": "C1"},
        "conclusion_layer": "fact_claim",
        "answer_key": {"direction": "decreased"},
        "decisive_fact_ids": ["F1"],
        "answer_summary": "支持度降低。",
    }
    resolved = resolve_answer_contract_hypotheses(
        target_claim={"claim_id": "C1"},
        conclusion_layer="fact_claim",
        generator_answer_contract=generator,
        blind_solver_result=blind,
    )
    assert resolved["status"] == "resolved"

    conflicting = dict(blind)
    conflicting["answer_key"] = {"direction": "reversed"}
    result = resolve_answer_contract_hypotheses(
        target_claim={"claim_id": "C1"},
        conclusion_layer="fact_claim",
        generator_answer_contract=generator,
        blind_solver_result=conflicting,
    )
    assert result["status"] == "conflict"
    assert result["conflict_fields"] == ["answer_key"]


def test_strict_generation_calls_independent_blind_solver_before_freezing():
    client = GeneratorAndBlindClient()
    processor = QuestionEvolutionProcessor(
        client,
        model="fixture",
        max_concurrent=1,
        max_retries=0,
        max_validation_retries=0,
        strict_operator_contracts=True,
        enable_blind_solver=True,
    )
    record = asyncio.run(processor.process_item(strict_item()))
    assert record["question_evolved"] is True
    assert len(client.calls) == 2
    metadata = record["meta_info"]["question_evolution_metadata"]
    resolution = metadata["operator_envelope"]["answer_contract_resolution"]
    assert resolution["status"] == "resolved"
    assert resolution["required"] is True
    assert metadata["answer_contract"]["frozen"] is True
