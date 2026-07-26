"""Historical operator yield-per-time statistics and stable candidate ranking."""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from pipeline_runtime import load_json_records


GROUP_FIELDS = ("scene", "sample_type", "core_capability")


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        text = _clean(value)
        if text and text not in result:
            result.append(text)
    return result


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """Accept flat historical rows and BranchArtifactStore envelopes."""

    nested = record.get("record")
    return nested if isinstance(nested, Mapping) else record


def _operator_id(record: Mapping[str, Any]) -> str:
    record = _payload(record)
    for field in ("operator_id", "candidate_operator", "operator_used"):
        value = _clean(record.get(field))
        if value:
            return value
    summary = record.get("branch_summary")
    if isinstance(summary, Mapping):
        return _clean(summary.get("operator_id"))
    return ""


def _group_value(record: Mapping[str, Any], field: str) -> str:
    record = _payload(record)
    value = _clean(record.get(field))
    if value:
        return value
    profile = record.get("sample_profile")
    if isinstance(profile, Mapping):
        return _clean(profile.get(field))
    return ""


def _duration_seconds(record: Mapping[str, Any]) -> float:
    record = _payload(record)
    for field in ("branch_duration_seconds", "duration_seconds", "wall_clock_seconds"):
        try:
            value = float(record.get(field))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    started = record.get("started_at")
    completed = record.get("completed_at")
    try:
        value = float(completed) - float(started)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    return 1.0


def _branch_status(record: Mapping[str, Any]) -> str:
    record = _payload(record)
    status = _clean(record.get("branch_status"))
    if status:
        return status
    summary = record.get("branch_summary")
    if isinstance(summary, Mapping):
        return _clean(summary.get("branch_status"))
    return ""


def _validation_passed(record: Mapping[str, Any]) -> bool:
    record = _payload(record)
    validation = record.get("validation_result")
    if isinstance(validation, Mapping):
        return validation.get("passed") is True
    return _branch_status(record) not in {
        "validation_failed",
        "duplicate_exhausted",
        "not_applicable",
        "branch_error",
    }


def _was_scored(record: Mapping[str, Any]) -> bool:
    record = _payload(record)
    return _branch_status(record) in {
        "boundary_candidate",
        "no_score_change",
        "score_increased",
    } or record.get("decision_evaluation_status") == "completed"


def _aggregate(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    attempts = len(rows)
    boundary_count = sum(
        1 for row in rows if _branch_status(row) == "boundary_candidate"
    )
    validation_pass_count = sum(1 for row in rows if _validation_passed(row))
    scored_count = sum(1 for row in rows if _was_scored(row))
    error_count = sum(1 for row in rows if _branch_status(row) == "branch_error")
    total_duration = sum(_duration_seconds(row) for row in rows)
    average_duration = total_duration / attempts if attempts else 0.0
    return {
        "attempt_count": attempts,
        "boundary_candidate_count": boundary_count,
        "boundary_candidate_rate": boundary_count / attempts if attempts else 0.0,
        "validation_pass_count": validation_pass_count,
        "validation_pass_rate": validation_pass_count / attempts if attempts else 0.0,
        "scored_count": scored_count,
        "scored_rate": scored_count / attempts if attempts else 0.0,
        "error_count": error_count,
        "error_rate": error_count / attempts if attempts else 0.0,
        "total_duration_seconds": total_duration,
        "average_duration_seconds": average_duration,
        "boundary_candidates_per_hour": (
            boundary_count * 3600.0 / total_duration if total_duration > 0 else 0.0
        ),
    }


def build_operator_statistics(
    records: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    by_operator: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    by_group: Dict[Tuple[str, str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for record in records:
        operator_id = _operator_id(record)
        if not operator_id:
            continue
        by_operator[operator_id].append(record)
        for field in GROUP_FIELDS:
            group = _group_value(record, field)
            if group:
                by_group[(field, group, operator_id)].append(record)
    return {
        "format_version": 1,
        "operators": {
            operator_id: _aggregate(rows)
            for operator_id, rows in sorted(by_operator.items())
        },
        "groups": [
            {
                "group_field": field,
                "group_value": value,
                "operator_id": operator_id,
                **_aggregate(rows),
            }
            for (field, value, operator_id), rows in sorted(by_group.items())
        ],
    }


def _matching_stats(
    operator_id: str,
    statistics: Mapping[str, Any],
    sample_profile: Optional[Mapping[str, Any]],
) -> Mapping[str, Any]:
    if sample_profile:
        matching = [
            row
            for row in statistics.get("groups") or []
            if isinstance(row, Mapping)
            and row.get("operator_id") == operator_id
            and _clean(sample_profile.get(_clean(row.get("group_field"))))
            == _clean(row.get("group_value"))
        ]
        if matching:
            return max(
                matching,
                key=lambda row: int(row.get("attempt_count") or 0),
            )
    operators = statistics.get("operators")
    if isinstance(operators, Mapping):
        row = operators.get(operator_id)
        if isinstance(row, Mapping):
            return row
    return {}


def _yield_score(stats: Mapping[str, Any], exploration_ratio: float) -> float:
    attempts = max(0, int(stats.get("attempt_count") or 0))
    boundaries = max(0, int(stats.get("boundary_candidate_count") or 0))
    duration = max(1e-6, float(stats.get("average_duration_seconds") or 1.0))
    error_rate = min(1.0, max(0.0, float(stats.get("error_rate") or 0.0)))
    # Beta(1, 1) prior prevents sparse history from permanently suppressing a
    # new operator.  The explicit exploration term is deterministic.
    expected_boundary_probability = (boundaries + 1.0) / (attempts + 2.0)
    exploration_bonus = exploration_ratio / math.sqrt(attempts + 1.0)
    return (expected_boundary_probability + exploration_bonus) * (1.0 - error_rate) / duration


def rank_selected_operators(
    selected_operator_ids: Sequence[str],
    *,
    primary_operator: str = "",
    backup_operators: Sequence[str] = (),
    statistics: Optional[Mapping[str, Any]] = None,
    sample_profile: Optional[Mapping[str, Any]] = None,
    exploration_ratio: float = 0.1,
) -> List[str]:
    selected = _unique(selected_operator_ids)
    protected = [
        operator_id
        for operator_id in _unique([primary_operator, *backup_operators])
        if operator_id in selected
    ]
    remaining = [operator_id for operator_id in selected if operator_id not in protected]
    if not statistics or not remaining:
        return protected + remaining

    scored = []
    for original_index, operator_id in enumerate(remaining):
        stats = _matching_stats(operator_id, statistics, sample_profile)
        scored.append(
            {
                "operator_id": operator_id,
                "stats": stats,
                "score": _yield_score(stats, max(0.0, exploration_ratio)),
                "original_index": original_index,
                "attempt_count": int(stats.get("attempt_count") or 0),
            }
        )
    ranked: List[str] = []
    exploration_period = (
        max(1, round(1.0 / exploration_ratio))
        if exploration_ratio > 0
        else 0
    )
    position = 1
    while scored:
        if exploration_period and position % exploration_period == 0:
            chosen = min(
                scored,
                key=lambda row: (row["attempt_count"], row["original_index"]),
            )
        else:
            chosen = max(
                scored,
                key=lambda row: (row["score"], -row["original_index"]),
            )
        ranked.append(chosen["operator_id"])
        scored.remove(chosen)
        position += 1
    return protected + ranked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build operator yield-per-time statistics.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_json_records(args.input, stage="operator_statistics")
    result = build_operator_statistics(records)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
