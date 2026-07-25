import asyncio
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from operator_contracts import (
    DISABLED,
    ELIGIBLE,
    NOT_APPLICABLE,
    OPERATOR_SPACE_EXHAUSTED,
    evaluate_operator_applicability,
)
from operator_router import OPERATOR_ORDER, route_records
from question_evolution import QuestionEvolutionProcessor, resolve_operator_plan


class NoCallClient:
    def __init__(self):
        self.calls = []

    async def chat_completions_create(self, **kwargs):
        self.calls.append(kwargs)
        raise AssertionError("generation model must not be called")


def base_item():
    return {
        "sample_id": "strict-applicability",
        "prompt": "原题",
        "reference_answer": "参考答案",
        "scoring_result": {"candidate_answer": "候选答案"},
        "score_rate": 1.0,
        "evolution_action": "evolve_high_score_overscore",
        "sample_profile": {
            "core_capability": "边界判断",
            "claim_level": "事实结论",
            "problem_shape": "事实组合",
            "external_knowledge_risk": "low",
        },
        "overscore_diagnosis": {
            "is_worth_evolving": True,
            "candidate_overscore_cause": "原评价与新增事实",
            "target_failure_mode": "必要连接判断错误",
        },
        "operator_route": {
            "primary_operator": "O13_minimal_disqualifier",
            "backup_operators": ["O15_counterfactual_threshold_shift"],
            "avoid_operators": [],
            "routing_reason": "fixture",
        },
    }


def test_disabled_and_validation_only_operators_are_not_in_generation_registry():
    assert "O14_information_closure" not in OPERATOR_ORDER
    assert "O11_unobserved_state_attribution" not in OPERATOR_ORDER
    assert "O17_action_vs_fact_threshold" not in OPERATOR_ORDER
    assert "O18_baseline_scope_mismatch" not in OPERATOR_ORDER

    routed = route_records(
        [
            {
                **base_item(),
                "overscore_diagnosis": {
                    "is_worth_evolving": True,
                    "candidate_overscore_cause": "题外补设和信息闭包",
                    "target_failure_mode": "题干外事实",
                },
            }
        ]
    )[0]["operator_route"]
    assert routed["primary_operator"] == "O10_evidence_sufficiency_ladder"
    assert routed["shadow_operator_plan"][0]["operator_id"] == "O14_information_closure"
    assert routed["shadow_operator_plan"][0]["registry_status"] == "validation_only"


def test_o11_o17_o18_missing_any_hard_prerequisite_are_not_applicable():
    for operator_id in (
        "O11_unobserved_state_attribution",
        "O17_action_vs_fact_threshold",
        "O18_baseline_scope_mismatch",
    ):
        result = evaluate_operator_applicability(
            base_item(),
            operator_id,
            allow_disabled=True,
        )
        assert result["status"] == NOT_APPLICABLE
        assert result["candidate_budget_consumed"] is False
        assert result["missing_required_fact_slots"]


def test_not_applicable_does_not_consume_budget_and_next_operator_is_tried():
    item = base_item()
    item["meta_info"] = {
        "operator_manifests": {
            "O15_counterfactual_threshold_shift": {
                "human_confirmed": True,
                "target_claim": {"claim_id": "C1"},
                "changed_fact_id": "F1",
                "comparison_quantity": "evidence_support",
                "conclusion_layer": "fact_claim",
            }
        }
    }
    plan = resolve_operator_plan(item, 1, strict_contracts=True)
    assert plan["operator_ids"] == ["O15_counterfactual_threshold_shift"]
    assert plan["applicability_attempts"][0]["operator_id"] == "O13_minimal_disqualifier"
    assert plan["applicability_attempts"][0]["status"] == NOT_APPLICABLE
    assert plan["applicability_attempts"][0]["candidate_budget_consumed"] is False
    assert plan["applicability_attempts"][1]["status"] == ELIGIBLE
    assert plan["applicability_attempts"][1]["candidate_budget_consumed"] is True


def test_operator_space_exhausted_preserves_parent_and_skips_model_call():
    item = base_item()
    client = NoCallClient()
    processor = QuestionEvolutionProcessor(
        client,
        model="unused",
        max_concurrent=1,
        max_retries=0,
        num_candidates=2,
        strict_operator_contracts=True,
    )
    records = asyncio.run(processor.process_item_candidates(item, requested_candidates=2))
    assert len(records) == 1
    record = records[0]
    assert record["question_evolved"] is False
    assert record["question_evolution_status"] == OPERATOR_SPACE_EXHAUSTED
    assert record["operator_space_exhausted"]["parent_preserved"] is True
    assert record["operator_space_exhausted"]["applicability_attempts"]
    assert client.calls == []


def test_disabled_o17_can_only_be_evaluated_for_forced_qualification():
    item = base_item()
    item["operator_manifest"] = {
        "human_confirmed": True,
        "rule_a_text": "规则 A",
        "rule_a_version": "1",
        "rule_a_subject": "处置",
        "rule_a_threshold": "达到 A",
        "rule_b_text": "规则 B",
        "rule_b_version": "1",
        "rule_b_subject": "事实",
        "rule_b_threshold": "达到 B",
        "current_facts": ["F1"],
    }
    natural = evaluate_operator_applicability(
        item,
        "O17_action_vs_fact_threshold",
    )
    forced = evaluate_operator_applicability(
        item,
        "O17_action_vs_fact_threshold",
        allow_disabled=True,
    )
    assert natural["status"] == DISABLED
    assert forced["status"] == ELIGIBLE
