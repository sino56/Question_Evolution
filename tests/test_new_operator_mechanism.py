import sys
from dataclasses import replace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from operator_contracts import (
    OPERATOR_CONTRACTS,
    QUALIFICATION_ONLY,
    build_candidate_envelope,
    get_operator_contract,
    validate_candidate_envelope,
    validate_contract_registry,
    validate_operator_payload,
)
from scene_adapters import (
    SCENE_ADAPTERS,
    adapt_scene_record,
    validate_adapter_output,
)
from schema_validation import load_schema, validate_instance
from gen_rubric import (
    validate_and_normalize_rubric,
    validate_rubric_axis_mapping,
)
from scoring import aggregate_axis_scores
from analyze_evolution_effect import build_effect_analysis


O28 = "O28_multihop_chain_closure"
O33 = "O33_cross_modal_support_boundary"


def _payload(operator_id):
    schema = get_operator_contract(operator_id).operator_payload_schema
    placeholders = {
        "array": ["value"],
        "object": {"value": True},
        "string": "supported",
        "boolean": True,
        "number": 1,
    }
    return {
        field: placeholders[schema["properties"][field]]
        for field in schema["required"]
    }


def _source(operator_id):
    contract = get_operator_contract(operator_id)
    manifest = {"human_confirmed": True, "adapter_version": "1.0"}
    for slot in contract.required_fact_slots:
        current = manifest
        parts = slot.split(".")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = ["F1"] if parts[-1].endswith("s") else "F1"
    return {
        "sample_id": f"source-{operator_id}",
        "candidate_group_id": f"group-{operator_id}",
        "candidate_id": f"group-{operator_id}::cand_1",
        "prompt": "原题",
        "fact_ledger": [
            {"fact_id": "F1", "fact_type": "observed", "text": "可观察事实"}
        ],
        "operator_manifest": manifest,
    }


def _evolved(operator_id, *, manual_review_record=None):
    payload = _payload(operator_id)
    contract = get_operator_contract(operator_id)
    transform = contract.transformation_contract["allowed_transforms"][0]
    return {
        "candidate_group_id": f"group-{operator_id}",
        "candidate_id": f"group-{operator_id}::cand_1",
        "target_claim": {"claim_id": "C1", "text": "目标判断"},
        "conclusion_layer": "evidence_support",
        "surface_fact_ids": ["F1"],
        "applied_transforms": [transform],
        "operator_payload": payload,
        "surface_leakage_risks": {},
        "answer_contract": {
            "answer_key": {"result": "supported"},
            "decisive_fact_ids": ["F1"],
            "rubric_assertions": ["结论不超过证据支持层"],
        },
        "manual_review_record": manual_review_record or {},
    }


def test_o19_o33_have_versioned_qualification_contracts_and_payloads():
    for number in range(19, 34):
        operator_id = next(
            key for key in OPERATOR_CONTRACTS if key.startswith(f"O{number}_")
        )
        contract = get_operator_contract(operator_id)
        assert contract.status == QUALIFICATION_ONLY
        assert contract.semantic_version == "1.0"
        assert contract.prompt_version == f"o{number}_prompt_v1"
        assert contract.applicability_version == f"o{number}_applicability_v1"
        assert contract.ability_axes
        assert contract.required_fact_slots
        assert contract.operator_payload_schema["additionalProperties"] is True
        assert contract.answer_contract_schema["additionalProperties"] is True
        assert contract.scorer_mapping["per_axis_attribution_required"] is True


def test_o28_payload_envelope_replays_unknown_fields_and_ignores_manual_review():
    source = _source(O28)
    evolved = _evolved(
        O28,
        manual_review_record={
            "review_targets": ["链路断点"],
            "notes": ["仅供人工查阅"],
            "reviewer": "reviewer-a",
            "reviewed_at": "2026-07-25",
        },
    )
    evolved["operator_payload"]["future_extension"] = {"kept": True}
    envelope = build_candidate_envelope(
        evolved,
        operator_id=O28,
        source_record=source,
        operator_manifest=source["operator_manifest"],
    )
    assert validate_candidate_envelope(envelope) == []
    assert envelope["selected_operator_id"] == O28
    assert envelope["operator_payload"]["future_extension"] == {"kept": True}
    assert envelope["status"] == QUALIFICATION_ONLY
    assert envelope["adapter_version"] == "1.0"

    changed = _evolved(
        O28,
        manual_review_record={"notes": ["内容完全不同且含 F404"], "reviewer": None},
    )
    changed_envelope = build_candidate_envelope(
        changed,
        operator_id=O28,
        source_record=source,
        operator_manifest=source["operator_manifest"],
    )
    assert validate_candidate_envelope(changed_envelope) == []

    schema_path = ROOT / "schemas" / "operator_envelope.schema.json"
    validate_instance(
        envelope,
        load_schema(schema_path),
        schema_dir=schema_path.parent,
    )


def test_multi_axis_candidate_freezes_independent_contracts_and_interaction():
    source = _source(O33)
    evolved = _evolved(O33)
    payload = evolved["operator_payload"]
    evolved["axis_assignments"] = [
        {
            "axis_id": "axis_scope",
            "semantic_ability_axis": "source_scope_alignment",
            "source_fact_ids": ["F1"],
            "target_claim": {"claim_id": "C1"},
            "operator_payload": payload,
            "rubric_item_ids": ["R1"],
        },
        {
            "axis_id": "axis_fusion",
            "semantic_ability_axis": "cross_source_fusion_ceiling",
            "source_fact_ids": ["F1"],
            "target_claim": {"claim_id": "C1"},
            "operator_payload": payload,
            "rubric_item_ids": ["R2"],
        },
    ]
    evolved["axis_answer_contracts"] = {
        "axis_scope": {
            "answer_key": {"scope_aligned": True},
            "decisive_fact_ids": ["F1"],
            "rubric_assertions": ["检查来源范围"],
        },
        "axis_fusion": {
            "answer_key": {"max_layer": "investigative_lead"},
            "decisive_fact_ids": ["F1"],
            "rubric_assertions": ["限制融合结论上限"],
        },
    }
    evolved["axis_interactions"] = [
        {
            "source_axis_id": "axis_scope",
            "target_axis_id": "axis_fusion",
            "relation": "constrains_fusion",
            "interaction_contract_id": "scope-before-fusion-v1",
        }
    ]
    envelope = build_candidate_envelope(
        evolved,
        operator_id=O33,
        source_record=source,
        operator_manifest=source["operator_manifest"],
    )
    assert validate_candidate_envelope(envelope) == []
    assert set(envelope["axis_answer_contracts"]) == {
        "axis_scope",
        "axis_fusion",
    }
    assert len(
        {
            axis["answer_contract_id"]
            for axis in envelope["axis_assignments"]
        }
    ) == 2


def test_registry_detects_semantic_collision_without_reusing_stable_ids():
    base = get_operator_contract(O28)
    collision = replace(
        base,
        operator_id="O34_collision_fixture",
    )
    findings = validate_contract_registry(
        {O28: base, collision.operator_id: collision}
    )
    assert any("semantic collision" in finding for finding in findings)


def test_scene_adapters_are_versioned_and_cannot_select_or_answer_for_operator():
    assert len(SCENE_ADAPTERS) == 4
    for key, adapter in SCENE_ADAPTERS.items():
        output = adapt_scene_record(
            {
                "entities": [{"entity_id": "E1"}],
                "objects": [],
                "actions": [{"fact_id": "F1"}],
                "observation_nodes": [{"node_id": "N1"}],
                "source_modalities": ["video"],
                "time_and_path_constraints": {},
                "candidate_claims": [{"claim_id": "C1"}],
                "fact_ids_by_observable": {"N1": ["F1"]},
            },
            key,
        )
        assert validate_adapter_output(output) == []
        assert output["adapter_version"] == adapter.adapter_version
        assert "selected_operator_id" not in output
        assert "operator_payload" not in output
        assert "answer_key" not in output
        schema_path = ROOT / "schemas" / "scene_adapter.schema.json"
        validate_instance(
            output,
            load_schema(schema_path),
            schema_dir=schema_path.parent,
        )


def test_missing_required_payload_field_is_not_applicable_to_contract():
    payload = _payload(O33)
    payload.pop("time_alignment")
    assert validate_operator_payload(O33, payload) == [
        "operator_payload missing required field: time_alignment"
    ]


def test_scorer_mapping_preserves_per_axis_rubric_and_effect_attribution():
    rubric = validate_and_normalize_rubric(
        [
            {
                "title": "来源范围",
                "description": "检查范围对齐。",
                "weight": 4,
                "operator_axis_id": "axis_scope",
                "answer_contract_id": "hash-scope",
            },
            {
                "title": "融合上限",
                "description": "检查结论上限。",
                "weight": 6,
                "operator_axis_id": "axis_fusion",
                "answer_contract_id": "hash-fusion",
            },
        ]
    )
    scorer_mapping = {
        "per_axis_attribution_required": True,
        "axis_assignments": [
            {
                "axis_id": "axis_scope",
                "answer_contract_id": "hash-scope",
            },
            {
                "axis_id": "axis_fusion",
                "answer_contract_id": "hash-fusion",
            },
        ],
        "axis_answer_contracts": {
            "axis_scope": {},
            "axis_fusion": {},
        },
    }
    assert validate_rubric_axis_mapping(
        rubric,
        scorer_mapping,
    )["status"] == "axis_aligned"

    scores = aggregate_axis_scores(
        [
            {
                **rubric[0],
                "awarded": 2,
                "brief_reason": "",
            },
            {
                **rubric[1],
                "awarded": 3,
                "brief_reason": "",
            },
        ]
    )
    assert scores["axis_scope"]["score_rate"] == 0.5
    assert scores["axis_fusion"]["score_rate"] == 0.5

    previous = {
        "sample_id": "axis-effect",
        "prompt": "old",
        "score_rate": 0.9,
        "scoring_result": {
            "candidate_answer": "old",
            "axis_scores": {
                "axis_scope": {
                    "score_rate": 1.0,
                    "answer_contract_ids": ["hash-scope"],
                }
            },
        },
    }
    current = {
        "sample_id": "axis-effect",
        "prompt": "new",
        "question_evolved": True,
        "score_rate": 0.5,
        "validation_result": {"passed": True},
        "scoring_result": {
            "candidate_answer": "new",
            "axis_scores": scores,
        },
    }
    effect = build_effect_analysis(current, previous)
    assert effect["axis_effects"]["axis_scope"]["delta_score_rate"] == -0.5
    assert effect["candidate_total_is_supplementary"] is True
