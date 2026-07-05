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
    o1 = OPERATOR_SPECS["O1_gap_choice"]
    o2 = OPERATOR_SPECS["O2_subclaim_localization"]
    o4 = OPERATOR_SPECS["O4_near_level_ranking"]
    o8 = OPERATOR_SPECS["O8_double_threshold_claim"]

    assert o1.name == "事实缺口识别"
    assert "给出 A/B 两个近似候选" not in o1.required_question_shape
    assert "最小事实" not in o1.avoid
    assert "不要显式要求在 A/B 缺口中选择" in o1.required_question_shape

    assert o2.name == "子结论边界定位"
    assert "把结论拆成 2 个以内子判断" not in o2.required_question_shape
    assert "不要显式要求指出哪一层已成立或未成立" in o2.required_question_shape

    assert o4.name == "近似依据边界辨析"
    assert "提供 2-3 个近似理由或事实层级" not in o4.required_question_shape
    assert "不要要求形式化排序或分层" in o4.required_question_shape

    assert o8.name == "性质门槛辨析"
    assert "把目标结论拆成两个门槛" not in o8.required_question_shape
    assert "不要显式拆成两个门槛" in o8.required_question_shape


if __name__ == "__main__":
    test_operator_prompt_spec_contract_is_not_expanded()
    test_targeted_operators_use_light_anti_scaffold_text()
    print("operator light anti-scaffold checks passed")
