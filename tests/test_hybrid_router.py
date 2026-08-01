import asyncio
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operator_router import (
    ASSIGNMENT_MODE_LIVE,
    ROUTING_MODE_HYBRID,
    RouterCache,
    RouterCallResult,
    RouterSettings,
    eligible_operator_ids,
    route_records_hybrid_async,
)
from operator_registry import OPERATOR_RUNTIME_POLICY
from prompts.operators import OPERATOR_SPECS, build_operator_prompt
from router_contract import ROUTING_SCHEMA_VERSION


class FakeRouterClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def route(self, prompt):
        self.calls.append(prompt)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return RouterCallResult(
            raw_response=json.dumps(response, ensure_ascii=False),
            input_tokens=123,
            output_tokens=45,
            elapsed_seconds=0.2,
        )


def sample():
    return {
        "sample_id": "hybrid-route-1",
        "prompt": "实体甲在第一时段将物品交给实体乙，第二时段实体乙独自离开。",
        "reference_answer": "应先确认实体和时段绑定，再判断结论边界。",
        "candidate_answer": "两人出现过，因此可以直接确认二者全程协同。",
        "score_rate": 0.95,
        "evolution_action": "evolve_high_score_overscore",
        "sample_profile": {
            "core_capability": "实体与角色绑定",
            "claim_level": "业务判断",
            "problem_shape": "开放判断",
            "external_knowledge_risk": "low",
        },
        "overscore_diagnosis": {
            "is_worth_evolving": True,
            "candidate_overscore_cause": "实体角色绑定与身份连续性需要区分",
            "target_failure_mode": "主体角色交换后错误地全程归属",
        },
    }


def response(candidates, *, audit=None, reasoning_span="实体甲在第一时段将物品交给实体乙"):
    if audit is None:
        audit = {
            "selected_operator_rationales": [
                {
                    "operator_id": item["operator_id"],
                    "matched_failure_mechanism": "候选答案的目标失败机制与该推理结构直接对应。",
                    "satisfied_hard_slots": ["题面已给出该结构所需事实。"],
                    "no_fabricated_facts": True,
                }
                for item in candidates
            ],
            "not_selected_operator_rationales": [],
            "uncertain_operator_rationales": [],
            "operator_improvement_notes": [],
        }
    return {
        "routing_schema_version": ROUTING_SCHEMA_VERSION,
        "reasoning_objects": [
            {
                "name": "实体与时段绑定",
                "evidence_spans": [reasoning_span],
                "confidence": 0.7,
            }
        ],
        "operator_candidates": candidates,
        "operator_decision_audit": audit,
        "not_selected_reasons": [],
        "router_comment": "",
    }


def candidate(operator_id, rank, adjacent):
    return {
        "operator_id": operator_id,
        "rank": rank,
        "applicability": "applicable",
        "confidence": 0.8,
        "reasoning_object": "实体与时段绑定",
        "evidence_spans": ["实体乙独自离开"],
        "why_fit": "题目需要绑定实体、角色与时段。",
        "why_not_adjacent": {adjacent: "相邻算子不以当前的核心绑定关系为主。"},
    }


def settings():
    return RouterSettings.from_values(
        routing_mode=ROUTING_MODE_HYBRID,
        assignment_mode=ASSIGNMENT_MODE_LIVE,
        model="fake-router",
        base_url="https://router.invalid/v1",
        timeout_seconds=60,
        retries=0,
        concurrency=20,
    )


def test_live_hybrid_route_keeps_all_valid_llm_candidates_in_router_rank_order():
    client = FakeRouterClient(
        [
            response(
                [
                    candidate("O19_multi_entity_role_binding", 2, "O29_entity_identity_conflict_resolution"),
                    candidate("O20_multistage_event_breakpoint", 1, "O28_multihop_chain_closure"),
                ]
            )
        ]
    )
    routed = asyncio.run(
        route_records_hybrid_async([sample()], settings=settings(), client=client)
    )

    route = routed[0]["operator_route"]
    assert route["route_source"] == "llm"
    assert route["selected_operator_ids"] == [
        "O20_multistage_event_breakpoint",
        "O19_multi_entity_role_binding",
    ]
    assert route["primary_operator"] == "O20_multistage_event_breakpoint"
    assert route["backup_operators"] == ["O19_multi_entity_role_binding"]
    assert route["router_fallback_used"] is False
    assert route["http_attempt_count"] == 1
    assert len(client.calls) == 1
    assert "operator_cards" in client.calls[0]


def test_frontier_route_bypasses_only_original_admission_and_is_explicit_in_prompt():
    record = sample()
    record["evolution_action"] = "stop_evolution"
    record["frontier_route"] = {
        "enabled": True,
        "parent_node_id": "hybrid-route-1::root::O10",
        "root_node_id": "hybrid-route-1::root",
        "parent_depth": 2,
        "operator_stack": ["O10_evidence_sufficiency_ladder"],
        "direct_parent_score_rate": 0.8,
        "root_score_rate": 1.0,
        "profile_version": "frontier-profile-v1",
    }
    client = FakeRouterClient(
        [response([candidate("O19_multi_entity_role_binding", 1, "O29_entity_identity_conflict_resolution")])]
    )
    routed = asyncio.run(
        route_records_hybrid_async([record], settings=settings(), client=client)
    )

    route = routed[0]["operator_route"]
    assert route["route_source"] == "llm"
    assert route["is_frontier_route"] is True
    assert route["selected_operator_ids"] == ["O19_multi_entity_role_binding"]
    assert '"frontier_route"' in client.calls[0]
    assert "不要重新执行仅适用于原始样本" in client.calls[0]


def test_invalid_candidate_does_not_discard_valid_sibling():
    invalid = candidate("O19_multi_entity_role_binding", 1, "O29_entity_identity_conflict_resolution")
    invalid["evidence_spans"] = ["算子卡片中的虚构证据"]
    client = FakeRouterClient(
        [response([invalid, candidate("O20_multistage_event_breakpoint", 2, "O28_multihop_chain_closure")])]
    )
    routed = asyncio.run(
        route_records_hybrid_async([sample()], settings=settings(), client=client)
    )

    route = routed[0]["operator_route"]
    assert route["route_source"] == "llm"
    assert route["selected_operator_ids"] == ["O20_multistage_event_breakpoint"]
    assert route["router_rejected_candidates"][0]["reason"] == "hallucinated_evidence"


def test_audit_only_directions_do_not_add_or_reorder_execution_candidates():
    selected = candidate("O19_multi_entity_role_binding", 1, "O29_entity_identity_conflict_resolution")
    audit = {
        "selected_operator_rationales": [
            {
                "operator_id": "O19_multi_entity_role_binding",
                "matched_failure_mechanism": "错误地把角色和定向行为归属给同一实体。",
                "satisfied_hard_slots": ["竞争实体", "跨节点绑定线索", "定向行为事实"],
                "no_fabricated_facts": True,
            }
        ],
        "not_selected_operator_rationales": [
            {
                "operator_id": "O29_entity_identity_conflict_resolution",
                "reason": "题面没有排他性身份冲突，当前失败是角色绑定而非同一性裁决。",
                "nearer_selected_operator_id": "O19_multi_entity_role_binding",
            }
        ],
        "uncertain_operator_rationales": [
            {
                "operator_id": "O22_path_topology_reachability",
                "missing_hard_slots": ["路径图节点", "边通行约束", "端点时间窗"],
                "would_need_fabricated_facts": "强行构题需要补写路径节点、通行边和时间窗口。",
            }
        ],
        "operator_improvement_notes": [],
    }
    client = FakeRouterClient([response([selected], audit=audit)])

    routed = asyncio.run(route_records_hybrid_async([sample()], settings=settings(), client=client))

    route = routed[0]["operator_route"]
    assert route["selected_operator_ids"] == ["O19_multi_entity_role_binding"]
    assert route["primary_operator"] == "O19_multi_entity_role_binding"
    assert route["operator_decision_audit"] == audit


def test_well_formed_empty_candidate_list_keeps_audit_and_creates_no_branch():
    audit = {
        "selected_operator_rationales": [],
        "not_selected_operator_rationales": [],
        "uncertain_operator_rationales": [
            {
                "operator_id": "O11_unobserved_state_attribution",
                "missing_hard_slots": ["预期出口窗口", "路径约束", "候选假设比较"],
                "would_need_fabricated_facts": "强行构题需要补写盲区端点、路径和候选解释。",
            }
        ],
        "operator_improvement_notes": ["当前算子卡片可进一步区分端点时序与事件链恢复。"],
    }
    client = FakeRouterClient([response([], audit=audit)])

    routed = asyncio.run(route_records_hybrid_async([sample()], settings=settings(), client=client))

    route = routed[0]["operator_route"]
    assert route["route_source"] == "llm"
    assert route["selected_operator_ids"] == []
    assert route["primary_operator"] is None
    assert route["backup_operators"] == []
    assert route["operator_decision_audit"] == audit


@pytest.mark.parametrize(
    ("prompt", "candidate_answer", "operator_id", "missing_slots"),
    (
        (
            "视频在盲区前后出现同一车辆，但没有入口或出口的具体时间窗口。",
            "车辆再次出现，因此盲区内必然完成了目标行为。",
            "O11_unobserved_state_attribution",
            ["入口与出口时间窗", "路径或速度约束", "候选假设比较"],
        ),
        (
            "多人在同一地点出现，但材料没有跨节点身份线索或定向行为事实。",
            "多人共同出现，说明每个人都参与了同一行为。",
            "O19_multi_entity_role_binding",
            ["竞争实体绑定线索", "节点差异", "定向行为事实"],
        ),
        (
            "材料提到一条可能路线，但没有节点、边、通行限制或端点时间窗。",
            "既然存在路线，就一定能够在该时间到达终点。",
            "O22_path_topology_reachability",
            ["路径节点", "边通行限制", "端点时间窗"],
        ),
    ),
)
def test_hard_slot_missing_route_decision_fixtures_stay_in_uncertain_audit(
    prompt,
    candidate_answer,
    operator_id,
    missing_slots,
):
    record = sample()
    record["prompt"] = prompt
    record["candidate_answer"] = candidate_answer
    audit = {
        "selected_operator_rationales": [],
        "not_selected_operator_rationales": [],
        "uncertain_operator_rationales": [
            {
                "operator_id": operator_id,
                "missing_hard_slots": missing_slots,
                "would_need_fabricated_facts": "强行构题需要补写题面尚未提供的关键结构事实。",
            }
        ],
        "operator_improvement_notes": [],
    }
    client = FakeRouterClient([response([], audit=audit, reasoning_span=candidate_answer)])

    routed = asyncio.run(route_records_hybrid_async([record], settings=settings(), client=client))

    route = routed[0]["operator_route"]
    assert route["selected_operator_ids"] == []
    assert route["operator_decision_audit"]["uncertain_operator_rationales"] == audit["uncertain_operator_rationales"]


def test_conclusion_layer_fixture_prefers_o27_over_surface_related_o23_and_o31():
    record = sample()
    record["prompt"] = "视频片段提供的是线索支持，题面同时给出事实表述和行动处置各自的条件。"
    record["candidate_answer"] = "候选答案将视频线索直接写成可执行处置结论。"
    record["overscore_diagnosis"] = {
        "is_worth_evolving": True,
        "candidate_overscore_cause": "证据支持被直接上推为行动结论",
        "target_failure_mode": "将线索越级写成可执行处置结论",
    }
    selected = {
        "operator_id": "O27_cross_layer_conclusion_calibration",
        "rank": 1,
        "applicability": "applicable",
        "confidence": 0.9,
        "reasoning_object": "从线索支持到可执行结论的跨层边界",
        "evidence_spans": ["候选答案将视频线索直接写成可执行处置结论。"],
        "why_fit": "目标失败是结论层级越界，题面已给出各层条件。",
        "why_not_adjacent": {
            "O17_action_vs_fact_threshold": "当前核心不是两套规则对象映射，而是支持效力的跨层传递。"
        },
    }
    audit = {
        "selected_operator_rationales": [
            {
                "operator_id": "O27_cross_layer_conclusion_calibration",
                "matched_failure_mechanism": "线索支持被越级上推为可执行处置结论。",
                "satisfied_hard_slots": ["支持材料", "层级规则", "越级结论"],
                "no_fabricated_facts": True,
            }
        ],
        "not_selected_operator_rationales": [
            {
                "operator_id": "O23_observation_reliability_conflict",
                "reason": "题面没有决定性的观测质量冲突，核心是结论跨层而非可靠性。",
                "nearer_selected_operator_id": "O27_cross_layer_conclusion_calibration",
            },
            {
                "operator_id": "O31_observation_accumulation_calibration",
                "reason": "题面没有多次观测的独立性问题，核心不是证据累积校准。",
                "nearer_selected_operator_id": "O27_cross_layer_conclusion_calibration",
            },
        ],
        "uncertain_operator_rationales": [],
        "operator_improvement_notes": [],
    }
    client = FakeRouterClient([response([selected], audit=audit, reasoning_span=record["candidate_answer"])])

    routed = asyncio.run(route_records_hybrid_async([record], settings=settings(), client=client))

    route = routed[0]["operator_route"]
    assert route["selected_operator_ids"] == ["O27_cross_layer_conclusion_calibration"]
    assert {item["operator_id"] for item in route["operator_decision_audit"]["not_selected_operator_rationales"]} == {
        "O23_observation_reliability_conflict",
        "O31_observation_accumulation_calibration",
    }


def test_all_invalid_llm_candidates_use_deterministic_fallback():
    invalid = candidate("O19_multi_entity_role_binding", 1, "O29_entity_identity_conflict_resolution")
    invalid["why_not_adjacent"] = {"O18_baseline_scope_mismatch": "不是相邻算子。"}
    client = FakeRouterClient([response([invalid])])
    routed = asyncio.run(
        route_records_hybrid_async([sample()], settings=settings(), client=client)
    )

    route = routed[0]["operator_route"]
    assert route["route_source"] == "deterministic_fallback"
    assert route["router_fallback_used"] is True
    assert route["router_error_classification"] == "no_valid_candidates"
    assert route["selected_operator_ids"]


def test_successful_router_cache_is_reused_without_second_http_call(tmp_path):
    cache = RouterCache(str(tmp_path / "router_cache.jsonl"))
    client = FakeRouterClient(
        [response([candidate("O19_multi_entity_role_binding", 1, "O29_entity_identity_conflict_resolution")])]
    )
    first = asyncio.run(
        route_records_hybrid_async([sample()], settings=settings(), client=client, cache=cache)
    )
    second = asyncio.run(
        route_records_hybrid_async([sample()], settings=settings(), client=client, cache=RouterCache(str(tmp_path / "router_cache.jsonl")))
    )

    assert len(client.calls) == 1
    assert first[0]["operator_route"]["router_cache_hit"] is False
    assert second[0]["operator_route"]["router_cache_hit"] is True
    assert second[0]["operator_route"]["http_attempt_count"] == 0


def test_identical_concurrent_cache_keys_share_one_http_call(tmp_path):
    client = FakeRouterClient(
        [response([candidate("O19_multi_entity_role_binding", 1, "O29_entity_identity_conflict_resolution")])]
    )
    routed = asyncio.run(
        route_records_hybrid_async(
            [sample(), dict(sample())],
            settings=settings(),
            client=client,
            cache=RouterCache(str(tmp_path / "router_cache.jsonl")),
        )
    )

    assert len(client.calls) == 1
    assert [row["operator_route"]["selected_operator_ids"] for row in routed] == [
        ["O19_multi_entity_role_binding"],
        ["O19_multi_entity_role_binding"],
    ]


def test_live_eligibility_allows_qualification_only_but_respects_authoritative_ledger(monkeypatch):
    operator_id = "O19_multi_entity_role_binding"
    monkeypatch.setitem(
        OPERATOR_RUNTIME_POLICY,
        operator_id,
        {"generation_enabled": True, "validation_only": False, "qualification_status": "qualification_only"},
    )
    eligible, excluded = eligible_operator_ids(sample())
    assert operator_id in eligible
    assert operator_id not in excluded

    record = sample()
    record["fact_ledger"] = {
        "authoritative": True,
        "complete": True,
        "operator_preconditions": {operator_id: False},
    }
    eligible, excluded = eligible_operator_ids(record)
    assert operator_id not in eligible
    assert excluded[operator_id] == "authoritative_fact_ledger_precondition_false"


def test_generation_prompt_does_not_receive_router_evidence_or_ranking():
    rendered = build_operator_prompt(
        "O19_multi_entity_role_binding",
        prompt="原题",
        reference_answer="参考答案",
        candidate_answer="候选答案",
        rubric=[],
        sample_profile={},
        overscore_diagnosis={},
        evolution_state={},
        operator_route={
            "routing_reason": "此内容不得进入下游题目生成提示词",
            "router_candidates": [{"why_fit": "也不得进入"}],
            "router_raw_response_trace_id": "trace-id",
            "route_source": "llm",
        },
    )
    assert "此内容不得进入下游题目生成提示词" not in rendered
    assert "也不得进入" not in rendered
    assert "trace-id" not in rendered
