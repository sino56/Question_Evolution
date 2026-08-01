import importlib
import inspect
import sys
from dataclasses import fields
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


import operator_router
from operator_router import route_records
from prompts.operators import OPERATOR_SPECS, build_operator_prompt
from prompts.operators.base import OperatorPromptSpec
from prompts.operators.new_operator_specs import NEW_OPERATOR_SPECS


EXPECTED_NEW_OPERATOR_IDS = (
    "O19_multi_entity_role_binding",
    "O20_multistage_event_breakpoint",
    "O21_object_provenance_identity",
    "O22_path_topology_reachability",
    "O23_observation_reliability_conflict",
    "O24_multi_hypothesis_residual_ranking",
    "O25_procedural_invariant_frame",
    "O26_quantitative_threshold_propagation",
    "O27_cross_layer_conclusion_calibration",
    "O28_multihop_chain_closure",
    "O29_entity_identity_conflict_resolution",
    "O30_active_discriminative_observation",
    "O31_observation_accumulation_calibration",
    "O32_role_graph_critical_edge",
    "O33_cross_modal_support_boundary",
)


SEMANTIC_ECONOMY_EXPECTATIONS = {
    "O19_multi_entity_role_binding": ("实体—角色—时段关系", "完整出场表"),
    "O20_multistage_event_breakpoint": ("阶段状态和必要承接", "完整流程"),
    "O21_object_provenance_identity": ("对象来源、关键转移、遮挡和竞争来源", "完整来源链"),
    "O22_path_topology_reachability": ("节点、方向边、端点和时间窗口", "完整路网"),
    "O23_observation_reliability_conflict": ("可见条件与竞争来源", "最高支持"),
    "O24_multi_hypothesis_residual_ranking": ("共同事实只在题干出现一次", "覆盖矩阵"),
    "O25_procedural_invariant_frame": ("共享程序、参照系和不变条件只写一次", "两套完整程序"),
    "O26_quantitative_threshold_propagation": ("全部数值与定义", "中间计算"),
    "O27_cross_layer_conclusion_calibration": ("跨层判断所需的事实张力和竞争证据", "最高支持"),
    "O28_multihop_chain_closure": ("多跳链子图和必要连接", "完整路径集合"),
    "O29_entity_identity_conflict_resolution": ("共享实体画像只定义一次", "完整排除表"),
    "O30_active_discriminative_observation": ("当前竞争假设和能改变选择价值的可观测差异", "结果分支或更新矩阵"),
    "O31_observation_accumulation_calibration": ("共享画面内容只写一次", "观察全文或统计表"),
    "O32_role_graph_critical_edge": ("角色节点和关键关系边", "完整关系图"),
    "O33_cross_modal_support_boundary": ("各模态事实、时间/实体对齐和竞争关系", "融合总结"),
}


def test_new_operator_ids_are_stable_unique_and_registered():
    actual_ids = tuple(spec.operator_id for spec in NEW_OPERATOR_SPECS)
    assert actual_ids == EXPECTED_NEW_OPERATOR_IDS
    assert len(actual_ids) == len(set(actual_ids))
    assert all(OPERATOR_SPECS[operator_id].operator_id == operator_id for operator_id in actual_ids)
    assert tuple(OPERATOR_SPECS)[:9] == tuple(
        f"O{number}_{suffix}"
        for number, suffix in (
            (10, "evidence_sufficiency_ladder"),
            (11, "unobserved_state_attribution"),
            (12, "conjunctive_necessity"),
            (13, "minimal_disqualifier"),
            (14, "information_closure"),
            (15, "counterfactual_threshold_shift"),
            (16, "close_alternative_normalization"),
            (17, "action_vs_fact_threshold"),
            (18, "baseline_scope_mismatch"),
        )
    )


def test_each_new_operator_has_an_individual_module_used_by_the_registry():
    for spec in NEW_OPERATOR_SPECS:
        module = importlib.import_module(f"prompts.operators.{spec.operator_id}")
        assert module.SPEC is spec
        assert OPERATOR_SPECS[spec.operator_id] is module.SPEC


@pytest.mark.parametrize("operator_id", EXPECTED_NEW_OPERATOR_IDS)
def test_each_individual_new_operator_owns_its_semantic_budget_contract(operator_id):
    module = importlib.import_module(f"prompts.operators.{operator_id}")
    required_content, forbidden_expansion = SEMANTIC_ECONOMY_EXPECTATIONS[operator_id]

    assert "semantic_economy=" in inspect.getsource(module)
    assert any(required_content in rule for rule in module.SPEC.semantic_economy)
    assert any(forbidden_expansion in rule for rule in module.SPEC.semantic_economy)


@pytest.mark.parametrize("spec", NEW_OPERATOR_SPECS, ids=lambda spec: spec.operator_id)
def test_each_new_operator_has_complete_content_spec(spec):
    assert spec.reasoning_object
    assert spec.content_transformation
    assert spec.invariants
    assert spec.competition_structure
    assert spec.preserved_parent_obligations
    assert spec.required_reasoning_tasks
    assert spec.semantic_axes
    assert spec.target_error_taxonomy
    assert spec.excluded_error_taxonomy
    assert spec.forbidden_shortcuts
    assert spec.adjacent_operator_boundaries
    assert spec.positive_controls
    assert spec.conclusion_invariant_negative_controls
    assert spec.adjacent_operator_controls
    assert spec.surface_swap_controls
    assert spec.hidden_role_balance_controls
    assert spec.allowed_answer_shapes
    assert spec.forbidden_answer_shapes
    assert spec.semantic_economy
    assert spec.prompt_recipe_version == "semantic_economy_structural_v1"
    assert spec.generates_question is True
    for axis in spec.semantic_axes:
        assert set(axis) == {
            "axis_name",
            "reasoning_task",
            "target_errors",
            "conclusion_boundary",
            "content_dependencies",
        }
        assert all(axis.values())


def test_four_scene_content_seeds_stay_inside_content_specs():
    assert len(OPERATOR_SPECS["O30_active_discriminative_observation"].scene_content_seeds) == 4
    assert len(OPERATOR_SPECS["O31_observation_accumulation_calibration"].scene_content_seeds) == 4
    assert len(OPERATOR_SPECS["O33_cross_modal_support_boundary"].scene_content_seeds) == 4


@pytest.mark.parametrize("spec", NEW_OPERATOR_SPECS, ids=lambda spec: spec.operator_id)
def test_new_operators_render_surface_contract_without_leaking_internal_tasks(spec):
    rendered = build_operator_prompt(
        spec.operator_id,
        prompt="根据现有材料作出一个业务判断并说明依据。",
        reference_answer="结论必须受题面证据边界约束。",
        candidate_answer="候选答案过度推广了局部事实。",
        rubric=[],
        sample_profile={},
        overscore_diagnosis={},
        evolution_state={},
        operator_route={"primary_operator": spec.operator_id},
    )
    assert spec.operator_id in rendered
    assert '"semantic_economy"' in rendered
    assert "题面生成可见上下文" in rendered
    for rule in spec.semantic_economy:
        assert rule in rendered
    assert '"semantic_axes"' not in rendered
    assert '"required_reasoning_tasks"' not in rendered
    assert "一个自然业务判断和开放式依据要求" in rendered
    assert "不得使用“逐项说明”“分别列出”“先……再……”" in rendered
    assert "共享主体、时段、目标命题与不变背景只出现一次" in rendered


def test_first_part_does_not_add_second_part_runtime_contract_fields():
    field_names = {field.name for field in fields(OperatorPromptSpec)}
    assert field_names.isdisjoint(
        {
            "semantic_version",
            "applicability_spec",
            "quality_contract",
            "answer_contract",
            "operator_payload",
            "qualification_status",
            "adapter",
            "validator",
            "release_gate",
        }
    )


def test_new_operator_content_does_not_impose_fixed_element_counts():
    rendered_content = "\n".join(
        str(value)
        for spec in NEW_OPERATOR_SPECS
        for value in (
            spec.goal,
            spec.required_question_shape,
            spec.reasoning_object,
            spec.content_transformation,
            spec.invariants,
            spec.required_reasoning_tasks,
        )
    )
    assert "至少一" not in rendered_content
    assert "必须包含三个" not in rendered_content
    assert "必须包含四个" not in rendered_content


@pytest.mark.parametrize(
    ("diagnosis", "expected_operator"),
    (
        ("多实体角色绑定出现主体角色交换", "O19_multi_entity_role_binding"),
        ("多阶段事件链存在链路断点", "O20_multistage_event_breakpoint"),
        ("对象来源链有转移缺口和竞争来源", "O21_object_provenance_identity"),
        ("路径拓扑与时间窗需要联合可达性判断", "O22_path_topology_reachability"),
        ("观测可靠性受可见性与清晰度限制", "O23_observation_reliability_conflict"),
        ("多假设残差排序忽略额外假设成本", "O24_multi_hypothesis_residual_ranking"),
        ("程序不变量因参照系一致性破坏", "O25_procedural_invariant_frame"),
        ("不确定区间发生误差传播并跨阈值", "O26_quantitative_threshold_propagation"),
        ("支持到事实的跨层结论发生越级", "O27_cross_layer_conclusion_calibration"),
        ("跨阶段跨节点的多跳链路未整体闭合", "O28_multihop_chain_closure"),
        ("实体同一性冲突由排他身份线索触发", "O29_entity_identity_conflict_resolution"),
        ("需要选择下一步观测以最大化区分力", "O30_active_discriminative_observation"),
        ("同源重复被误当作独立的观测累积", "O31_observation_accumulation_calibration"),
        ("角色关系图中关系边方向和必要关系边错误", "O32_role_graph_critical_edge"),
        ("跨模态材料需要时间与实体对齐后再融合", "O33_cross_modal_support_boundary"),
    ),
)
def test_rule_router_recognizes_each_new_content_operator(diagnosis, expected_operator):
    item = {
        "sample_id": expected_operator,
        "prompt": "根据材料作出业务判断。",
        "score_rate": 0.9,
        "evolution_action": "evolve_high_score_overscore",
        "sample_profile": {
            "core_capability": "结构化业务判断",
            "claim_level": "综合结论",
            "problem_shape": "开放判断",
            "external_knowledge_risk": "low",
        },
        "overscore_diagnosis": {
            "is_worth_evolving": True,
            "candidate_overscore_cause": diagnosis,
            "target_failure_mode": diagnosis,
        },
    }
    route = route_records([item])[0]["operator_route"]
    assert route["primary_operator"] == expected_operator
    assert route["backup_operators"]


def test_new_operator_ids_match_router_registry_but_do_not_replace_legacy_fallback():
    assert set(operator_router.OPERATOR_ORDER) == set(OPERATOR_SPECS)
    assert tuple(operator_router.NEW_CONTENT_OPERATOR_ORDER) == EXPECTED_NEW_OPERATOR_IDS
    assert all(
        operator_id not in operator_router.LEGACY_OPERATOR_ORDER
        for operator_id in EXPECTED_NEW_OPERATOR_IDS
    )
