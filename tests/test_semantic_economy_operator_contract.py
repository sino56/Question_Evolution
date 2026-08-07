import sys
from dataclasses import fields
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from prompts.operators import OPERATOR_SPECS, build_operator_prompt
from prompts.operators.base import OperatorPromptSpec


@pytest.mark.parametrize("spec", OPERATOR_SPECS.values(), ids=lambda spec: spec.operator_id)
def test_all_operators_define_and_render_semantic_economy(spec):
    assert spec.semantic_economy
    rendered = build_operator_prompt(
        spec.operator_id,
        prompt="根据画面中的可观察事实作出一项业务判断。",
        reference_answer="不能直接确认身份，只能作为线索。",
        candidate_answer="候选回答。",
        rubric=[],
        sample_profile={"target_error": "internal only"},
        overscore_diagnosis={},
        evolution_state={},
        operator_route={},
    )
    assert "题面每个独立句段必须承担" in rendered
    assert "共享主体、时段、目标命题与不变背景只出现一次" in rendered
    assert '"used_fact_ids"' in rendered
    assert '"surface_notes"' in rendered
    assert "balanced_semantic_load" not in rendered
    assert "similar_length" not in rendered
    assert "1200" not in rendered
    assert "字符区间" not in rendered
    for rule in spec.semantic_economy:
        assert rule in rendered


def test_semantic_economy_is_a_required_operator_prompt_spec_field():
    assert "semantic_economy" in {item.name for item in fields(OperatorPromptSpec)}
    with pytest.raises(TypeError):
        OperatorPromptSpec(
            operator_id="test",
            name="test",
            ability_axis="test",
            goal="test",
            required_question_shape="test",
            avoid="test",
            default_evaluation_focus=("test",),
        )


def test_special_operator_contracts_preserve_required_content_without_surface_leaks():
    assert OPERATOR_SPECS["O14_information_closure"].generates_question is False
    assert any("事实并集" in rule for rule in OPERATOR_SPECS["O10_evidence_sufficiency_ladder"].semantic_economy)
    assert any("公共题干" in rule for rule in OPERATOR_SPECS["O12_conjunctive_necessity"].semantic_economy)
    assert any("完整且必要的规则可保留" in rule for rule in OPERATOR_SPECS["O17_action_vs_fact_threshold"].semantic_economy)
    assert any("全部数值" in rule for rule in OPERATOR_SPECS["O26_quantitative_threshold_propagation"].semantic_economy)
    assert any("最高支持" in rule for rule in OPERATOR_SPECS["O27_cross_layer_conclusion_calibration"].semantic_economy)
