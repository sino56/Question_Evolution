import asyncio
import json
import sys
from pathlib import Path


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


def response(candidates):
    return {
        "routing_schema_version": ROUTING_SCHEMA_VERSION,
        "reasoning_objects": [
            {
                "name": "实体与时段绑定",
                "evidence_spans": ["实体甲在第一时段将物品交给实体乙"],
                "confidence": 0.7,
            }
        ],
        "operator_candidates": candidates,
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
