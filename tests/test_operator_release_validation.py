import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from candidate_selection import validation_quality_score
from operator_contracts import answer_contract_hash, build_candidate_envelope
from validate_evolved_question import attach_validation_result


def source_item():
    return {
        "sample_id": "release-o13",
        "prompt": "原题：根据 F1、F2 形成的连接判断目标命题。",
        "reference_answer": "参考答案",
        "scoring_result": {"candidate_answer": "候选答案"},
        "fact_ledger": [
            {"fact_id": "F1", "fact_type": "observed"},
            {"fact_id": "F2", "fact_type": "observed"},
            {"fact_id": "F3", "fact_type": "observed"},
        ],
        "operator_manifest": {
            "target_claim": {"claim_id": "C1"},
            "required_link_id": "L1",
            "candidate_fact_ids": ["F1", "F2", "F3"],
        },
    }


def evolved_output(*, leakage=False):
    return {
        "target_claim": {"claim_id": "C1", "text": "目标命题"},
        "conclusion_layer": "overall_claim",
        "surface_fact_ids": ["F2", "F3"],
        "applied_transforms": ["add_review_fact"],
        "operator_payload": {
            "selected_fact_id": "F2",
            "broken_link_id": "L1",
            "claim_level_effect": "local_link_broken_overall_supported",
            "alternative_support_fact_ids": ["F3"],
            "unknown_payload_extension": {"retained": True},
        },
        "surface_leakage_risks": {
            "option_only": leakage,
            "fact_ablated": False,
            "surface_swapped": False,
            "parent_obligation_drift": False,
            "cross_operator_isomorphism": False,
        },
        "answer_contract": {
            "answer_key": {
                "selected_fact_id": "F2",
                "broken_link_id": "L1",
                "claim_level_effect": "local_link_broken_overall_supported",
            },
            "decisive_fact_ids": ["F2", "F3"],
            "rubric_assertions": ["识别 F2 破坏 L1", "整体命题仍有 F3 支持"],
        },
    }


def candidate_record(*, leakage=False):
    source = source_item()
    envelope = build_candidate_envelope(
        evolved_output(leakage=leakage),
        operator_id="O13_minimal_disqualifier",
        source_record=source,
        operator_manifest=source["operator_manifest"],
    )
    return {
        **source,
        "prompt": "复核 F2 后，原目标业务判断是否仍成立？请说明依据。",
        "question_evolved": True,
        "candidate_operator": "O13_minimal_disqualifier",
        "meta_info": {
            "prompt_old": source["prompt"],
            "question_evolution_metadata": {
                "question_evolved": True,
                "operator_used": "O13_minimal_disqualifier",
                "operator_envelope": envelope,
                "operator_payload": envelope["operator_payload"],
                "answer_contract": envelope["answer_contract"],
            },
        },
    }


def test_valid_v2_candidate_passes_o14_closure_and_keeps_unknown_payload():
    record = candidate_record()
    validated = attach_validation_result(record)
    validation = validated["validation_result"]
    envelope = validated["meta_info"]["question_evolution_metadata"]["operator_envelope"]
    assert validation["passed"] is True
    assert validation["release_status"] == "eligible"
    assert validation["information_closure_findings"] == []
    assert envelope["operator_payload"]["unknown_payload_extension"] == {"retained": True}


def test_missing_or_forbidden_fact_reference_is_hard_reject():
    missing = candidate_record()
    missing_envelope = missing["meta_info"]["question_evolution_metadata"]["operator_envelope"]
    missing_envelope["operator_payload"]["selected_fact_id"] = "F404"
    missing_envelope["answer_contract"]["operator_answer"] = deepcopy(missing_envelope["operator_payload"])
    missing_envelope["answer_contract"]["answer_key"]["selected_fact_id"] = "F404"
    missing_envelope["answer_contract"]["decisive_fact_ids"] = ["F404"]
    missing_envelope["answer_contract"]["answer_contract_hash"] = answer_contract_hash(
        missing_envelope["answer_contract"]
    )
    result = attach_validation_result(missing)["validation_result"]
    assert result["passed"] is False
    assert result["release_status"] == "reject_candidate"
    assert any("F404" in error for error in result["deterministic_errors"])

    forbidden = candidate_record()
    forbidden["fact_ledger"][1]["fact_type"] = "external_knowledge"
    result = attach_validation_result(forbidden)["validation_result"]
    assert result["passed"] is False
    assert any(
        finding["code"] == "forbidden_fact_type_promoted"
        for finding in result["information_closure_findings"]
    )


def test_answer_key_payload_conflict_blocks_release_even_with_valid_hash():
    record = candidate_record()
    envelope = record["meta_info"]["question_evolution_metadata"]["operator_envelope"]
    contract = envelope["answer_contract"]
    contract["answer_key"]["claim_level_effect"] = "overall_claim_reversed"
    contract["answer_contract_hash"] = answer_contract_hash(contract)
    validation = attach_validation_result(record)["validation_result"]
    assert validation["passed"] is False
    assert any(
        "answer_contract.answer_key.claim_level_effect" in error
        for error in validation["deterministic_errors"]
    )


def test_probability_diagnostics_do_not_hard_reject():
    validation = attach_validation_result(candidate_record(leakage=True))["validation_result"]
    assert validation["passed"] is True
    assert validation["release_status"] == "diagnostic_risk"
    assert any("option_only" in diagnostic for diagnostic in validation["diagnostics"])


def test_o15_reversal_without_explicit_threshold_is_hard_reject():
    source = source_item()
    output = {
        "target_claim": {"claim_id": "C2"},
        "conclusion_layer": "fact_claim",
        "surface_fact_ids": ["F1"],
        "applied_transforms": ["replace_single_fact"],
        "operator_payload": {
            "changed_fact_id": "F1",
            "comparison_quantity": "evidence_support",
            "direction_or_order": "reversed",
            "conclusion_layer_effect": "overall_reversal",
            "threshold_given": False,
        },
        "surface_leakage_risks": {},
        "answer_contract": {
            "answer_key": {
                "changed_fact_id": "F1",
                "comparison_quantity": "evidence_support",
                "direction_or_order": "reversed",
                "conclusion_layer_effect": "overall_reversal",
            },
            "decisive_fact_ids": ["F1"],
        },
    }
    envelope = build_candidate_envelope(
        output,
        operator_id="O15_counterfactual_threshold_shift",
        source_record=source,
        operator_manifest={},
    )
    record = {
        **source,
        "prompt": "事实变化后，目标业务判断是否翻转？",
        "question_evolved": True,
        "candidate_operator": "O15_counterfactual_threshold_shift",
        "meta_info": {
            "prompt_old": source["prompt"],
            "question_evolution_metadata": {
                "operator_used": "O15_counterfactual_threshold_shift",
                "operator_envelope": envelope,
            },
        },
    }
    validation = attach_validation_result(record)["validation_result"]
    assert validation["passed"] is False
    assert any("without an explicit threshold" in error for error in validation["deterministic_errors"])


def test_quantity_metrics_do_not_change_candidate_validation_ranking_score():
    short = candidate_record()
    short["validation_result"] = {
        "passed": True,
        "contract_mode": "v2",
        "release_status": "eligible",
        "main_axis_count": 1,
        "estimated_prompt_chars": 100,
        "output_tasks_count": 1,
        "candidate_options_count": 1,
        "counterfactual_count": 0,
        "external_knowledge_risk": "low",
        "format_difficulty_risk": "low",
        "repeat_pattern_risk": "low",
    }
    long = deepcopy(short)
    long["validation_result"].update(
        {
            "main_axis_count": 5,
            "estimated_prompt_chars": 5000,
            "output_tasks_count": 8,
            "candidate_options_count": 9,
            "counterfactual_count": 7,
        }
    )
    assert validation_quality_score(short) == validation_quality_score(long)
