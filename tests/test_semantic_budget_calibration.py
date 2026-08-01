import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from prompts.operators import build_operator_prompt
from semantic_budget import (
    answer_generation_context,
    build_reference_ledgers,
    detect_surface_leaks,
    generator_visible_context,
    suggested_same_operator_retry_reason,
)


def test_reference_ledgers_keep_observable_facts_separate_from_boundary_and_rubric_intent():
    ledgers = build_reference_ledgers(
        original_prompt="视频显示甲在 10:00 将物品放入柜台，随后乙取走物品。应如何研判？",
        reference_answer="视频显示乙随后取走物品。不能直接确认乙为物品所有人，只能作为线索。",
        rubric=[{"title": "边界", "description": "说明不能将取走行为直接认定为所有权。"}],
    )
    observable_text = "\n".join(entry["text"] for entry in ledgers["observable_fact_ledger"])
    boundary_text = "\n".join(entry["text"] for entry in ledgers["answer_boundary_ledger"])
    rubric_text = "\n".join(entry["text"] for entry in ledgers["rubric_intent_ledger"])

    assert "甲在 10:00" in observable_text
    assert "乙随后取走" in observable_text
    assert "只能作为线索" in boundary_text
    assert "所有权" in rubric_text


def test_question_generator_context_excludes_answer_boundary_and_rubric_intent():
    ledgers = build_reference_ledgers(
        original_prompt="画面显示车辆进入停车场。应如何研判？",
        reference_answer="不能直接确认驾驶人身份，只能作为线索。",
        rubric=[{"title": "评分维度", "description": "区分事实与推断。"}],
    )
    visible = generator_visible_context(original_prompt="画面显示车辆进入停车场。应如何研判？", ledgers=ledgers)
    rendered = build_operator_prompt(
        "O27_cross_layer_conclusion_calibration",
        prompt="画面显示车辆进入停车场。应如何研判？",
        reference_answer="不能直接确认驾驶人身份，只能作为线索。",
        candidate_answer="直接确认身份。",
        rubric=[{"title": "评分维度", "description": "区分事实与推断。"}],
        sample_profile={},
        overscore_diagnosis={},
        evolution_state={},
        operator_route={},
        generator_visible_context=visible,
    )

    assert "answer_boundary_ledger" not in visible
    assert "rubric_intent_ledger" not in visible
    assert "不能直接确认驾驶人身份" not in rendered
    assert "评分维度" not in rendered
    assert "observable_fact_ledger" in rendered


def test_surface_leak_detector_distinguishes_leaks_from_natural_request_for_reasons():
    boundary = detect_surface_leaks("请判断现有材料最高支持什么结论，并说明依据。")
    safe_option = detect_surface_leaks(
        "请选择：\nA. 已查明违法并立即处置\nB. 疑似线索，按可见参与情况开展核查\nC. 直接确认身份"
    )
    natural = detect_surface_leaks("请根据题面事实作出业务判断，并说明依据。")

    assert "boundary_language_leak" in boundary["surface_leak_type"]
    assert "safe_option_leak" in safe_option["surface_leak_type"]
    assert natural["surface_leak_risk"] is False
    assert "删除题面中的答案边界提示" in suggested_same_operator_retry_reason(boundary)
    assert "重写全部选项" in suggested_same_operator_retry_reason(safe_option)


def test_answer_generation_context_receives_boundary_while_question_surface_does_not():
    ledgers = build_reference_ledgers(
        original_prompt="画面显示车辆进入停车场。应如何研判？",
        reference_answer="不能直接确认驾驶人身份，只能作为线索。",
    )
    answer_context = answer_generation_context("画面显示车辆进入停车场。应如何研判？", ledgers)
    assert "答案侧边界与评分意图" in answer_context
    assert "只能作为线索" in answer_context
    assert "题面已明确提示" in answer_context
