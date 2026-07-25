import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from new_operator_qualification_data import (
    FOUR_SCENE_DEVELOPMENT_SAMPLE_IDS,
    build_all_templates,
    minimum_per_split,
    new_operator_ids,
    validate_qualification_templates,
)
from operator_lifecycle import (
    build_rollback_record,
    evaluate_lifecycle_transition,
)
from operator_qualification import (
    CONFIRMED,
    INSUFFICIENT,
    evaluate_forced_qualification,
    evaluate_natural_routing,
)
from operator_contracts import get_operator_contract
from question_evolution import OperatorSpaceExhaustedError, resolve_operator_plan
from update_sample_state import classify_memory_entries
from capability_gap_pool import extract_capability_gap_records


O28 = "O28_multihop_chain_closure"


def _qualification_record(index, *, include_new_metrics=True):
    qualification = {
        "answer_unique_and_rubric_consistent": True,
        "no_surface_leakage": True,
        "parent_obligations_preserved": True,
        "required_reasoning_observable": True,
        "non_isomorphic_to_adjacent": True,
        "neighbor_attribution_correct": True,
        "target_error_taxonomy_hit": True,
        "manual_boundary_confirmed": True,
        "semantic_direction": "chain_not_closed",
    }
    if include_new_metrics:
        qualification.update(
            {
                "required_slots_complete": True,
                "operator_payload_replayable": True,
                "gold_answer_contract_consistent": True,
                "content_controls_consistent": True,
                "adapter_semantics_preserved": True,
                "manual_review_record_ignored": True,
            }
        )
    return {
        "sample_id": f"forced-o28-{index}",
        "candidate_operator": O28,
        "qualification_manifest": {"human_confirmed": True},
        "validation_result": {"passed": True},
        "qualification": qualification,
        "effect_analysis": {
            "score_rate_before": 1.0,
            "score_rate_after": 0.5,
        },
    }


def test_annotation_templates_cover_splits_surfaces_controls_without_fake_evidence():
    records = build_all_templates()
    assert validate_qualification_templates(records) == []
    assert not (
        {record["sample_id"] for record in records}
        & FOUR_SCENE_DEVELOPMENT_SAMPLE_IDS
    )
    for operator_id in new_operator_ids():
        matching = [
            record for record in records
            if record["operator_family"] == operator_id
        ]
        minimum = minimum_per_split(operator_id)
        assert sum(
            record["dataset_split"] == "development"
            for record in matching
        ) == minimum
        assert sum(
            record["dataset_split"] == "qualification_holdout"
            for record in matching
        ) == minimum
        assert len({record["business_surface"] for record in matching}) >= 2
        assert all(
            record["qualification_manifest"]["human_confirmed"] is False
            for record in matching
        )


def test_new_operator_forced_qualification_requires_mechanism_metrics():
    missing = evaluate_forced_qualification(
        [_qualification_record(index, include_new_metrics=False) for index in range(8)],
        O28,
    )
    assert missing["qualification_decision"] == INSUFFICIENT
    assert missing["minimum_records_required"] == 8
    assert missing["manual_review_record_used_for_decision"] is False

    complete = evaluate_forced_qualification(
        [_qualification_record(index) for index in range(8)],
        O28,
        qualification_run_id="forced-o28-v1",
    )
    assert complete["qualification_decision"] == CONFIRMED
    assert (
        complete["recommended_contract_status"]
        == "eligible_for_natural_routing_holdout"
    )


def test_natural_routing_uses_shadow_recognition_and_reports_confusion():
    records = [
        {
            "expected_operator_id": O28,
            "operator_route": {
                "primary_operator": "O10_evidence_sufficiency_ladder",
                "recognized_operator_id": O28,
            },
        },
        {
            "expected_operator_id": O28,
            "operator_route": {
                "primary_operator": "O10_evidence_sufficiency_ladder",
                "recognized_operator_id": "O20_multistage_event_breakpoint",
            },
        },
    ]
    skipped = evaluate_natural_routing(
        records,
        qualified_operator_ids=[],
    )
    assert skipped["counts"]["unqualified_operator_records_skipped"] == 2

    report = evaluate_natural_routing(
        records,
        qualified_operator_ids=[O28],
    )
    assert report["counts"]["correct_route"] == 1
    assert report["counts"]["wrong_route"] == 1
    assert report["routing_accuracy"] == 0.5
    assert report["confusion_matrix"][O28][O28] == 1
    assert (
        report["per_operator_precision_recall"][O28]["recall"]
        == 0.5
    )


def test_lifecycle_requires_forced_then_natural_evidence_and_supports_rollback():
    forced = evaluate_forced_qualification(
        [_qualification_record(index) for index in range(8)],
        O28,
    )
    blocked = evaluate_lifecycle_transition(
        O28,
        current_status="qualification_only",
        target_status="shadow_routing",
    )
    assert blocked["allowed"] is False

    shadow = evaluate_lifecycle_transition(
        O28,
        current_status="qualification_only",
        target_status="shadow_routing",
        forced_report=forced,
    )
    assert shadow["allowed"] is True
    assert shadow["applied"] is False

    natural = evaluate_natural_routing(
        [
            {
                "expected_operator_id": O28,
                "operator_route": {"recognized_operator_id": O28},
            }
        ],
        qualified_operator_ids=[O28],
    )
    enabled = evaluate_lifecycle_transition(
        O28,
        current_status="shadow_routing",
        target_status="enabled",
        forced_report=forced,
        natural_report=natural,
    )
    assert enabled["allowed"] is True
    assert enabled["applied"] is False

    rollback = build_rollback_record(
        O28,
        from_status="enabled",
        to_status="qualification_only",
        failure_reason="contract replay regression",
        rollback_date="2026-07-25",
    )
    assert rollback["history_preserved"] is True
    assert rollback["semantic_version"] == "1.0"


def test_qualification_only_operator_never_writes_formal_positive_memory():
    operator_entries, failure_entries, invalid_entries = classify_memory_entries(
        [
            {
                "sample_id": "new-memory-guard",
                "question_evolved": True,
                "effect_analysis": {
                    "effect_label": "effective_boundary_probe",
                    "complexity_passed": True,
                    "operator_used": O28,
                },
            }
        ]
    )
    assert operator_entries == []
    assert failure_entries == []
    assert invalid_entries == []


def test_forced_qualification_keeps_one_selected_operator_and_never_falls_back():
    item = {
        "sample_id": "forced-plan-o28",
        "evolution_action": "evolve_high_score_overscore",
        "operator_route": {
            "primary_operator": "O10_evidence_sufficiency_ladder",
            "backup_operators": ["O13_minimal_disqualifier"],
            "avoid_operators": [],
        },
    }
    try:
        resolve_operator_plan(
            item,
            2,
            strict_contracts=True,
            qualification_operator_id=O28,
        )
    except OperatorSpaceExhaustedError as exc:
        assert [attempt["operator_id"] for attempt in exc.attempts] == [O28]
    else:
        raise AssertionError("missing O28 slots must stop instead of fallback")

    manifest = {
        "human_confirmed": True,
        "source_manifest_id": "source-manifest-o28",
    }
    for slot in get_operator_contract(O28).required_fact_slots:
        current = manifest
        parts = slot.split(".")
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = "confirmed"
    item["meta_info"] = {"operator_manifests": {O28: manifest}}
    plan = resolve_operator_plan(
        item,
        2,
        strict_contracts=True,
        qualification_operator_id=O28,
    )
    assert plan["operator_ids"] == [O28]
    assert plan["qualification_mode"] is True
    assert plan["fallback_disabled"] is True


def test_recognized_new_family_can_be_exported_to_isolated_capability_gap_pool():
    records = extract_capability_gap_records(
        [
            {
                "sample_id": "gap-o28",
                "operator_route": {
                    "recognized_operator_id": O28,
                    "primary_reasoning_object": "跨节点整体链路",
                    "required_slots_satisfied": ["event_nodes"],
                    "missing_required_slots": ["required_edges"],
                    "supporting_fact_ids": ["F1"],
                    "adapter_version": "1.0",
                },
            },
            {
                "sample_id": "legacy",
                "operator_route": {
                    "recognized_operator_id": "O13_minimal_disqualifier"
                },
            },
        ]
    )
    assert len(records) == 1
    assert records[0]["capability_gap"]["operator_family"] == O28
    assert records[0]["capability_gap"]["formal_memory_eligible"] is False
