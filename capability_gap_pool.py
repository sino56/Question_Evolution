"""Extract new-operator capability-gap records from routed samples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from operator_contracts import collect_referenced_fact_ids, get_operator_contract
from pipeline_runtime import load_json_records


def build_capability_gap_record(
    record: Mapping[str, Any],
) -> Dict[str, Any] | None:
    route = record.get("operator_route")
    route = route if isinstance(route, Mapping) else {}
    operator_id = str(route.get("recognized_operator_id") or "").strip()
    if not operator_id:
        return None
    try:
        number = int(operator_id[1:].split("_", 1)[0])
        contract = get_operator_contract(operator_id)
    except (TypeError, ValueError):
        return None
    if not 19 <= number <= 33:
        return None
    result = dict(record)
    result["capability_gap"] = {
        "operator_family": operator_id,
        "semantic_version": contract.semantic_version,
        "applicability_version": contract.applicability_version,
        "qualification_status": contract.status,
        "primary_reasoning_object": route.get("primary_reasoning_object"),
        "required_slots_satisfied": list(
            route.get("required_slots_satisfied") or []
        ),
        "missing_required_slots": list(
            route.get("missing_required_slots") or []
        ),
        "supporting_fact_ids": list(
            route.get("supporting_fact_ids")
            or collect_referenced_fact_ids(record)
        ),
        "adapter_version": str(route.get("adapter_version") or ""),
        "formal_memory_eligible": False,
    }
    return result


def extract_capability_gap_records(
    records: Sequence[Mapping[str, Any]],
) -> list[Dict[str, Any]]:
    return [
        result
        for result in (
            build_capability_gap_record(record) for record in records
        )
        if result is not None
    ]


def write_jsonl(
    records: Iterable[Mapping[str, Any]],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract O19-O33 capability-gap records for dedicated data work."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    records = load_json_records(args.input, stage="capability_gap_pool")
    write_jsonl(
        extract_capability_gap_records(records),
        Path(args.output),
    )


if __name__ == "__main__":
    main()
