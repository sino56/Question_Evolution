import sys
from dataclasses import fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from prompts.operators import GENERATION_OPERATOR_SPECS, OPERATOR_SPECS
from prompts.operators.base import OperatorPromptSpec


def test_operator_prompt_spec_implements_uniform_content_template():
    assert [field.name for field in fields(OperatorPromptSpec)] == [
        "operator_id",
        "name",
        "ability_axis",
        "goal",
        "reasoning_object",
        "required_question_shape",
        "content_transformation",
        "invariants",
        "competition_structure",
        "preserved_parent_obligations",
        "required_reasoning_tasks",
        "target_error_taxonomy",
        "excluded_error_taxonomy",
        "forbidden_shortcuts",
        "adjacent_boundaries",
        "content_controls",
        "allowed_answer_shape",
        "forbidden_answer_shape",
        "default_evaluation_focus",
        "generates_question",
        "ability_axes",
        "axis_reasoning_tasks",
        "axis_dependencies",
    ]
    for spec in OPERATOR_SPECS.values():
        for field_name in (
            "ability_axis",
            "reasoning_object",
            "required_question_shape",
            "content_transformation",
            "competition_structure",
            "forbidden_shortcuts",
            "adjacent_boundaries",
            "allowed_answer_shape",
            "forbidden_answer_shape",
        ):
            assert getattr(spec, field_name)
        assert spec.invariants
        assert spec.preserved_parent_obligations
        assert spec.required_reasoning_tasks
        assert spec.target_error_taxonomy
        assert spec.content_controls


def test_new_operator_specs_cover_o19_to_o33_without_fixed_quantity_gates():
    expected = {f"O{number}" for number in range(19, 34)}
    actual = {operator_id.split("_", 1)[0] for operator_id in OPERATOR_SPECS}
    assert expected <= actual
    for operator_id, spec in OPERATOR_SPECS.items():
        if int(operator_id[1:].split("_", 1)[0]) < 19:
            continue
        assert spec.ability_axes
        prompt_text = " ".join(
            (
                spec.required_question_shape,
                spec.competition_structure,
                spec.forbidden_shortcuts,
            )
        )
        assert "固定数量" not in prompt_text


def test_repaired_operators_encode_content_boundaries_and_controls():
    o10 = OPERATOR_SPECS["O10_evidence_sufficiency_ladder"]
    o13 = OPERATOR_SPECS["O13_minimal_disqualifier"]
    o15 = OPERATOR_SPECS["O15_counterfactual_threshold_shift"]
    o16 = OPERATOR_SPECS["O16_close_alternative_normalization"]
    o17 = OPERATOR_SPECS["O17_action_vs_fact_threshold"]
    o18 = OPERATOR_SPECS["O18_baseline_scope_mismatch"]

    assert o10.name == "最小充分事实集"
    assert "成员消融" in " ".join(o10.content_controls)
    assert "方向标签" in o10.forbidden_shortcuts

    assert o13.name == "必要连接与推翻层级"
    assert "claim_level_effect" in o13.required_reasoning_tasks
    assert "整体翻转" in o13.forbidden_answer_shape

    assert o15.name == "比较量与结论门槛"
    assert "无明确阈值" in o15.forbidden_shortcuts

    assert o16.name == "相近解释的覆盖与残差"
    assert "discriminator" in o16.required_reasoning_tasks[1]

    assert o17.name == "双规则边界判断"
    assert "两套规则" in o17.required_question_shape

    assert o18.name == "基线口径和异常性"
    assert "更权威" in o18.forbidden_shortcuts


def test_o14_is_validation_only_and_not_in_generation_registry():
    o14 = OPERATOR_SPECS["O14_information_closure"]
    assert o14.generates_question is False
    assert o14.operator_id not in GENERATION_OPERATOR_SPECS


if __name__ == "__main__":
    test_operator_prompt_spec_implements_uniform_content_template()
    test_repaired_operators_encode_content_boundaries_and_controls()
    test_o14_is_validation_only_and_not_in_generation_registry()
    print("operator light anti-scaffold checks passed")
