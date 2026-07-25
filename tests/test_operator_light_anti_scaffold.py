import sys
from dataclasses import fields
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from prompts.operators import OPERATOR_SPECS
from prompts.operators.base import OperatorPromptSpec


def test_operator_prompt_spec_contract_is_not_expanded():
    assert [field.name for field in fields(OperatorPromptSpec)] == [
        "operator_id",
        "name",
        "ability_axis",
        "goal",
        "required_question_shape",
        "avoid",
        "default_evaluation_focus",
    ]


def test_targeted_operators_use_light_anti_scaffold_text():
    o10 = OPERATOR_SPECS["O10_evidence_sufficiency_ladder"]
    o13 = OPERATOR_SPECS["O13_minimal_disqualifier"]
    o17 = OPERATOR_SPECS["O17_action_vs_fact_threshold"]
    o18 = OPERATOR_SPECS["O18_baseline_scope_mismatch"]

    assert o10.name == "近似判断竞争"
    assert "固定层级模板" in o10.avoid
    assert "答案标签" in o10.avoid

    assert o13.name == "边界诱发式最小推翻事实"
    assert "直接推翻/削弱/外围/无关" in o13.avoid

    assert o17.name == "处置触发与事实定性门槛"
    assert "不要明说处置门槛或事实定性门槛" in o17.avoid

    assert o18.name == "基准样本范围错配"
    assert "不要明说基准范围不一致或样本口径错配" in o18.avoid


if __name__ == "__main__":
    test_operator_prompt_spec_contract_is_not_expanded()
    test_targeted_operators_use_light_anti_scaffold_text()
    print("operator light anti-scaffold checks passed")
