import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from operator_contracts import (
    DISABLED,
    ENABLED,
    VALIDATION_ONLY,
    OPERATOR_CONTRACTS,
    build_candidate_envelope,
    validate_candidate_envelope,
)
from question_evolution import enrich_evolution_result_with_operator, make_evolved_record
from schema_validation import load_schema, validate_instance


def o13_evolved_output():
    return {
        "evolved_prompt": "复核材料均与原判断相关。现有材料下原业务判断是否仍成立？请说明依据。",
        "evolution_strategy": "隐藏候选角色，要求回答者自行定位必要连接。",
        "target_claim": {"claim_id": "C1", "text": "原业务判断成立"},
        "conclusion_layer": "overall_claim",
        "surface_fact_ids": ["F3", "F4"],
        "applied_transforms": ["add_review_fact"],
        "operator_payload": {
            "selected_fact_id": "F3",
            "broken_link_id": "L1",
            "claim_level_effect": "local_link_broken_overall_supported",
            "alternative_support_fact_ids": ["F4"],
            "future_unknown_field": {"kept": True},
        },
        "surface_leakage_risks": {
            "option_only": False,
            "surface_swapped": False,
        },
        "answer_contract": {
            "answer_key": {
                "selected_fact_id": "F3",
                "claim_level_effect": "local_link_broken_overall_supported",
            },
            "decisive_fact_ids": ["F3", "F4"],
            "rubric_assertions": [
                "识别 F3 破坏 L1",
                "不把局部连接失效夸大为整体翻转",
            ],
            "future_answer_field": "retained",
        },
        "notes_for_reference": "需补充局部连接与整体命题的区分。",
    }


def source_item():
    return {
        "sample_id": "contract-o13",
        "round": 1,
        "prompt": "原题",
        "reference_answer": "参考答案",
        "scoring_result": {"candidate_answer": "候选答案"},
        "score_rate": 1.0,
        "evolution_action": "evolve_high_score_overscore",
        "sample_profile": {"claim_level": "overall_claim"},
        "overscore_diagnosis": {"target_failure_mode": "结论层级错误"},
        "operator_route": {
            "primary_operator": "O13_minimal_disqualifier",
            "backup_operators": [],
            "avoid_operators": [],
            "routing_reason": "fixture",
        },
        "fact_ledger": [
            {"fact_id": "F3", "fact_type": "observed"},
            {"fact_id": "F4", "fact_type": "observed"},
        ],
        "operator_manifest": {
            "target_claim": {"claim_id": "C1"},
            "required_link_id": "L1",
            "candidate_fact_ids": ["F3", "F4"],
        },
    }


def test_contract_registry_has_versions_statuses_and_release_checks():
    legacy_ids = {
        "O10_evidence_sufficiency_ladder",
        "O11_unobserved_state_attribution",
        "O12_conjunctive_necessity",
        "O13_minimal_disqualifier",
        "O14_information_closure",
        "O15_counterfactual_threshold_shift",
        "O16_close_alternative_normalization",
        "O17_action_vs_fact_threshold",
        "O18_baseline_scope_mismatch",
    }
    assert legacy_ids <= set(OPERATOR_CONTRACTS)
    assert len(OPERATOR_CONTRACTS) == 24
    assert OPERATOR_CONTRACTS["O13_minimal_disqualifier"].status == ENABLED
    assert OPERATOR_CONTRACTS["O14_information_closure"].status == VALIDATION_ONLY
    assert OPERATOR_CONTRACTS["O17_action_vs_fact_threshold"].status == DISABLED
    for operator_id, contract in OPERATOR_CONTRACTS.items():
        if operator_id not in legacy_ids:
            continue
        assert contract.semantic_version == "2.0"
        assert contract.prompt_version
        assert contract.applicability_version
        assert contract.validation_policy_version == "operator_validation_v2"
        assert contract.operator_payload_schema["additionalProperties"] is True
        assert contract.release_checks


def test_candidate_envelope_freezes_versions_hashes_and_unknown_payload_fields():
    envelope = build_candidate_envelope(
        o13_evolved_output(),
        operator_id="O13_minimal_disqualifier",
        source_record=source_item(),
        operator_manifest=source_item()["operator_manifest"],
    )
    assert validate_candidate_envelope(envelope) == []
    assert envelope["operator_payload"]["future_unknown_field"] == {"kept": True}
    assert envelope["answer_contract"]["future_answer_field"] == "retained"
    assert envelope["answer_contract"]["operator_answer"]["future_unknown_field"] == {"kept": True}
    assert len(envelope["recipe_hash"]) == 64
    assert len(envelope["answer_contract"]["answer_contract_hash"]) == 64

    schema_path = ROOT / "schemas" / "operator_envelope.schema.json"
    validate_instance(
        envelope,
        load_schema(schema_path),
        schema_dir=schema_path.parent,
    )


def test_envelope_is_persisted_end_to_end_in_question_evolution_metadata():
    item = source_item()
    evolved = enrich_evolution_result_with_operator(
        o13_evolved_output(),
        item,
        "O13_minimal_disqualifier",
    )
    record = make_evolved_record(item, evolved, 1.0, "fixture-model")
    metadata = record["meta_info"]["question_evolution_metadata"]

    assert metadata["operator_envelope"] == evolved["operator_envelope"]
    assert metadata["operator_payload"]["future_unknown_field"] == {"kept": True}
    assert metadata["answer_contract"]["frozen"] is True
    assert metadata["recipe_hash"] == evolved["operator_envelope"]["recipe_hash"]
    assert validate_candidate_envelope(metadata["operator_envelope"]) == []


def test_tampered_answer_contract_hash_is_rejected():
    envelope = build_candidate_envelope(
        o13_evolved_output(),
        operator_id="O13_minimal_disqualifier",
        source_record=source_item(),
        operator_manifest=source_item()["operator_manifest"],
    )
    envelope["answer_contract"]["answer_key"] = "tampered"
    assert "answer_contract_hash mismatch" in validate_candidate_envelope(envelope)
