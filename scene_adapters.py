"""Versioned four-scenario adapters for O28-O33 qualification data.

Adapters normalize business metadata only.  They never select an operator,
produce an answer, define an error taxonomy, or mutate an operator payload.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Sequence


ADAPTER_FORBIDDEN_FIELDS = {
    "operator_id",
    "selected_operator_id",
    "answer",
    "answer_key",
    "target_error_taxonomy",
    "operator_payload",
    "qualification_status",
}


@dataclass(frozen=True)
class SceneAdapter:
    adapter_id: str
    adapter_version: str
    business_surface: str
    allowed_expressions: Sequence[str]
    unsupported_claim_types: Sequence[str]
    default_claim_layer_ceiling: str = "investigative_lead"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


SCENE_ADAPTERS: Dict[str, SceneAdapter] = {
    "livestream_role_video_v1": SceneAdapter(
        adapter_id="livestream_role_video",
        adapter_version="1.0",
        business_surface="涉黄直播线索",
        allowed_expressions=("人员与设备出入", "重复关系节点", "信号覆盖范围", "视频可观察动作"),
        unsupported_claim_types=("confirmed_identity", "confirmed_illegal_role", "confirmed_offense"),
    ),
    "nitrous_oxide_clue_video_v1": SceneAdapter(
        adapter_id="nitrous_oxide_clue_video",
        adapter_version="1.0",
        business_surface="笑气线索",
        allowed_expressions=("容器外观", "搬运与充气动作", "地点与时间记录", "可观察转移"),
        unsupported_claim_types=("confirmed_substance", "confirmed_illegal_trade", "confirmed_identity"),
    ),
    "door_handle_theft_video_v1": SceneAdapter(
        adapter_id="door_handle_theft_video",
        adapter_version="1.0",
        business_surface="拉车门线索",
        allowed_expressions=("连续动作", "车辆目标选择", "多摄像头路径", "离场轨迹"),
        unsupported_claim_types=("confirmed_identity", "confirmed_theft", "confirmed_intent"),
    ),
    "electric_bike_coordination_video_v1": SceneAdapter(
        adapter_id="electric_bike_coordination_video",
        adapter_version="1.0",
        business_surface="电动车协同线索",
        allowed_expressions=("同步出现", "等待与汇合", "车辆轨迹", "相对位置变化"),
        unsupported_claim_types=("confirmed_identity", "confirmed_coordination_role", "confirmed_offense"),
    ),
}


def get_scene_adapter(adapter_key: str) -> SceneAdapter:
    try:
        return SCENE_ADAPTERS[adapter_key]
    except KeyError as exc:
        raise ValueError(f"unknown scene adapter: {adapter_key}") from exc


def validate_adapter_output(output: Any) -> list[str]:
    if not isinstance(output, Mapping):
        return ["adapter output must be an object"]
    errors = []
    required = (
        "adapter_id",
        "adapter_version",
        "entities",
        "objects",
        "actions",
        "observation_nodes",
        "source_modalities",
        "time_and_path_constraints",
        "candidate_claims",
        "claim_layer_ceiling",
        "unsupported_claim_types",
        "fact_ids_by_observable",
    )
    for field in required:
        if field not in output:
            errors.append(f"adapter output missing required field: {field}")
    forbidden = sorted(ADAPTER_FORBIDDEN_FIELDS.intersection(output))
    if forbidden:
        errors.append(
            "adapter output contains mechanism fields: " + ", ".join(forbidden)
        )
    for field in (
        "entities",
        "objects",
        "actions",
        "observation_nodes",
        "source_modalities",
        "candidate_claims",
        "unsupported_claim_types",
    ):
        if field in output and not isinstance(output.get(field), list):
            errors.append(f"adapter output.{field} must be an array")
    if "fact_ids_by_observable" in output and not isinstance(
        output.get("fact_ids_by_observable"), Mapping
    ):
        errors.append("adapter output.fact_ids_by_observable must be an object")
    return errors


def adapt_scene_record(
    record: Mapping[str, Any],
    adapter_key: str,
) -> Dict[str, Any]:
    """Normalize a business record while preserving its observable fact IDs."""

    adapter = get_scene_adapter(adapter_key)
    output = {
        "adapter_id": adapter.adapter_id,
        "adapter_version": adapter.adapter_version,
        "business_surface": adapter.business_surface,
        "entities": list(record.get("entities") or []),
        "objects": list(record.get("objects") or []),
        "actions": list(record.get("actions") or []),
        "observation_nodes": list(record.get("observation_nodes") or []),
        "source_modalities": list(record.get("source_modalities") or []),
        "time_and_path_constraints": dict(
            record.get("time_and_path_constraints") or {}
        ),
        "candidate_claims": list(record.get("candidate_claims") or []),
        "claim_layer_ceiling": str(
            record.get("claim_layer_ceiling")
            or adapter.default_claim_layer_ceiling
        ),
        "unsupported_claim_types": list(adapter.unsupported_claim_types),
        "allowed_expressions": list(adapter.allowed_expressions),
        "fact_ids_by_observable": dict(
            record.get("fact_ids_by_observable") or {}
        ),
    }
    errors = validate_adapter_output(output)
    if errors:
        raise ValueError("; ".join(errors))
    return output
