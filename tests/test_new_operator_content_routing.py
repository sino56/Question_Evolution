import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from operator_contracts import QUALIFICATION_ONLY, get_operator_contract
from operator_router import route_records
from prompts.operators import OPERATOR_SPECS, build_operator_prompt


def _item(keyword):
    return {
        "sample_id": f"route-{keyword}",
        "prompt": "请根据题面材料判断目标业务命题是否成立，并说明依据。",
        "reference_answer": "只能在题面证据支持范围内作答。",
        "score_rate": 1.0,
        "scoring_result": {"candidate_answer": "目标命题成立。"},
        "evolution_action": "evolve_high_score_overscore",
        "sample_profile": {
            "core_capability": keyword,
            "claim_level": "evidence_support",
            "problem_shape": keyword,
            "external_knowledge_risk": "low",
        },
        "overscore_diagnosis": {
            "is_worth_evolving": True,
            "candidate_overscore_cause": keyword,
            "target_failure_mode": keyword,
        },
    }


def test_new_operator_ids_are_stable_and_qualification_only():
    for number in range(19, 34):
        matching = [
            operator_id
            for operator_id in OPERATOR_SPECS
            if operator_id.startswith(f"O{number}_")
        ]
        assert len(matching) == 1
        contract = get_operator_contract(matching[0])
        assert contract.semantic_version == "1.0"
        assert contract.status == QUALIFICATION_ONLY


def test_rule_router_recognizes_each_new_primary_reasoning_object_without_enabling_it():
    cases = {
        "多实体角色绑定": "O19_multi_entity_role_binding",
        "多阶段事件链断点": "O20_multistage_event_breakpoint",
        "物品来源与同一性": "O21_object_provenance_identity",
        "路径拓扑联合可达": "O22_path_topology_reachability",
        "观测质量与可见性": "O23_observation_reliability_conflict",
        "多假设残差排序": "O24_multi_hypothesis_residual_ranking",
        "程序不变量与参照系": "O25_procedural_invariant_frame",
        "不确定区间与误差传播": "O26_quantitative_threshold_propagation",
        "跨层结论映射": "O27_cross_layer_conclusion_calibration",
        "跨阶段多跳链路整体闭合": "O28_multihop_chain_closure",
        "冲突绑定下的实体同一性": "O29_entity_identity_conflict_resolution",
        "下一主动判别观测": "O30_active_discriminative_observation",
        "多次观测独立增量": "O31_observation_accumulation_calibration",
        "角色关系图关键边": "O32_role_graph_critical_edge",
        "跨模态多源融合": "O33_cross_modal_support_boundary",
    }
    for keyword, expected in cases.items():
        route = route_records([_item(keyword)])[0]["operator_route"]
        assert route["recognized_operator_id"] == expected
        assert route["recognized_operator_registry_status"] == QUALIFICATION_ONLY
        assert route["primary_operator"] != expected
        assert route["shadow_operator_plan"][0]["operator_id"] == expected


def test_unified_prompt_entry_calls_new_operator_without_leaking_internal_outline():
    operator_id = "O33_cross_modal_support_boundary"
    prompt = build_operator_prompt(
        operator_id,
        prompt="综合视频和时间记录判断目标线索。",
        reference_answer="只能形成线索。",
        candidate_answer="已经确认身份。",
        rubric=[],
        sample_profile={},
        overscore_diagnosis={},
        evolution_state={},
        operator_route={"recognized_operator_id": operator_id},
        operator_manifest={},
        fact_ledger=[],
    )
    assert operator_id in prompt
    assert "不得要求分别判断每个来源" not in prompt
    assert "不得用\"逐项说明\"" in prompt
    assert '"ability_axes"' in prompt
