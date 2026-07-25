"""Build non-evidentiary annotation templates for O19-O33 qualification.

The generated records reserve splits, controls, business surfaces, neighboring
families and required fields.  They are intentionally marked unconfirmed and
cannot be counted as forced-qualification evidence until facts, gold structure,
answer contracts and review results are supplied by the real data workflow.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from operator_contracts import get_operator_contract
from prompts.operators import OPERATOR_SPECS


FOUR_SCENE_DEVELOPMENT_SAMPLE_IDS = {
    "23904",
    "28996",
    "17815",
    "30155",
    "34455",
    "27965",
    "35211",
    "32575",
    "18436",
    "21725",
    "30470",
    "24804",
}

SURFACES = {
    19: ("人员协同", "跨镜头车辆角色"),
    20: ("物品转移", "交通事件"),
    21: ("相似包装转移", "容器遮挡重现"),
    22: ("多摄像头路径", "多入口区域"),
    23: ("夜间身份观察", "模糊物品观察"),
    24: ("行为解释竞争", "来源解释竞争"),
    25: ("现场坐标记录", "软件标注映射"),
    26: ("通行时间区间", "摄像头时间偏差"),
    27: ("证据支持层级", "事实与行动门槛"),
    28: ("拉车门多镜头链", "电动车跨节点链"),
    29: ("笑气容器同一性", "拉车门人员同一性"),
    30: ("笑气下一观测", "电动车协同判别"),
    31: ("拉车门重复观测", "涉黄出入累积"),
    32: ("电动车角色关系", "涉黄接送关系"),
    33: ("笑气视频与时间记录", "拉车门多摄像头来源"),
}

SPECIAL_CONTROLS = {
    28: (
        "local_closed_global_missing",
        "missing_vs_counterevidence",
        "cross_node_binding_conflict",
        "reachable_but_time_incompatible",
        "surface_swap",
    ),
    29: (
        "appearance_similar_spacetime_conflict",
        "local_binding_not_global_identity",
        "person_continuous_object_gap",
        "conflict_resolution_ablation",
        "surface_swap",
    ),
    30: (
        "suspicious_not_discriminative",
        "tied_best_observations",
        "no_unique_discriminator",
        "best_unavailable",
        "surface_swap",
    ),
    31: (
        "same_source_repetition",
        "independent_new_feature",
        "repeated_low_quality",
        "dependency_ablation",
        "support_up_ceiling_unchanged",
        "surface_swap",
    ),
    32: (
        "cooccurrence_not_coordination",
        "direction_reversal",
        "noncritical_edge_ablation",
        "critical_edge_ablation",
        "surface_swap",
    ),
    33: (
        "same_surface_different_entity",
        "same_entity_time_misaligned",
        "complementary_sources",
        "source_added_ceiling_unchanged",
        "source_quality_ablation",
        "surface_swap",
    ),
}


def new_operator_ids() -> List[str]:
    return [
        operator_id
        for operator_id in OPERATOR_SPECS
        if 19 <= int(operator_id[1:].split("_", 1)[0]) <= 33
    ]


def minimum_per_split(operator_id: str) -> int:
    number = int(operator_id[1:].split("_", 1)[0])
    return 8 if number >= 28 else 6


def controls_for_operator(operator_id: str) -> Sequence[str]:
    number = int(operator_id[1:].split("_", 1)[0])
    if number in SPECIAL_CONTROLS:
        return SPECIAL_CONTROLS[number]
    return tuple(
        f"content_control_{index + 1}"
        for index, _ in enumerate(
            OPERATOR_SPECS[operator_id].content_controls
        )
    )


def build_operator_templates(operator_id: str) -> List[Dict[str, Any]]:
    contract = get_operator_contract(operator_id)
    number = int(operator_id[1:].split("_", 1)[0])
    controls = controls_for_operator(operator_id)
    surfaces = SURFACES[number]
    count = minimum_per_split(operator_id)
    records = []
    for split in ("development", "qualification_holdout"):
        for index in range(count):
            control = controls[index % len(controls)]
            sample_id = f"template-{number}-{split}-{index + 1:02d}"
            records.append(
                {
                    "sample_id": sample_id,
                    "operator_family": operator_id,
                    "dataset_split": split,
                    "business_surface": surfaces[index % len(surfaces)],
                    "control_type": control,
                    "fact_ledger": [],
                    "required_slots": {
                        slot: None for slot in contract.required_fact_slots
                    },
                    "target_ability_axes": list(contract.ability_axes),
                    "target_error_taxonomy": list(
                        contract.target_error_taxonomy
                    ),
                    "gold_structure": {},
                    "answer_contract": {},
                    "neighbor_operator_labels": list(
                        contract.neighbor_operators
                    ),
                    "adapter_id": None,
                    "adapter_version": None,
                    "qualification_manifest": {
                        "human_confirmed": False,
                        "template_only": True,
                        "source_manifest_required": True,
                        "holdout_leakage_group": sample_id,
                    },
                    "manual_review_record": {
                        "review_targets": [],
                        "notes": [],
                        "reviewer": None,
                        "reviewed_at": None,
                    },
                }
            )
    return records


def build_all_templates() -> List[Dict[str, Any]]:
    records = []
    for operator_id in new_operator_ids():
        records.extend(build_operator_templates(operator_id))
    return records


def validate_qualification_templates(
    records: Sequence[Mapping[str, Any]],
) -> List[str]:
    errors = []
    by_operator_split: Dict[str, Counter] = defaultdict(Counter)
    surfaces: Dict[str, set] = defaultdict(set)
    controls: Dict[str, set] = defaultdict(set)
    sample_ids = set()
    for record in records:
        sample_id = str(record.get("sample_id") or "")
        operator_id = str(record.get("operator_family") or "")
        split = str(record.get("dataset_split") or "")
        if not sample_id or sample_id in sample_ids:
            errors.append(f"duplicate or empty sample_id: {sample_id}")
        sample_ids.add(sample_id)
        if operator_id not in set(new_operator_ids()):
            errors.append(f"unknown new operator family: {operator_id}")
            continue
        by_operator_split[operator_id][split] += 1
        surfaces[operator_id].add(record.get("business_surface"))
        controls[operator_id].add(record.get("control_type"))
        manifest = record.get("qualification_manifest")
        if not isinstance(manifest, Mapping):
            errors.append(f"{sample_id} missing qualification_manifest")
        elif manifest.get("human_confirmed") is not False:
            errors.append(f"{sample_id} template must not claim confirmation")
        if (
            split == "qualification_holdout"
            and sample_id in FOUR_SCENE_DEVELOPMENT_SAMPLE_IDS
        ):
            errors.append(f"development sample leaked into holdout: {sample_id}")
    for operator_id in new_operator_ids():
        minimum = minimum_per_split(operator_id)
        for split in ("development", "qualification_holdout"):
            if by_operator_split[operator_id][split] < minimum:
                errors.append(
                    f"{operator_id} has fewer than {minimum} {split} templates"
                )
        if len(surfaces[operator_id]) < 2:
            errors.append(f"{operator_id} has fewer than two business surfaces")
        if not set(controls_for_operator(operator_id)) <= controls[operator_id]:
            errors.append(f"{operator_id} does not cover all planned controls")
    return errors


def write_jsonl(records: Iterable[Mapping[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output:
        for record in records:
            output.write(json.dumps(record, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build unconfirmed O19-O33 qualification annotation templates."
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    records = build_all_templates()
    errors = validate_qualification_templates(records)
    if errors:
        raise ValueError("; ".join(errors))
    write_jsonl(records, Path(args.output))


if __name__ == "__main__":
    main()
