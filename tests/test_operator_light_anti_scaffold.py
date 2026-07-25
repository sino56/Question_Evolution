import sys
from dataclasses import fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from prompts.operators import OPERATOR_SPECS, build_operator_prompt
from prompts.operators.base import OperatorPromptSpec


def test_operator_prompt_spec_contract_covers_content_definition_template():
    field_names = [field.name for field in fields(OperatorPromptSpec)]
    assert field_names == [
        "operator_id",
        "name",
        "ability_axis",
        "goal",
        "required_question_shape",
        "avoid",
        "default_evaluation_focus",
        "reasoning_object",
        "content_transformation",
        "invariants",
        "competition_structure",
        "preserved_parent_obligations",
        "required_reasoning_tasks",
        "semantic_axes",
        "scene_content_seeds",
        "target_error_taxonomy",
        "excluded_error_taxonomy",
        "forbidden_shortcuts",
        "adjacent_operator_boundaries",
        "positive_controls",
        "conclusion_invariant_negative_controls",
        "adjacent_operator_controls",
        "surface_swap_controls",
        "hidden_role_balance_controls",
        "allowed_answer_shapes",
        "forbidden_answer_shapes",
        "generates_question",
    ]
    assert not {
        "semantic_version",
        "prompt_version",
        "applicability_version",
        "validation_policy_version",
        "required_fact_slots",
        "operator_payload_schema",
        "scorer_mapping",
        "release_checks",
        "status",
    } & set(field_names)


def test_targeted_operators_use_light_anti_scaffold_text():
    o10 = OPERATOR_SPECS["O10_evidence_sufficiency_ladder"]
    o11 = OPERATOR_SPECS["O11_unobserved_state_attribution"]
    o12 = OPERATOR_SPECS["O12_conjunctive_necessity"]
    o13 = OPERATOR_SPECS["O13_minimal_disqualifier"]
    o14 = OPERATOR_SPECS["O14_information_closure"]
    o15 = OPERATOR_SPECS["O15_counterfactual_threshold_shift"]
    o16 = OPERATOR_SPECS["O16_close_alternative_normalization"]
    o17 = OPERATOR_SPECS["O17_action_vs_fact_threshold"]
    o18 = OPERATOR_SPECS["O18_baseline_scope_mismatch"]

    assert o10.name == "最小充分事实集"
    assert "固定层级模板" in o10.avoid
    assert "最小集合" in o10.avoid
    assert "missing_minimal_set_member" in o10.target_error_taxonomy
    assert any("移除集合中任一成员" in control for control in o10.positive_controls)

    assert o12.name == "独立性与共同必要性"
    assert any("X/Y/Vx/Vy/Vxy" in shortcut for shortcut in o12.forbidden_shortcuts)
    assert "monotonic_gain_mistaken_for_joint_necessity" in o12.target_error_taxonomy

    assert o13.name == "必要连接破坏与推翻层级"
    assert "不要声明存在唯一破坏项" in o13.avoid
    assert "local_failure_vs_overall_claim_confusion" in o13.target_error_taxonomy
    assert any("整体目标命题" in task for task in o13.required_reasoning_tasks)

    assert o15.name == "单一比较量与结论门槛"
    assert "不要混用" in o15.avoid
    assert "被比较的语义量在变化前后保持唯一且一致" in o15.invariants
    assert "unsupported_full_reversal" in o15.target_error_taxonomy

    assert o16.name == "相近解释覆盖与残差"
    assert "discriminator" in o16.reasoning_object
    assert "residual_ignored" in o16.target_error_taxonomy
    assert any("删除 discriminator" in invariant for invariant in o16.invariants)

    assert o11.name == "端点时序一致性"
    assert "unobserved_state_fabricated" in o11.target_error_taxonomy
    assert any("盲区内" in shortcut for shortcut in o11.forbidden_shortcuts)

    assert o14.generates_question is False
    assert o14.competition_structure == "不适用；O14 不是独立生成算子"
    assert not o14.required_reasoning_tasks

    assert o17.name == "双规则边界映射"
    assert "不要省略规则" in o17.avoid
    assert "dual_rule_scope_mapping" == o17.ability_axis

    assert o18.name == "基线纳入口径与异常性"
    assert "不要依赖" in o18.avoid
    assert "baseline_inclusion_scope_ignored" in o18.target_error_taxonomy


def test_every_generating_operator_has_complete_content_controls():
    required_scalar_fields = (
        "ability_axis",
        "goal",
        "reasoning_object",
        "required_question_shape",
        "content_transformation",
        "competition_structure",
        "avoid",
    )
    required_sequence_fields = (
        "invariants",
        "preserved_parent_obligations",
        "required_reasoning_tasks",
        "target_error_taxonomy",
        "excluded_error_taxonomy",
        "forbidden_shortcuts",
        "adjacent_operator_boundaries",
        "positive_controls",
        "conclusion_invariant_negative_controls",
        "adjacent_operator_controls",
        "surface_swap_controls",
        "hidden_role_balance_controls",
        "allowed_answer_shapes",
        "forbidden_answer_shapes",
    )
    generating_specs = [spec for spec in OPERATOR_SPECS.values() if spec.generates_question]
    assert {spec.operator_id for spec in generating_specs} == (
        set(OPERATOR_SPECS) - {"O14_information_closure"}
    )
    for spec in generating_specs:
        for field_name in required_scalar_fields:
            assert getattr(spec, field_name), f"{spec.operator_id}.{field_name}"
        for field_name in required_sequence_fields:
            assert getattr(spec, field_name), f"{spec.operator_id}.{field_name}"


def test_prompt_keeps_content_controls_internal_and_preserves_open_reasoning():
    rendered = build_operator_prompt(
        "O16_close_alternative_normalization",
        prompt="原题要求结合多项观察判断当前事件。",
        reference_answer="应基于全部观察控制结论边界。",
        candidate_answer="替代解释出现，因此没有异常。",
        rubric=[],
        sample_profile={},
        overscore_diagnosis={},
        evolution_state={},
        operator_route={"primary_operator": "O16_close_alternative_normalization"},
    )
    assert "内部内容规格（只用于构造题目" in rendered
    assert "不得把字段名、角色名、控制说明或预期方向复制到题面" in rendered
    assert "required_reasoning_tasks 由回答者自行完成" in rendered
    assert "只提出一个自然业务判断和开放式依据要求" in rendered
    assert "适用性门控、资格状态和发布校验不属于本内容 Prompt" in rendered


if __name__ == "__main__":
    test_operator_prompt_spec_contract_is_not_expanded()
    test_targeted_operators_use_light_anti_scaffold_text()
    print("operator light anti-scaffold checks passed")
