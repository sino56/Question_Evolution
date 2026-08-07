import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operator_content_audit import build_risk_report, detect_surface_risks
from operator_execution_contracts import OPERATOR_EXECUTION_CONTRACTS
from prompts.operators import OPERATOR_SPECS, build_operator_prompt
from question_evolution import parse_evolution_response


GENERATING_OPERATOR_IDS = tuple(
    operator_id for operator_id, spec in OPERATOR_SPECS.items() if spec.generates_question
)


@pytest.mark.parametrize("operator_id", tuple(OPERATOR_SPECS))
def test_every_operator_has_a_material_contract(operator_id):
    contract = OPERATOR_EXECUTION_CONTRACTS[operator_id]
    assert contract.operator_id == operator_id
    assert contract.required_slots
    assert contract.neutral_task_intent
    assert contract.required_checks
    assert not set(contract.required_slots).issubset(contract.non_synthesizable_slots)


def test_o14_is_validation_only_in_the_content_contract():
    contract = OPERATOR_EXECUTION_CONTRACTS["O14_information_closure"]
    assert contract.generates_question is False
    assert "information_closure" in contract.required_checks
    assert OPERATOR_SPECS[contract.operator_id].generates_question is False


@pytest.mark.parametrize("operator_id", GENERATING_OPERATOR_IDS)
def test_every_generating_operator_has_required_content_controls(operator_id):
    controls = OPERATOR_SPECS[operator_id].content_controls
    assert all(controls[key] for key in (
        "decisive_fact_ablation",
        "irrelevant_fact_ablation",
        "name_or_order_swap",
    ))


@pytest.mark.parametrize("operator_id", GENERATING_OPERATOR_IDS)
def test_every_generating_operator_uses_a_neutral_single_task_shape(operator_id):
    shape = OPERATOR_SPECS[operator_id].required_question_shape
    for answer_side_marker in (
        "最高支持",
        "不能直接认定",
        "为什么不成立",
        "缺少第",
        "关键证据",
        "关键闭合",
        "支持到什么程度",
        "共同支持的边界",
    ):
        assert answer_side_marker not in shape
    assert "依据" in shape or "理由" in shape


@pytest.mark.parametrize("operator_id", GENERATING_OPERATOR_IDS)
def test_neutral_operator_writer_contract_has_no_answer_side_fields(operator_id):
    rendered = build_operator_prompt(
        operator_id,
        prompt="根据材料作出业务判断。",
        reference_answer="不应进入 writer。",
        candidate_answer="不应进入 writer。",
        rubric=[{"criterion": "不应进入 writer"}],
        sample_profile={"hidden": "不应进入 writer"},
        overscore_diagnosis={"hidden": "不应进入 writer"},
        evolution_state={},
        operator_route={},
    )
    assert "不应进入 writer" not in rendered
    assert operator_id not in rendered
    assert '"evolved_prompt"' in rendered
    assert '"used_fact_ids"' in rendered
    assert '"surface_notes"' in rendered
    for forbidden in (
        "expected_qwen_failure",
        "expected_evaluation_focus",
        "target_subclaim",
        "boundary_hypothesis",
        "answer_key",
        "answer_rationale",
        "decisive_fact_ids",
    ):
        assert forbidden not in rendered


def test_writer_response_is_sanitized_to_the_three_public_fields():
    parsed = parse_evolution_response(json.dumps({
        "evolved_prompt": "根据两段记录判断是否属于同一车辆，并说明依据。",
        "used_fact_ids": ["F01", "F02"],
        "surface_notes": "按时间顺序组织公开记录。",
        "expected_qwen_failure": "不应保留",
        "target_subclaim": "不应保留",
    }, ensure_ascii=False))
    assert parsed == {
        "evolved_prompt": "根据两段记录判断是否属于同一车辆，并说明依据。",
        "used_fact_ids": ["F01", "F02"],
        "surface_notes": "按时间顺序组织公开记录。",
    }


@pytest.mark.parametrize("operator_id", GENERATING_OPERATOR_IDS)
def test_each_operator_has_positive_negative_leak_and_control_fixture_slots(operator_id):
    contract = OPERATOR_EXECUTION_CONTRACTS[operator_id]
    # Contracts make the four fixture families explicit without turning them
    # into runtime gates.  The individual control descriptions stay in specs.
    assert contract.neutral_task_intent
    assert contract.required_slots
    assert OPERATOR_SPECS[operator_id].forbidden_shortcuts
    assert OPERATOR_SPECS[operator_id].content_controls


def test_surface_leak_replay_detects_governance_examples_without_a_disposition():
    assert detect_surface_risks("请说明为什么现有材料不能直接认定该结论。") == ["answer_direction"]
    risks = detect_surface_risks("关键证据已经满足 R3 规则，另一解释已被排除。")
    assert {"fact_role_disclosure", "rule_application_disclosure", "competitor_elimination"} <= set(risks)
    assert detect_surface_risks("根据记录判断当前业务主张是否成立，并说明依据。") == []


def test_offline_risk_report_is_advisory_and_preserves_gray_release_path(tmp_path):
    records = [
        {"candidate_operator": "O10_evidence_sufficiency_ladder", "prompt": "根据记录作出判断。"}
        for _ in range(20)
    ]
    records.append({
        "candidate_operator": "O11_unobserved_state_attribution",
        "prompt": "关键证据表明不能直接认定。",
    })
    report = build_risk_report(records)
    assert report["online_disposition"] == "none"
    assert report["by_operator"]["O10_evidence_sufficiency_ladder"]["gray_release_recommendation"] == "eligible_for_gray_release_review"
    assert report["by_operator"]["O11_unobserved_state_attribution"]["gray_release_recommendation"] == "continue_forced_qualification"
