"""Offline qualification reports for repaired operators.

Forced qualification consumes pre-generated, human-confirmed operator records
and evaluates prompt/contract quality without using Router correctness as a
success criterion.  Natural routing validation consumes a separate holdout and
measures routing after forced qualification has confirmed an operator.

This module never enables an operator by itself.  It emits an evidence decision
that can be reviewed and then reflected in a later contract-version change.
"""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from operator_contracts import get_operator_contract
from pipeline_runtime import load_json_records


CONFIRMED = "design_hypothesis_confirmed"
REFUTED = "design_hypothesis_refuted"
INSUFFICIENT = "evidence_insufficient"
QUALIFICATION_DECISIONS = {CONFIRMED, REFUTED, INSUFFICIENT}

DEFAULT_MIN_RECORDS = 5


def minimum_records_for_operator(operator_id: str) -> int:
    try:
        number = int(operator_id[1:].split("_", 1)[0])
    except (TypeError, ValueError):
        return DEFAULT_MIN_RECORDS
    if number >= 28:
        return 8
    if number >= 19:
        return 6
    return DEFAULT_MIN_RECORDS


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _bool_metric(record: Mapping[str, Any], field: str) -> Optional[bool]:
    qualification = record.get("qualification")
    qualification = qualification if isinstance(qualification, Mapping) else {}
    value = qualification.get(field)
    return value if isinstance(value, bool) else None


def _rate(values: Iterable[Optional[bool]]) -> Optional[float]:
    known = [value for value in values if value is not None]
    if not known:
        return None
    return round(sum(1 for value in known if value) / len(known), 4)


def _validation_pass(record: Mapping[str, Any]) -> Optional[bool]:
    validation = record.get("validation_result")
    if not isinstance(validation, Mapping):
        return None
    value = validation.get("passed")
    return value if isinstance(value, bool) else None


def _target_taxonomy_hit(record: Mapping[str, Any]) -> Optional[bool]:
    qualification = record.get("qualification")
    qualification = qualification if isinstance(qualification, Mapping) else {}
    hit = qualification.get("target_error_taxonomy_hit")
    if isinstance(hit, bool):
        return hit
    observed = qualification.get("observed_error_taxonomy")
    target = qualification.get("target_error_taxonomy")
    if isinstance(observed, str) and isinstance(target, list):
        return observed in target
    return None


def _score_drop(record: Mapping[str, Any]) -> Optional[bool]:
    effect = record.get("effect_analysis")
    if not isinstance(effect, Mapping):
        return None
    before = effect.get("score_rate_before")
    after = effect.get("score_rate_after")
    if not isinstance(before, (int, float)) or not isinstance(after, (int, float)):
        return None
    return after < before


def _direction(record: Mapping[str, Any]) -> str:
    qualification = record.get("qualification")
    qualification = qualification if isinstance(qualification, Mapping) else {}
    return _clean(qualification.get("semantic_direction"))


def _metric_bundle(records: Sequence[Mapping[str, Any]]) -> Dict[str, Optional[float]]:
    return {
        "fact_contract_pass_rate": _rate(_validation_pass(record) for record in records),
        "answer_uniqueness_rubric_consistency_rate": _rate(
            _bool_metric(record, "answer_unique_and_rubric_consistent")
            for record in records
        ),
        "no_surface_leakage_rate": _rate(
            _bool_metric(record, "no_surface_leakage")
            for record in records
        ),
        "parent_obligation_preservation_rate": _rate(
            _bool_metric(record, "parent_obligations_preserved")
            for record in records
        ),
        "required_reasoning_observable_rate": _rate(
            _bool_metric(record, "required_reasoning_observable")
            for record in records
        ),
        "non_isomorphic_to_adjacent_rate": _rate(
            _bool_metric(record, "non_isomorphic_to_adjacent")
            for record in records
        ),
        "neighbor_attribution_or_not_applicable_rate": _rate(
            _bool_metric(record, "neighbor_attribution_correct")
            for record in records
        ),
        "target_error_taxonomy_hit_rate": _rate(
            _target_taxonomy_hit(record)
            for record in records
        ),
        "manual_boundary_confirmation_rate": _rate(
            _bool_metric(record, "manual_boundary_confirmed")
            for record in records
        ),
        # Reported for analysis only; never used as the sole decision metric.
        "score_drop_rate": _rate(_score_drop(record) for record in records),
        "required_slot_complete_rate": _rate(
            _bool_metric(record, "required_slots_complete")
            for record in records
        ),
        "payload_replay_rate": _rate(
            _bool_metric(record, "operator_payload_replayable")
            for record in records
        ),
        "gold_answer_contract_consistency_rate": _rate(
            _bool_metric(record, "gold_answer_contract_consistent")
            for record in records
        ),
        "control_consistency_rate": _rate(
            _bool_metric(record, "content_controls_consistent")
            for record in records
        ),
        "adapter_semantics_preservation_rate": _rate(
            _bool_metric(record, "adapter_semantics_preserved")
            for record in records
        ),
        "manual_review_independence_rate": _rate(
            _bool_metric(record, "manual_review_record_ignored")
            for record in records
        ),
    }


def _decide_forced(
    record_count: int,
    metrics: Mapping[str, Optional[float]],
    *,
    min_records: int,
) -> str:
    if record_count < min_records:
        return INSUFFICIENT
    required = (
        "fact_contract_pass_rate",
        "answer_uniqueness_rubric_consistency_rate",
        "no_surface_leakage_rate",
        "parent_obligation_preservation_rate",
        "required_reasoning_observable_rate",
        "non_isomorphic_to_adjacent_rate",
        "target_error_taxonomy_hit_rate",
        "manual_boundary_confirmation_rate",
    )
    if any(metrics.get(field) is None for field in required):
        return INSUFFICIENT
    if (
        metrics["fact_contract_pass_rate"] < 0.9
        or metrics["answer_uniqueness_rubric_consistency_rate"] < 0.9
        or metrics["parent_obligation_preservation_rate"] < 0.8
        or metrics["required_reasoning_observable_rate"] < 0.8
        or metrics["manual_boundary_confirmation_rate"] < 0.8
    ):
        return REFUTED
    if (
        metrics["no_surface_leakage_rate"] >= 0.8
        and metrics["non_isomorphic_to_adjacent_rate"] >= 0.8
        and metrics["target_error_taxonomy_hit_rate"] >= 0.6
    ):
        return CONFIRMED
    return INSUFFICIENT


def evaluate_forced_qualification(
    records: Sequence[Mapping[str, Any]],
    operator_id: str,
    *,
    min_records: Optional[int] = None,
    qualification_run_id: str = "",
    memory_namespace: str = "isolated",
) -> Dict[str, Any]:
    contract = get_operator_contract(operator_id)
    matching = [
        record
        for record in records
        if _clean(
            record.get("candidate_operator")
            or record.get("operator_id")
            or (
                record.get("meta_info", {})
                .get("question_evolution_metadata", {})
                .get("operator_used")
                if isinstance(record.get("meta_info"), Mapping)
                else ""
            )
        )
        == operator_id
    ]
    manifest_confirmed = [
        record
        for record in matching
        if isinstance(record.get("qualification_manifest"), Mapping)
        and record["qualification_manifest"].get("human_confirmed") is True
    ]
    metrics = _metric_bundle(manifest_confirmed)
    directions = Counter(
        direction for direction in (_direction(record) for record in manifest_confirmed) if direction
    )
    required_minimum = (
        minimum_records_for_operator(operator_id)
        if min_records is None
        else max(1, min_records)
    )
    decision = _decide_forced(
        len(manifest_confirmed),
        metrics,
        min_records=required_minimum,
    )
    try:
        operator_number = int(operator_id[1:].split("_", 1)[0])
    except (TypeError, ValueError):
        operator_number = 0
    if operator_number >= 19 and decision == CONFIRMED:
        mechanism_metrics = (
            "required_slot_complete_rate",
            "payload_replay_rate",
            "gold_answer_contract_consistency_rate",
            "control_consistency_rate",
            "adapter_semantics_preservation_rate",
            "manual_review_independence_rate",
        )
        if any(metrics.get(field) is None for field in mechanism_metrics):
            decision = INSUFFICIENT
        elif any(float(metrics[field]) < 0.8 for field in mechanism_metrics):
            decision = REFUTED
    return {
        "mode": "forced_qualification",
        "qualification_run_id": qualification_run_id,
        "memory_namespace": memory_namespace,
        "memory_isolated": memory_namespace != "formal",
        "operator_id": operator_id,
        "semantic_version": contract.semantic_version,
        "prompt_version": contract.prompt_version,
        "applicability_version": contract.applicability_version,
        "validation_policy_version": contract.validation_policy_version,
        "evidence_status_before": contract.evidence_status,
        "records_received": len(records),
        "operator_records": len(matching),
        "human_confirmed_manifest_records": len(manifest_confirmed),
        "minimum_records_required": required_minimum,
        "metrics": metrics,
        "semantic_direction_distribution": dict(sorted(directions.items())),
        "qualification_decision": decision,
        "recommended_contract_status": (
            "eligible_for_natural_routing_holdout"
            if decision == CONFIRMED
            else (
                "remain_qualification_only"
                if operator_number >= 19
                else "remain_disabled_or_validation_only"
            )
        ),
        "router_results_used_for_decision": False,
        "score_drop_is_sole_success_metric": False,
        "manual_review_record_used_for_decision": False,
    }


def evaluate_natural_routing(
    records: Sequence[Mapping[str, Any]],
    *,
    qualification_run_id: str = "",
    qualified_operator_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    counts = Counter()
    per_operator: Dict[str, Counter] = {}
    confusion: Dict[str, Counter] = {}
    qualified_set = (
        set(qualified_operator_ids)
        if qualified_operator_ids is not None
        else None
    )
    for record in records:
        expected = _clean(
            record.get("expected_operator_id")
            or (
                record.get("qualification_manifest", {}).get("expected_operator_id")
                if isinstance(record.get("qualification_manifest"), Mapping)
                else ""
            )
        )
        route = record.get("operator_route")
        primary = _clean(route.get("primary_operator")) if isinstance(route, Mapping) else ""
        recognized = _clean(route.get("recognized_operator_id")) if isinstance(route, Mapping) else ""
        actual = recognized or primary
        applicability = _clean(record.get("expected_applicability") or "eligible")
        key = expected or "<none>"
        counter = per_operator.setdefault(key, Counter())
        if qualified_set is not None and expected and expected not in qualified_set:
            counts["unqualified_operator_records_skipped"] += 1
            counter["unqualified_operator_records_skipped"] += 1
            continue
        confusion.setdefault(key, Counter())[actual or "<none>"] += 1
        if applicability == "not_applicable":
            if not actual:
                counts["not_applicable_blocked"] += 1
                counter["not_applicable_blocked"] += 1
            else:
                counts["not_applicable_missed"] += 1
                counter["not_applicable_missed"] += 1
        elif actual == expected and expected:
            counts["correct_route"] += 1
            counter["correct_route"] += 1
        elif actual and actual != expected:
            counts["wrong_route"] += 1
            counter["wrong_route"] += 1
        else:
            counts["missed_route"] += 1
            counter["missed_route"] += 1

    eligible_total = counts["correct_route"] + counts["wrong_route"] + counts["missed_route"]
    not_applicable_total = counts["not_applicable_blocked"] + counts["not_applicable_missed"]
    fallback_distribution = Counter(
        _clean(record.get("operator_route", {}).get("primary_operator"))
        for record in records
        if isinstance(record.get("operator_route"), Mapping)
        and _clean(record.get("operator_route", {}).get("primary_operator"))
    )
    per_operator_metrics = {}
    expected_totals = Counter()
    predicted_totals = Counter()
    true_positives = Counter()
    for expected, row in confusion.items():
        for actual, count in row.items():
            expected_totals[expected] += count
            predicted_totals[actual] += count
            if expected == actual:
                true_positives[expected] += count
    for operator_id in sorted(set(expected_totals) | set(predicted_totals)):
        per_operator_metrics[operator_id] = {
            "precision": (
                round(true_positives[operator_id] / predicted_totals[operator_id], 4)
                if predicted_totals[operator_id]
                else None
            ),
            "recall": (
                round(true_positives[operator_id] / expected_totals[operator_id], 4)
                if expected_totals[operator_id]
                else None
            ),
        }
    return {
        "mode": "natural_routing_validation",
        "qualification_run_id": qualification_run_id,
        "records_received": len(records),
        "counts": dict(counts),
        "routing_accuracy": (
            round(counts["correct_route"] / eligible_total, 4)
            if eligible_total
            else None
        ),
        "not_applicable_interception_rate": (
            round(counts["not_applicable_blocked"] / not_applicable_total, 4)
            if not_applicable_total
            else None
        ),
        "per_operator": {
            operator_id: dict(counter)
            for operator_id, counter in sorted(per_operator.items())
        },
        "per_operator_precision_recall": per_operator_metrics,
        "confusion_matrix": {
            expected: dict(sorted(row.items()))
            for expected, row in sorted(confusion.items())
        },
        "primary_operator_distribution": dict(sorted(fallback_distribution.items())),
        "fallback_concentration_o10_o12": sum(
            count
            for operator_id, count in fallback_distribution.items()
            if operator_id
            in {
                "O10_evidence_sufficiency_ladder",
                "O12_conjunctive_necessity",
            }
        ),
    }


def write_json(data: Mapping[str, Any], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as output:
        json.dump(data, output, ensure_ascii=False, indent=2, sort_keys=True)
        output.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate forced operator qualification or natural routing holdout.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--mode", choices=("forced", "natural"), required=True)
    parser.add_argument("--operator-id", default=None)
    parser.add_argument("--min-records", type=int, default=None)
    parser.add_argument(
        "--qualified-operator-id",
        action="append",
        default=None,
        help="Operator allowed into natural routing evaluation after forced qualification.",
    )
    parser.add_argument("--qualification-run-id", default="")
    parser.add_argument("--memory-namespace", default="isolated")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_json_records(args.input, stage="operator_qualification")
    if args.mode == "forced":
        if not args.operator_id:
            raise ValueError("--operator-id is required for forced qualification")
        report = evaluate_forced_qualification(
            records,
            args.operator_id,
            min_records=args.min_records,
            qualification_run_id=args.qualification_run_id,
            memory_namespace=args.memory_namespace,
        )
    else:
        report = evaluate_natural_routing(
            records,
            qualification_run_id=args.qualification_run_id,
            qualified_operator_ids=args.qualified_operator_id,
        )
    write_json(report, args.output)


if __name__ == "__main__":
    main()
