import argparse
import asyncio
import hashlib
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from local_api_config import get_config_list, get_config_value
from operator_registry import runtime_policy
from operator_routing_cards import routing_card_gate
from pipeline_runtime import (
    StageMetrics,
    TraceStore,
    consume_model_request_budget,
    load_json_records,
    publish_records,
    sha256_file,
    stable_record_key,
    validate_published_artifact,
)
from prompts.operators import OPERATOR_SPECS
from prompts.router_prompt import build_router_prompt
from router_contract import (
    ROUTE_REVISION,
    ROUTER_PROMPT_VERSION,
    ROUTER_REGISTRY_POLICY_VERSION,
    ROUTER_TRANSPORT_POLICY_VERSION,
    ROUTING_SCHEMA_VERSION,
    ParsedRouterResponse,
    RouterContractError,
    parse_router_response,
)
from route_integrity import attach_live_route_integrity, validate_live_route_integrity
from governance import (
    analyze_source,
    operator_slot_assessment,
    resolve_evolution_authorization,
    resolve_evolution_mode,
)
from operator_execution_contracts import get_execution_contract

from select_evolution_candidates import (
    EVOLVE_HIGH_SCORE_OVERSCORE,
    PASS_THROUGH_OR_SCORING_NOISE,
    PROBE_MIDDLE_SCORE_BOUNDARY,
    RECONSTRUCT_LOW_SCORE_BOUNDARY,
    STOP_EVOLUTION,
    get_score_rate,
)


O10_EVIDENCE_SUFFICIENCY_LADDER = "O10_evidence_sufficiency_ladder"
O11_UNOBSERVED_STATE_ATTRIBUTION = "O11_unobserved_state_attribution"
O12_CONJUNCTIVE_NECESSITY = "O12_conjunctive_necessity"
O13_MINIMAL_DISQUALIFIER = "O13_minimal_disqualifier"
O14_INFORMATION_CLOSURE = "O14_information_closure"
O15_COUNTERFACTUAL_THRESHOLD_SHIFT = "O15_counterfactual_threshold_shift"
O16_CLOSE_ALTERNATIVE_NORMALIZATION = "O16_close_alternative_normalization"
O17_ACTION_VS_FACT_THRESHOLD = "O17_action_vs_fact_threshold"
O18_BASELINE_SCOPE_MISMATCH = "O18_baseline_scope_mismatch"
O19_MULTI_ENTITY_ROLE_BINDING = "O19_multi_entity_role_binding"
O20_MULTISTAGE_EVENT_BREAKPOINT = "O20_multistage_event_breakpoint"
O21_OBJECT_PROVENANCE_IDENTITY = "O21_object_provenance_identity"
O22_PATH_TOPOLOGY_REACHABILITY = "O22_path_topology_reachability"
O23_OBSERVATION_RELIABILITY_CONFLICT = "O23_observation_reliability_conflict"
O24_MULTI_HYPOTHESIS_RESIDUAL_RANKING = "O24_multi_hypothesis_residual_ranking"
O25_PROCEDURAL_INVARIANT_FRAME = "O25_procedural_invariant_frame"
O26_QUANTITATIVE_THRESHOLD_PROPAGATION = "O26_quantitative_threshold_propagation"
O27_CROSS_LAYER_CONCLUSION_CALIBRATION = "O27_cross_layer_conclusion_calibration"
O28_MULTIHOP_CHAIN_CLOSURE = "O28_multihop_chain_closure"
O29_ENTITY_IDENTITY_CONFLICT_RESOLUTION = "O29_entity_identity_conflict_resolution"
O30_ACTIVE_DISCRIMINATIVE_OBSERVATION = "O30_active_discriminative_observation"
O31_OBSERVATION_ACCUMULATION_CALIBRATION = "O31_observation_accumulation_calibration"
O32_ROLE_GRAPH_CRITICAL_EDGE = "O32_role_graph_critical_edge"
O33_CROSS_MODAL_SUPPORT_BOUNDARY = "O33_cross_modal_support_boundary"

LEGACY_OPERATOR_ORDER = (
    O10_EVIDENCE_SUFFICIENCY_LADDER,
    O11_UNOBSERVED_STATE_ATTRIBUTION,
    O12_CONJUNCTIVE_NECESSITY,
    O13_MINIMAL_DISQUALIFIER,
    # Retain the stable historical ID in registry ordering for replay and
    # reporting.  eligible_operator_ids still excludes it because its spec is
    # validation-only, so it can never become a generation candidate.
    O14_INFORMATION_CLOSURE,
    O15_COUNTERFACTUAL_THRESHOLD_SHIFT,
    O16_CLOSE_ALTERNATIVE_NORMALIZATION,
    O17_ACTION_VS_FACT_THRESHOLD,
    O18_BASELINE_SCOPE_MISMATCH,
)
NEW_CONTENT_OPERATOR_ORDER = (
    O19_MULTI_ENTITY_ROLE_BINDING,
    O20_MULTISTAGE_EVENT_BREAKPOINT,
    O21_OBJECT_PROVENANCE_IDENTITY,
    O22_PATH_TOPOLOGY_REACHABILITY,
    O23_OBSERVATION_RELIABILITY_CONFLICT,
    O24_MULTI_HYPOTHESIS_RESIDUAL_RANKING,
    O25_PROCEDURAL_INVARIANT_FRAME,
    O26_QUANTITATIVE_THRESHOLD_PROPAGATION,
    O27_CROSS_LAYER_CONCLUSION_CALIBRATION,
    O28_MULTIHOP_CHAIN_CLOSURE,
    O29_ENTITY_IDENTITY_CONFLICT_RESOLUTION,
    O30_ACTIVE_DISCRIMINATIVE_OBSERVATION,
    O31_OBSERVATION_ACCUMULATION_CALIBRATION,
    O32_ROLE_GRAPH_CRITICAL_EDGE,
    O33_CROSS_MODAL_SUPPORT_BOUNDARY,
)
OPERATOR_ORDER = LEGACY_OPERATOR_ORDER + NEW_CONTENT_OPERATOR_ORDER
OPERATOR_IDS = set(OPERATOR_ORDER)

EVOLUTION_REQUIRED_ACTIONS = {
    EVOLVE_HIGH_SCORE_OVERSCORE,
    RECONSTRUCT_LOW_SCORE_BOUNDARY,
    PROBE_MIDDLE_SCORE_BOUNDARY,
}

NON_EVOLUTION_ACTIONS = {
    PASS_THROUGH_OR_SCORING_NOISE,
    STOP_EVOLUTION,
}

SIGNATURE_FIELDS = (
    "core_capability",
    "claim_level",
    "problem_shape",
    "candidate_overscore_cause",
)
try:
    FAILURE_MEMORY_WINDOW_ROUNDS = int(os.getenv("FAILURE_MEMORY_WINDOW_ROUNDS", "3"))
except ValueError:
    FAILURE_MEMORY_WINDOW_ROUNDS = 3
FAILURE_MEMORY_WINDOW_ROUNDS = max(1, FAILURE_MEMORY_WINDOW_ROUNDS)

OPERATOR_SURFACE_FORM_FAMILY = {
    O10_EVIDENCE_SUFFICIENCY_LADDER: "evidence_sufficiency_ladder",
    O11_UNOBSERVED_STATE_ATTRIBUTION: "unobserved_state_attribution",
    O12_CONJUNCTIVE_NECESSITY: "conjunctive_necessity",
    O13_MINIMAL_DISQUALIFIER: "minimal_disqualifier",
    O14_INFORMATION_CLOSURE: "information_closure",
    O15_COUNTERFACTUAL_THRESHOLD_SHIFT: "counterfactual_threshold_shift",
    O16_CLOSE_ALTERNATIVE_NORMALIZATION: "close_alternative_normalization",
    O17_ACTION_VS_FACT_THRESHOLD: "action_vs_fact_threshold",
    O18_BASELINE_SCOPE_MISMATCH: "baseline_scope_mismatch",
    O19_MULTI_ENTITY_ROLE_BINDING: "multi_entity_role_binding",
    O20_MULTISTAGE_EVENT_BREAKPOINT: "multistage_event_breakpoint",
    O21_OBJECT_PROVENANCE_IDENTITY: "object_provenance_identity",
    O22_PATH_TOPOLOGY_REACHABILITY: "path_topology_reachability",
    O23_OBSERVATION_RELIABILITY_CONFLICT: "observation_reliability_conflict",
    O24_MULTI_HYPOTHESIS_RESIDUAL_RANKING: "multi_hypothesis_residual_ranking",
    O25_PROCEDURAL_INVARIANT_FRAME: "procedural_invariant_frame",
    O26_QUANTITATIVE_THRESHOLD_PROPAGATION: "quantitative_threshold_propagation",
    O27_CROSS_LAYER_CONCLUSION_CALIBRATION: "cross_layer_conclusion_calibration",
    O28_MULTIHOP_CHAIN_CLOSURE: "multihop_chain_closure",
    O29_ENTITY_IDENTITY_CONFLICT_RESOLUTION: "entity_identity_conflict_resolution",
    O30_ACTIVE_DISCRIMINATIVE_OBSERVATION: "active_discriminative_observation",
    O31_OBSERVATION_ACCUMULATION_CALIBRATION: "observation_accumulation_calibration",
    O32_ROLE_GRAPH_CRITICAL_EDGE: "role_graph_critical_edge",
    O33_CROSS_MODAL_SUPPORT_BOUNDARY: "cross_modal_support_boundary",
}

# First-part integration only: explicit diagnosis recognition for the new
# content operators. This does not qualify, validate, shadow-route, or release
# an operator; unmatched samples retain the legacy O10-O18 fallback behavior.
NEW_CONTENT_RULES = (
    (
        O33_CROSS_MODAL_SUPPORT_BOUNDARY,
        (O27_CROSS_LAYER_CONCLUSION_CALIBRATION, O23_OBSERVATION_RELIABILITY_CONFLICT),
        ("跨模态", "多源融合", "来源范围对齐", "时间与实体对齐", "视频与记录", "信号与文本"),
        "diagnosis indicates a cross-modal alignment and support-boundary problem.",
    ),
    (
        O32_ROLE_GRAPH_CRITICAL_EDGE,
        (O19_MULTI_ENTITY_ROLE_BINDING, O13_MINIMAL_DISQUALIFIER),
        ("角色关系图", "关系图关键边", "关系边方向", "必要关系边", "共现不等于协同", "替代关系路径"),
        "diagnosis indicates a critical directed edge in a role graph.",
    ),
    (
        O31_OBSERVATION_ACCUMULATION_CALIBRATION,
        (O23_OBSERVATION_RELIABILITY_CONFLICT, O10_EVIDENCE_SUFFICIENCY_LADDER),
        ("观测累积", "累积观测", "同源重复", "独立增量", "来源依赖", "重复观测"),
        "diagnosis indicates dependent or repeated observation accumulation.",
    ),
    (
        O30_ACTIVE_DISCRIMINATIVE_OBSERVATION,
        (O24_MULTI_HYPOTHESIS_RESIDUAL_RANKING, O16_CLOSE_ALTERNATIVE_NORMALIZATION),
        ("下一步观测", "主动判别观测", "判别力观测", "区分力观测", "下一步看什么", "观测结果分支"),
        "diagnosis calls for the next discriminative observation.",
    ),
    (
        O29_ENTITY_IDENTITY_CONFLICT_RESOLUTION,
        (O19_MULTI_ENTITY_ROLE_BINDING, O21_OBJECT_PROVENANCE_IDENTITY),
        ("实体同一性冲突", "身份线索冲突", "冲突绑定", "局部绑定全程", "身份连续性冲突", "排他身份线索"),
        "diagnosis indicates conflicting entity-identity evidence.",
    ),
    (
        O28_MULTIHOP_CHAIN_CLOSURE,
        (O20_MULTISTAGE_EVENT_BREAKPOINT, O22_PATH_TOPOLOGY_REACHABILITY),
        ("多跳链路", "整体链路闭合", "跨阶段跨节点", "局部链当整体链", "跨节点承接", "多跳闭合"),
        "diagnosis indicates incomplete multi-hop chain closure.",
    ),
    (
        O27_CROSS_LAYER_CONCLUSION_CALIBRATION,
        (O33_CROSS_MODAL_SUPPORT_BOUNDARY, O17_ACTION_VS_FACT_THRESHOLD),
        ("跨层结论", "结论层级传导", "支持到事实", "事实到可写结论", "可写结论到行动", "局部失效整体翻转"),
        "diagnosis indicates an overreach across conclusion layers.",
    ),
    (
        O26_QUANTITATIVE_THRESHOLD_PROPAGATION,
        (O25_PROCEDURAL_INVARIANT_FRAME, O18_BASELINE_SCOPE_MISMATCH),
        ("误差传播", "不确定区间", "结果区间跨阈值", "单位换算误差", "容差传播", "点估计替代区间"),
        "diagnosis indicates quantitative threshold and error propagation.",
    ),
    (
        O25_PROCEDURAL_INVARIANT_FRAME,
        (O26_QUANTITATIVE_THRESHOLD_PROPAGATION, O18_BASELINE_SCOPE_MISMATCH),
        ("程序不变量", "参照系一致", "记录对象映射", "步骤依赖倒置", "单位语义不一致", "结果可比性"),
        "diagnosis indicates a procedural invariant or reference-frame mismatch.",
    ),
    (
        O24_MULTI_HYPOTHESIS_RESIDUAL_RANKING,
        (O30_ACTIVE_DISCRIMINATIVE_OBSERVATION, O16_CLOSE_ALTERNATIVE_NORMALIZATION),
        ("多假设残差", "残差排序", "覆盖冲突残差", "假设覆盖与冲突", "额外假设成本", "解释过早锁定"),
        "diagnosis indicates multi-hypothesis coverage and residual ranking.",
    ),
    (
        O23_OBSERVATION_RELIABILITY_CONFLICT,
        (O31_OBSERVATION_ACCUMULATION_CALIBRATION, O29_ENTITY_IDENTITY_CONFLICT_RESOLUTION),
        ("观测可靠性", "观测质量限制", "可见性与清晰度", "遮挡视角限制", "来源可靠性冲突", "受限观测过度结论"),
        "diagnosis indicates a conflict in observation reliability.",
    ),
    (
        O22_PATH_TOPOLOGY_REACHABILITY,
        (O28_MULTIHOP_CHAIN_CLOSURE, O11_UNOBSERVED_STATE_ATTRIBUTION),
        ("路径拓扑", "联合可达性", "候选路径约束", "端点方向约束", "拓扑连通与时间窗", "单向边可达"),
        "diagnosis indicates joint reachability under topology and time constraints.",
    ),
    (
        O21_OBJECT_PROVENANCE_IDENTITY,
        (O29_ENTITY_IDENTITY_CONFLICT_RESOLUTION, O19_MULTI_ENTITY_ROLE_BINDING),
        ("对象来源链", "物品同一性", "竞争来源", "转移缺口", "遮挡后重现对象", "来源与对象连续性"),
        "diagnosis indicates object provenance and identity tracking.",
    ),
    (
        O20_MULTISTAGE_EVENT_BREAKPOINT,
        (O28_MULTIHOP_CHAIN_CLOSURE, O13_MINIMAL_DISQUALIFIER),
        ("多阶段事件链", "状态转移图", "链路断点", "阶段状态承接", "局部链整体成立", "断点后果传播"),
        "diagnosis indicates a breakpoint in a multistage event chain.",
    ),
    (
        O19_MULTI_ENTITY_ROLE_BINDING,
        (O29_ENTITY_IDENTITY_CONFLICT_RESOLUTION, O21_OBJECT_PROVENANCE_IDENTITY),
        ("多实体角色绑定", "主体角色交换", "实体行为绑定", "角色方向错误", "节点实体绑定", "实施与协助混淆"),
        "diagnosis indicates a multi-entity role-binding failure.",
    ),
)
FAILURE_MEMORY_WARN_THRESHOLD = 1
FAILURE_MEMORY_DOWNRANK_THRESHOLD = 2
FAILURE_MEMORY_AVOID_THRESHOLD = 3


def load_json_or_jsonl(input_path: str) -> List[Dict[str, Any]]:
    return load_json_records(input_path, stage="operator_router")


def write_jsonl(records: Iterable[Dict[str, Any]], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_jsonl_if_exists(path: str) -> List[Dict[str, Any]]:
    if not path or not os.path.exists(path):
        return []
    records: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))
    return records


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _has_any(text: str, terms: Sequence[str]) -> bool:
    return any(term and term in text for term in terms)


def _append_unique(items: List[str], values: Sequence[Optional[str]]) -> None:
    for value in values:
        if value and value not in items:
            items.append(value)


def _remove_values(items: Sequence[str], blocked: Sequence[str]) -> List[str]:
    blocked_set = set(blocked)
    return [item for item in items if item not in blocked_set]


def _normalize_operator(value: Any) -> Optional[str]:
    text = _clean_text(value)
    return text if text in OPERATOR_IDS else None


def _read_nonnegative_round(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def get_evolution_action(item: Dict[str, Any]) -> str:
    return _clean_text(item.get("evolution_action"))


def frontier_route_context(item: Mapping[str, Any]) -> Dict[str, Any]:
    context = item.get("frontier_route")
    if not isinstance(context, Mapping) or context.get("enabled") is not True:
        return {}
    return {
        key: context.get(key)
        for key in (
            "enabled",
            "parent_node_id",
            "root_node_id",
            "parent_depth",
            "operator_stack",
            "direct_parent_score_rate",
            "root_score_rate",
            "profile_version",
        )
        if key in context
    }


def is_frontier_route(item: Mapping[str, Any]) -> bool:
    return bool(frontier_route_context(item))


def should_route_for_evolution(item: Dict[str, Any]) -> bool:
    return (
        get_evolution_action(item) in EVOLUTION_REQUIRED_ACTIONS
        or is_frontier_route(item)
    )


def get_sample_profile(item: Dict[str, Any]) -> Dict[str, Any]:
    profile = item.get("sample_profile")
    if not isinstance(profile, dict):
        raise ValueError("record missing sample_profile; run profile_samples.py first")
    return profile


def get_overscore_diagnosis(item: Dict[str, Any]) -> Dict[str, Any]:
    diagnosis = item.get("overscore_diagnosis")
    if not isinstance(diagnosis, dict):
        raise ValueError("record missing overscore_diagnosis; run profile_samples.py first")
    return diagnosis


def get_evolution_state(item: Dict[str, Any]) -> Dict[str, Any]:
    state = item.get("evolution_state")
    return state if isinstance(state, dict) else {}


def build_sample_signature(item: Dict[str, Any]) -> Dict[str, str]:
    profile = get_sample_profile(item)
    diagnosis = get_overscore_diagnosis(item)
    return {
        "core_capability": _clean_text(profile.get("core_capability")),
        "claim_level": _clean_text(profile.get("claim_level")),
        "problem_shape": _clean_text(profile.get("problem_shape")),
        "candidate_overscore_cause": _clean_text(diagnosis.get("candidate_overscore_cause")),
    }


def _sample_signature_from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    signature = record.get("sample_signature")
    return signature if isinstance(signature, dict) else {}


def _round_value(item: Dict[str, Any]) -> Optional[int]:
    direct = _read_nonnegative_round(item.get("round"))
    if direct is not None:
        return direct
    state = item.get("evolution_state")
    if isinstance(state, dict):
        return _read_nonnegative_round(state.get("round"))
    return None


def _record_round(record: Dict[str, Any]) -> Optional[int]:
    return _read_nonnegative_round(record.get("round"))


def _operator_from_failure_record(record: Dict[str, Any]) -> Optional[str]:
    for field in ("operator_used", "operator_id", "candidate_operator"):
        operator = _normalize_operator(record.get(field))
        if operator:
            return operator
    return None


def _surface_form_from_record(record: Dict[str, Any], operator: Optional[str] = None, *, use_operator_fallback: bool = True) -> str:
    for field in ("surface_form_family", "question_surface_form"):
        value = _clean_text(record.get(field))
        if value:
            return value
    generation = record.get("candidate_generation")
    if isinstance(generation, dict):
        for field in ("surface_form_family", "question_surface_form"):
            value = _clean_text(generation.get(field))
            if value:
                return value
    metadata = record.get("meta_info")
    if isinstance(metadata, dict):
        metadata = metadata.get("question_evolution_metadata")
        if isinstance(metadata, dict):
            for field in ("surface_form_family", "question_surface_form"):
                value = _clean_text(metadata.get(field))
                if value:
                    return value
    if operator and use_operator_fallback:
        return OPERATOR_SURFACE_FORM_FAMILY.get(operator, "unknown")
    return "unknown"


def _failure_type_from_record(record: Dict[str, Any]) -> str:
    for field in ("failure_type", "effect_label"):
        value = _clean_text(record.get(field))
        if value:
            return value
    effect = record.get("effect_analysis")
    if isinstance(effect, dict):
        return _clean_text(effect.get("effect_label"))
    return ""


def _same_signature(left: Dict[str, Any], right: Dict[str, Any], *, min_similarity: float = 0.75) -> bool:
    return signature_similarity(left, right) >= min_similarity


def build_failure_memory_actions(
    item: Dict[str, Any],
    failure_memory: Sequence[Dict[str, Any]],
    *,
    window_rounds: int = FAILURE_MEMORY_WINDOW_ROUNDS,
    memory_index: Optional["MemoryMatchIndex"] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    signature = build_sample_signature(item)
    current_round = _round_value(item)
    min_round = current_round - window_rounds + 1 if current_round is not None else None
    grouped: Counter = Counter()

    candidate_memory = (
        memory_index.candidate_records(signature)
        if memory_index is not None
        else failure_memory
    )
    for record in candidate_memory:
        memory_signature = _sample_signature_from_record(record)
        if not memory_signature or not _same_signature(signature, memory_signature):
            continue
        memory_round = _record_round(record)
        if min_round is not None and memory_round is not None and memory_round < min_round:
            continue
        operator = _operator_from_failure_record(record)
        if not operator:
            continue
        surface_form = _surface_form_from_record(record, operator, use_operator_fallback=False)
        failure_type = _failure_type_from_record(record)
        if not surface_form or surface_form == "unknown" or not failure_type:
            continue
        grouped[(operator, surface_form, failure_type)] += 1

    warnings: List[Dict[str, Any]] = []
    downrank: List[Dict[str, Any]] = []
    avoid: List[Dict[str, Any]] = []
    for (operator, surface_form, failure_type), count in sorted(grouped.items()):
        entry = {
            "operator_used": operator,
            "surface_form_family": surface_form,
            "failure_type": failure_type,
            "failure_count": count,
            "reason": "repeated_negative_gain",
        }
        if count >= FAILURE_MEMORY_AVOID_THRESHOLD:
            avoid.append({**entry, "action": "avoid"})
        elif count >= FAILURE_MEMORY_DOWNRANK_THRESHOLD:
            downrank.append({**entry, "action": "downrank"})
        elif count >= FAILURE_MEMORY_WARN_THRESHOLD:
            warnings.append({**entry, "action": "warn_only"})

    return {
        "memory_warnings": warnings,
        "downrank_operator_surface_forms": downrank,
        "avoid_operator_surface_forms": avoid,
    }


def _matches_operator_surface(action: Dict[str, Any], operator: Optional[str]) -> bool:
    if not operator:
        return False
    return (
        _clean_text(action.get("operator_used")) == operator
        and _clean_text(action.get("surface_form_family")) == OPERATOR_SURFACE_FORM_FAMILY.get(operator, "unknown")
    )


def _apply_surface_form_memory_actions(
    primary: Optional[str],
    backups: List[str],
    memory_actions: Dict[str, List[Dict[str, Any]]],
) -> Tuple[Optional[str], List[str], List[str]]:
    reason_parts: List[str] = []
    avoid_actions = memory_actions.get("avoid_operator_surface_forms", [])
    downrank_actions = memory_actions.get("downrank_operator_surface_forms", [])

    candidates: List[str] = []
    _append_unique(candidates, [primary])
    _append_unique(candidates, backups)

    avoid_pairs = [action for action in avoid_actions if _clean_text(action.get("operator_used"))]
    if primary and any(_matches_operator_surface(action, primary) for action in avoid_pairs):
        replacement = next(
            (
                operator
                for operator in candidates
                if operator != primary and not any(_matches_operator_surface(action, operator) for action in avoid_pairs)
            ),
            None,
        )
        if replacement:
            reason_parts.append(
                f"failure memory avoids surface form {OPERATOR_SURFACE_FORM_FAMILY.get(primary, 'unknown')} for {primary}; using {replacement}."
            )
            candidates = [replacement] + [operator for operator in candidates if operator != replacement]
            primary = replacement
            backups = [
                operator
                for operator in candidates[1:]
                if operator != primary and not any(_matches_operator_surface(action, operator) for action in avoid_pairs)
            ]
        else:
            reason_parts.append(
                f"failure memory marks {primary}+{OPERATOR_SURFACE_FORM_FAMILY.get(primary, 'unknown')} as avoid, but no safe backup exists."
            )

    if primary and any(_matches_operator_surface(action, primary) for action in downrank_actions):
        replacement = next((operator for operator in backups if operator != primary), None)
        if replacement:
            reason_parts.append(
                f"failure memory downranks surface form {OPERATOR_SURFACE_FORM_FAMILY.get(primary, 'unknown')} for {primary}; using {replacement} first."
            )
            backups = [operator for operator in backups if operator != replacement]
            backups.append(primary)
            primary = replacement

    backups = _remove_values(backups, [primary] if primary else [])
    return primary, backups, reason_parts


def signature_similarity(a: Dict[str, Any], b: Dict[str, Any]) -> float:
    compared = 0
    matched = 0
    for field in SIGNATURE_FIELDS:
        left = _clean_text(a.get(field))
        right = _clean_text(b.get(field))
        if not left or not right:
            continue
        compared += 1
        if left == right:
            matched += 1
    if compared == 0:
        return 0.0
    return matched / compared


class MemoryMatchIndex:
    """Read-only inverted index with exact-result caching.

    Candidate pruning uses exact signature field hits, then delegates to the
    existing similarity calculation and stable ordering.  It therefore changes
    lookup cost, not routing semantics.
    """

    def __init__(self, records: Sequence[Dict[str, Any]]):
        self.records = list(records)
        self._postings: Dict[Tuple[str, str], set] = {}
        self._cache: Dict[Tuple[Tuple[str, str], float], List[Dict[str, Any]]] = {}
        self.cache_hits = 0
        for index, record in enumerate(self.records):
            signature = _sample_signature_from_record(record)
            for field in SIGNATURE_FIELDS:
                value = _clean_text(signature.get(field))
                if value:
                    self._postings.setdefault((field, value), set()).add(index)

    @staticmethod
    def _signature_key(signature: Dict[str, Any]) -> Tuple[Tuple[str, str], ...]:
        return tuple((field, _clean_text(signature.get(field))) for field in SIGNATURE_FIELDS)

    def candidate_records(self, signature: Dict[str, Any]) -> List[Dict[str, Any]]:
        indexes = set()
        for field in SIGNATURE_FIELDS:
            value = _clean_text(signature.get(field))
            if value:
                indexes.update(self._postings.get((field, value), ()))
        return [self.records[index] for index in sorted(indexes)]

    def find(
        self,
        signature: Dict[str, Any],
        *,
        min_similarity: float = 0.75,
    ) -> List[Dict[str, Any]]:
        cache_key = (self._signature_key(signature), float(min_similarity))
        cached = self._cache.get(cache_key)
        if cached is not None:
            self.cache_hits += 1
            return [dict(item) for item in cached]
        matches = _find_memory_matches_linear(
            signature,
            self.candidate_records(signature),
            min_similarity=min_similarity,
        )
        self._cache[cache_key] = matches
        return [dict(item) for item in matches]


def _find_memory_matches_linear(
    signature: Dict[str, str],
    memory_records: Sequence[Dict[str, Any]],
    *,
    min_similarity: float = 0.75,
) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for record in memory_records:
        memory_signature = record.get("sample_signature")
        if not isinstance(memory_signature, dict):
            continue
        similarity = signature_similarity(signature, memory_signature)
        if similarity >= min_similarity:
            match = dict(record)
            match["signature_similarity"] = similarity
            matches.append(match)
    matches.sort(key=lambda item: item.get("signature_similarity", 0), reverse=True)
    return matches


def find_memory_matches(
    signature: Dict[str, str],
    memory_records: Sequence[Dict[str, Any]],
    *,
    min_similarity: float = 0.75,
    index: Optional[MemoryMatchIndex] = None,
) -> List[Dict[str, Any]]:
    if index is not None:
        return index.find(signature, min_similarity=min_similarity)
    return _find_memory_matches_linear(
        signature,
        memory_records,
        min_similarity=min_similarity,
    )


def _base_rule_route(item: Dict[str, Any]) -> Tuple[Optional[str], List[str], str]:
    diagnosis = get_overscore_diagnosis(item)
    cause = _clean_text(diagnosis.get("candidate_overscore_cause"))
    target = _clean_text(diagnosis.get("target_failure_mode"))
    combined = f"{cause} {target}"

    for primary, backups, terms, reason in NEW_CONTENT_RULES:
        if _has_any(combined, terms):
            return primary, list(backups), reason

    if _has_any(
        combined,
        (
            "盲区",
            "不可见区间",
            "未出现",
            "端点事实",
            "可见端点",
            "时间窗",
            "路径约束",
            "时序一致",
            "不可见状态",
        ),
    ):
        return (
            O11_UNOBSERVED_STATE_ATTRIBUTION,
            [O17_ACTION_VS_FACT_THRESHOLD],
            "diagnosis indicates unobserved-state attribution risk.",
        )

    if _has_any(
        combined,
        ("基线", "样本口径", "统计口径", "纳入口径", "可比基线", "范围错配", "基准范围", "异常性"),
    ):
        return (
            O18_BASELINE_SCOPE_MISMATCH,
            [O10_EVIDENCE_SUFFICIENCY_LADDER],
            "diagnosis indicates baseline-scope mismatch.",
        )

    if _has_any(
        combined,
        ("正常解释", "替代解释", "竞争解释", "覆盖关系", "残差", "discriminator", "风险消失", "异常强度下降"),
    ):
        return (
            O16_CLOSE_ALTERNATIVE_NORMALIZATION,
            [O15_COUNTERFACTUAL_THRESHOLD_SHIFT],
            "diagnosis indicates over-normalization by a close alternative.",
        )

    if _has_any(
        combined,
        ("反事实", "单变量", "变量变化", "单一比较量", "固定门槛", "门槛裕量", "门槛迁移", "保留范围"),
    ):
        return (
            O15_COUNTERFACTUAL_THRESHOLD_SHIFT,
            [O16_CLOSE_ALTERNATIVE_NORMALIZATION],
            "diagnosis calls for a single-variable threshold shift.",
        )

    if _has_any(
        combined,
        (
            "两套规则",
            "双规则",
            "规则适用对象",
            "规则范围",
            "处置规则",
            "事实定性规则",
            "处置",
            "事实定性",
            "行动门槛",
            "报告表述",
            "动作层与性质层",
        ),
    ):
        return (
            O17_ACTION_VS_FACT_THRESHOLD,
            [O11_UNOBSERVED_STATE_ATTRIBUTION, O12_CONJUNCTIVE_NECESSITY],
            "diagnosis indicates confusion between action and fact thresholds.",
        )

    if _has_any(combined, ("题外补设", "题干外", "隐藏前提", "信息闭包", "泛化罗列", "事实绑定")):
        return (None, [], "diagnosis is an information-closure risk; O14 is validation-only, so no generator is selected.")

    if _has_any(
        combined,
        (
            "必要连接",
            "连接失效",
            "局部连接",
            "整体命题",
            "原评价",
            "新增事实",
            "推翻",
            "下调",
            "最小否决",
            "最小关键事实",
            "最关键缺口",
        ),
    ):
        return (
            O13_MINIMAL_DISQUALIFIER,
            [O15_COUNTERFACTUAL_THRESHOLD_SHIFT],
            "diagnosis calls for testing whether a new fact changes an existing evaluation.",
        )

    if _has_any(
        combined,
        (
            "仅 X",
            "仅 Y",
            "X+Y",
            "独立贡献",
            "共同闭合",
            "联合必要",
            "共同必要",
            "强线索",
            "必要条件",
            "门槛未闭合",
            "层级越推",
            "抓显眼点漏关键层",
        ),
    ):
        return (
            O12_CONJUNCTIVE_NECESSITY,
            [O17_ACTION_VS_FACT_THRESHOLD],
            "diagnosis indicates that a strong clue is replacing an unclosed threshold.",
        )

    if _has_any(
        combined,
        (
            "最小充分集",
            "最小充分集合",
            "集合成员",
            "成员消融",
            "证明力跃迁",
            "反常线索",
            "主线切换",
            "受干扰信息带偏",
            "近似项分层",
            "层级混淆",
        ),
    ):
        return (
            O10_EVIDENCE_SUFFICIENCY_LADDER,
            [O15_COUNTERFACTUAL_THRESHOLD_SHIFT, O14_INFORMATION_CLOSURE],
            "diagnosis calls for close business-judgment competition.",
        )

    return (None, [], "no target failure mechanism maps safely to a generation operator.")


def _previous_operator(item: Dict[str, Any]) -> Optional[str]:
    state = get_evolution_state(item)
    operator = _normalize_operator(state.get("previous_operator"))
    if operator:
        return operator

    meta_info = item.get("meta_info")
    if isinstance(meta_info, dict):
        metadata = meta_info.get("question_evolution_metadata")
        if isinstance(metadata, dict):
            return _normalize_operator(metadata.get("operator_used"))
    return None


def _recommended_next_methods(item: Dict[str, Any]) -> List[str]:
    state = get_evolution_state(item)
    values = state.get("recommended_next_methods")
    if not isinstance(values, list):
        return []
    operators: List[str] = []
    for value in values:
        operator = _normalize_operator(value)
        if operator and operator not in operators:
            operators.append(operator)
    return operators


def _is_current_full_score(item: Dict[str, Any], full_score_threshold: float) -> bool:
    score_rate = get_score_rate(item)
    if score_rate is None:
        return False
    return score_rate >= full_score_threshold


def _is_high_value_sample(item: Dict[str, Any]) -> bool:
    diagnosis = get_overscore_diagnosis(item)
    profile = get_sample_profile(item)
    action = get_evolution_action(item)
    cause = _clean_text(diagnosis.get("candidate_overscore_cause"))
    target = _clean_text(diagnosis.get("target_failure_mode"))
    return (
        action in EVOLUTION_REQUIRED_ACTIONS
        and bool(diagnosis.get("is_worth_evolving"))
        and _clean_text(profile.get("external_knowledge_risk")).lower() != "high"
        and _has_any(
            f"{cause} {target}",
            (
                "盲区",
                "强线索",
                "题外补设",
                "反事实",
                "正常解释",
                "处置",
                "基线",
                "主线抓偏",
            ),
        )
    )


def build_operator_route(
    item: Dict[str, Any],
    *,
    operator_memory: Sequence[Dict[str, Any]] = (),
    failure_memory: Sequence[Dict[str, Any]] = (),
    full_score_threshold: float = 0.99,
    failure_memory_window_rounds: int = FAILURE_MEMORY_WINDOW_ROUNDS,
    operator_memory_index: Optional[MemoryMatchIndex] = None,
    failure_memory_index: Optional[MemoryMatchIndex] = None,
) -> Dict[str, Any]:
    action = get_evolution_action(item)
    frontier_route = is_frontier_route(item)
    if action in NON_EVOLUTION_ACTIONS and not frontier_route:
        return {
            "primary_operator": None,
            "backup_operators": [],
            "avoid_operators": [],
            "routing_reason": f"evolution_action={action} does not require question evolution.",
            "is_high_value_sample": False,
            "should_use_local_tree_search": False,
            "memory_matches": {"operator": [], "failure": []},
        }
    if action and action not in EVOLUTION_REQUIRED_ACTIONS and not frontier_route:
        raise ValueError(f"unsupported evolution_action: {action}")

    get_sample_profile(item)
    get_overscore_diagnosis(item)

    primary, backups, reason = _base_rule_route(item)
    avoid: List[str] = []
    reason_parts = [reason]
    if frontier_route:
        reason_parts.append(
            "frontier_route bypasses only the original-sample admission stop; current-node profile and route evidence are used."
        )
    recommended_next = _recommended_next_methods(item)

    signature = build_sample_signature(item)
    operator_matches = find_memory_matches(
        signature,
        operator_memory,
        index=operator_memory_index,
    )
    failure_matches = find_memory_matches(
        signature,
        failure_memory,
        index=failure_memory_index,
    )
    failure_memory_actions = build_failure_memory_actions(
        item,
        failure_memory,
        window_rounds=failure_memory_window_rounds,
        memory_index=failure_memory_index,
    )

    if operator_matches:
        memory_operator = _normalize_operator(operator_matches[0].get("operator_used"))
        if memory_operator and memory_operator not in avoid:
            if primary and primary != memory_operator:
                _append_unique(backups, [primary])
                reason_parts.append(
                    f"operator memory promotes {memory_operator} over rule primary {primary}."
                )
            primary = memory_operator

    previous_operator = _previous_operator(item)
    state = get_evolution_state(item)
    previous_effect = _clean_text(state.get("previous_effect_status"))
    stop_status = _clean_text(state.get("stop_status"))
    if previous_operator and (
        _is_current_full_score(item, full_score_threshold)
        or previous_effect in {
            "full_score_no_drop",
            "no_clear_effect",
            "needs_manual_review",
            "repeated_pattern",
            "score_increased",
        }
        or stop_status in {
            "continue_with_new_operator",
            "local_tree_search_needed",
            "rollback_and_reroute",
        }
    ):
        _append_unique(avoid, [previous_operator])
        reason_parts.append(f"previous ineffective operator {previous_operator} is blocked for this reroute.")

    if recommended_next:
        ordered_candidates: List[str] = []
        _append_unique(ordered_candidates, recommended_next)
        _append_unique(ordered_candidates, [primary])
        _append_unique(ordered_candidates, backups)
        ordered_candidates = _remove_values(ordered_candidates, avoid)
        if ordered_candidates:
            primary = ordered_candidates[0]
            backups = ordered_candidates[1:]
            reason_parts.append(
                "recommended_next_methods from evolution_state are prioritized before fallback rule routing."
            )

    backups = _remove_values(backups, [primary] if primary else [])
    backups = _remove_values(backups, avoid)
    if primary in avoid:
        replacement = next((operator for operator in backups if operator not in avoid), None)
        if replacement:
            primary = replacement
            backups = _remove_values(backups, [primary])
        else:
            primary = None

    primary, backups, memory_action_reasons = _apply_surface_form_memory_actions(
        primary,
        backups,
        failure_memory_actions,
    )
    reason_parts.extend(memory_action_reasons)

    consecutive_full = int(get_evolution_state(item).get("consecutive_full_score_count", 0) or 0)
    should_tree = (
        _is_high_value_sample(item)
        or action == RECONSTRUCT_LOW_SCORE_BOUNDARY
        or consecutive_full >= 2
    )

    source_analysis = analyze_source(item)
    authorization = resolve_evolution_authorization(item)
    mode_decision = resolve_evolution_mode(item, source_analysis)
    candidates = [operator for operator in [primary, *backups] if operator]
    mode_exclusions: Dict[str, str] = {}
    slot_assessments: Dict[str, Dict[str, Any]] = {}
    compatible_candidates: List[str] = []
    for operator in candidates:
        contract = get_execution_contract(operator)
        if mode_decision["evolution_mode"] not in contract.supported_modes:
            mode_exclusions[operator] = "mode_not_supported"
        else:
            assessment = operator_slot_assessment({
                **item,
                "source_analysis": source_analysis,
                "mode_decision": mode_decision,
            }, operator)
            slot_assessments[operator] = assessment
            if assessment["has_hard_missing_slot"]:
                mode_exclusions[operator] = "authoritative_slot_missing"
                continue
            compatible_candidates.append(operator)
    primary = compatible_candidates[0] if compatible_candidates else None
    backups = compatible_candidates[1:]
    no_safe_operator = primary is None
    if no_safe_operator:
        reason_parts.append("no safe generation operator after authorization, mode, slot, adjacency, and memory review.")
    return {
        "primary_operator": primary,
        "backup_operators": backups,
        "avoid_operators": avoid,
        "routing_reason": " ".join(reason_parts),
        "is_high_value_sample": _is_high_value_sample(item),
        "should_use_local_tree_search": should_tree,
        "memory_warnings": failure_memory_actions["memory_warnings"],
        "downrank_operator_surface_forms": failure_memory_actions["downrank_operator_surface_forms"],
        "avoid_operator_surface_forms": failure_memory_actions["avoid_operator_surface_forms"],
        "memory_matches": {
            "operator": operator_matches[:3],
            "failure": failure_matches[:3],
        },
        "is_frontier_route": frontier_route,
        "source_analysis": source_analysis,
        "evolution_authorization": authorization,
        "mode_decision": mode_decision,
        "mode_excluded_operator_reasons": mode_exclusions,
        "operator_slot_assessments": slot_assessments,
        "no_safe_operator": no_safe_operator,
        "no_safe_operator_reason": " ".join(reason_parts) if no_safe_operator else None,
    }


ROUTING_MODE_RULE = "rule"
ROUTING_MODE_HYBRID = "hybrid"
ASSIGNMENT_MODE_NATURAL = "natural"
ASSIGNMENT_MODE_LIVE = "live"
DEFAULT_ROUTER_TIMEOUT_SECONDS = 60.0
DEFAULT_ROUTER_CONCURRENCY = 20
DEFAULT_ROUTER_RETRIES = 0
ROUTER_TEMPERATURE = 0.0
def _digest_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _provider_identifier(base_url: str) -> str:
    return hashlib.sha256(str(base_url or "").encode("utf-8")).hexdigest()[:16]


def _configured_router_api_keys(cli_keys: Optional[Sequence[str]] = None) -> List[str]:
    if cli_keys:
        values = [str(value).strip() for value in cli_keys if str(value).strip()]
        if values:
            return values
    for name in (
        "ROUTER_API_KEYS",
        "GPT_API_KEYS",
        "HIAPI_KEYS_BIG",
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY",
    ):
        raw = os.getenv(name, "")
        values = [part.strip() for part in raw.split(",") if part.strip()]
        if values:
            return values
    return get_config_list(
        "ROUTER_API_KEYS",
        "GPT_API_KEYS",
        "HIAPI_KEYS_BIG",
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY",
    )


def _configured_router_base_url() -> str:
    for name in ("ROUTER_BASE_URL", "GPT_BASE_URL", "OPENAI_BASE_URL"):
        value = _clean_text(os.getenv(name))
        if value:
            return value
    return get_config_value(
        "ROUTER_BASE_URL",
        "GPT_BASE_URL",
        "BASE_URL",
        "OPENAI_BASE_URL",
        default="",
    )


def _configured_router_model() -> str:
    return (
        _clean_text(os.getenv("ROUTER_MODEL"))
        or _clean_text(os.getenv("GPT_MODEL"))
        or get_config_value("ROUTER_MODEL", "GPT_MODEL", "QA_MODEL", default="gpt-5.4")
    )


@dataclass(frozen=True)
class RouterSettings:
    routing_mode: str = ROUTING_MODE_RULE
    assignment_mode: str = ASSIGNMENT_MODE_NATURAL
    model: str = ""
    base_url: str = ""
    timeout_seconds: float = DEFAULT_ROUTER_TIMEOUT_SECONDS
    retries: int = DEFAULT_ROUTER_RETRIES
    concurrency: int = DEFAULT_ROUTER_CONCURRENCY
    temperature: float = ROUTER_TEMPERATURE

    @classmethod
    def from_values(
        cls,
        *,
        routing_mode: str = ROUTING_MODE_RULE,
        assignment_mode: str = ASSIGNMENT_MODE_NATURAL,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = DEFAULT_ROUTER_TIMEOUT_SECONDS,
        retries: int = DEFAULT_ROUTER_RETRIES,
        concurrency: int = DEFAULT_ROUTER_CONCURRENCY,
    ) -> "RouterSettings":
        normalized_mode = _clean_text(routing_mode).lower() or ROUTING_MODE_RULE
        normalized_assignment = _clean_text(assignment_mode).lower() or ASSIGNMENT_MODE_NATURAL
        if normalized_mode not in {ROUTING_MODE_RULE, ROUTING_MODE_HYBRID}:
            raise ValueError(f"unsupported routing mode: {normalized_mode}")
        if normalized_assignment not in {ASSIGNMENT_MODE_NATURAL, ASSIGNMENT_MODE_LIVE}:
            raise ValueError(f"unsupported assignment mode: {normalized_assignment}")
        if timeout_seconds <= 0:
            raise ValueError("router timeout must be positive")
        if retries != 0:
            raise ValueError("Router retries must be 0; failures use deterministic fallback")
        if concurrency < 1:
            raise ValueError("router concurrency must be >= 1")
        return cls(
            routing_mode=normalized_mode,
            assignment_mode=normalized_assignment,
            model=_clean_text(model) or _configured_router_model(),
            base_url=_clean_text(base_url) or _configured_router_base_url(),
            timeout_seconds=float(timeout_seconds),
            retries=int(retries),
            concurrency=int(concurrency),
        )

    @property
    def provider_id(self) -> str:
        return _provider_identifier(self.base_url)

    def cache_policy(self) -> Dict[str, Any]:
        return {
            "routing_mode": self.routing_mode,
            "assignment_mode": self.assignment_mode,
            "model": self.model,
            "temperature": self.temperature,
            "provider_id": self.provider_id,
            "timeout_seconds": self.timeout_seconds,
            "retries": self.retries,
            "transport_policy_version": ROUTER_TRANSPORT_POLICY_VERSION,
            "registry_policy_version": ROUTER_REGISTRY_POLICY_VERSION,
            "prompt_version": ROUTER_PROMPT_VERSION,
            "schema_version": ROUTING_SCHEMA_VERSION,
        }


@dataclass(frozen=True)
class RouterCallResult:
    raw_response: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    elapsed_seconds: float


class HybridRouterClient:
    """One-attempt OpenAI-compatible Router client.

    ``max_retries=0`` prevents the SDK from turning a logical routing task into
    multiple hidden HTTP attempts.  Key rotation is intentionally not used:
    every routing task gets one request and then deterministic fallback.
    """

    def __init__(self, settings: RouterSettings, api_keys: Sequence[str]):
        if not api_keys:
            raise ValueError("Router requires ROUTER_API_KEYS/GPT_API_KEYS/OPENAI_API_KEY")
        self.settings = settings
        self.api_keys = list(api_keys)
        self._client: Any = None

    async def _client_or_create(self) -> Any:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:
                raise RuntimeError("Missing dependency: install openai to run hybrid routing") from exc
            kwargs: Dict[str, Any] = {
                "api_key": self.api_keys[0],
                "timeout": self.settings.timeout_seconds,
                "max_retries": 0,
            }
            if self.settings.base_url:
                kwargs["base_url"] = self.settings.base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()

    async def route(self, prompt: str) -> RouterCallResult:
        started = time.monotonic()
        client = await self._client_or_create()
        consume_model_request_budget()
        response = await client.chat.completions.create(
            model=self.settings.model,
            messages=[{"role": "user", "content": prompt}],
            temperature=self.settings.temperature,
            max_tokens=8192,
        )
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise RuntimeError("empty_response")
        content = _clean_text(getattr(getattr(choices[0], "message", None), "content", ""))
        if not content:
            raise RuntimeError("empty_response")
        usage = getattr(response, "usage", None)
        return RouterCallResult(
            raw_response=content,
            input_tokens=getattr(usage, "prompt_tokens", None) if usage is not None else None,
            output_tokens=getattr(usage, "completion_tokens", None) if usage is not None else None,
            elapsed_seconds=time.monotonic() - started,
        )


class _RouterKeyLock:
    """Small cross-process advisory lock used while populating one cache key."""

    def __init__(self, path: Path):
        self.path = path
        self.handle: Optional[Any] = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = open(self.path, "a+b")
        self.handle.seek(0)
        self.handle.write(b"0")
        self.handle.flush()
        deadline = time.monotonic() + 120.0
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    self.handle.seek(0)
                    msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"timed out waiting for router cache lock: {self.path}")
                time.sleep(0.05)

    def release(self) -> None:
        if self.handle is None:
            return
        try:
            self.handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        finally:
            self.handle.close()
            self.handle = None


class RouterCache:
    """Append-only, fsynced cache that stores only successful parsed routes."""

    def __init__(self, path: Optional[str]):
        self.path = Path(path).resolve() if path else None
        self._entries: Dict[str, Dict[str, Any]] = {}
        if self.path and self.path.exists():
            with self.path.open("r", encoding="utf-8") as source:
                for line in source:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = _clean_text(entry.get("cache_key")) if isinstance(entry, Mapping) else ""
                    if key and entry.get("status") == "succeeded" and isinstance(entry.get("parsed_response"), Mapping):
                        self._entries[key] = dict(entry)

    def get(self, cache_key: str) -> Optional[Dict[str, Any]]:
        entry = self._entries.get(cache_key)
        return dict(entry) if entry else None

    def key_lock(self, cache_key: str) -> Optional[_RouterKeyLock]:
        if self.path is None:
            return None
        lock_dir = self.path.parent / f"{self.path.name}.locks"
        return _RouterKeyLock(lock_dir / f"{cache_key}.lock")

    def reload_key(self, cache_key: str) -> Optional[Dict[str, Any]]:
        if self.path is None or not self.path.exists():
            return self.get(cache_key)
        with self.path.open("r", encoding="utf-8") as source:
            for line in source:
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(entry, Mapping)
                    and entry.get("cache_key") == cache_key
                    and entry.get("status") == "succeeded"
                    and isinstance(entry.get("parsed_response"), Mapping)
                ):
                    self._entries[cache_key] = dict(entry)
        return self.get(cache_key)

    def put_success(
        self,
        cache_key: str,
        parsed_response: Mapping[str, Any],
        *,
        raw_candidate_count: int,
    ) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "cache_key": cache_key,
            "status": "succeeded",
            "created_at": time.time(),
            "raw_candidate_count": max(0, int(raw_candidate_count)),
            "parsed_response": dict(parsed_response),
        }
        line = (json.dumps(entry, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        descriptor = os.open(str(self.path), os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        self._entries[cache_key] = entry


def _operator_adjacency() -> Dict[str, Set[str]]:
    by_number = {
        operator_id.split("_", 1)[0]: operator_id
        for operator_id in OPERATOR_SPECS
        if "_" in operator_id
    }
    adjacency: Dict[str, Set[str]] = {}
    for operator_id, spec in OPERATOR_SPECS.items():
        values: Set[str] = set()
        for boundary in getattr(spec, "adjacent_operator_boundaries", ()) or ():
            text = str(boundary)
            for marker in re.findall(r"\bO\d{2}(?:_[a-z0-9_]+)?\b", text):
                values.add(marker if marker in OPERATOR_SPECS else by_number.get(marker, ""))
        values.discard("")
        values.discard(operator_id)
        adjacency[operator_id] = values
    return adjacency


def _terminal_operator_ids(item: Mapping[str, Any]) -> Set[str]:
    state = item.get("search_state")
    if not isinstance(state, Mapping):
        state = item.get("multi_operator_search_state")
    terminal = {"completed", "duplicate_exhausted", "not_applicable", "validation_failed", "branch_error"}
    result: Set[str] = set()
    if isinstance(state, Mapping):
        for entry in state.get("operator_plan") or []:
            if isinstance(entry, Mapping) and _clean_text(entry.get("status")) in terminal:
                operator_id = _normalize_operator(entry.get("operator_id"))
                if operator_id:
                    result.add(operator_id)
    return result


def _fact_ledger_exclusions(item: Mapping[str, Any]) -> Dict[str, str]:
    """Use only an explicitly authoritative and complete fact ledger.

    The current pipeline normally has no such ledger.  Missing or partial facts
    deliberately leave every otherwise runnable operator eligible.
    """

    ledger = item.get("fact_ledger")
    if not isinstance(ledger, Mapping):
        return {}
    if ledger.get("authoritative") is not True or ledger.get("complete") is not True:
        return {}
    preconditions = ledger.get("operator_preconditions")
    if not isinstance(preconditions, Mapping):
        return {}
    excluded: Dict[str, str] = {}
    for operator_id, raw_value in preconditions.items():
        normalized = _normalize_operator(operator_id)
        if not normalized:
            continue
        unsatisfied = raw_value is False or (
            isinstance(raw_value, Mapping) and raw_value.get("satisfied") is False
        )
        if unsatisfied:
            excluded[normalized] = "authoritative_fact_ledger_precondition_false"
    return excluded


def eligible_operator_ids(
    item: Mapping[str, Any],
    *,
    avoid_operators: Sequence[str] = (),
) -> Tuple[List[str], Dict[str, str]]:
    """Return the one ordered candidate space shared by cards and validation."""

    avoid = {_clean_text(operator_id) for operator_id in avoid_operators if _clean_text(operator_id)}
    completed = _terminal_operator_ids(item)
    ledger_exclusions = _fact_ledger_exclusions(item)
    source_analysis = analyze_source(item)
    mode_decision = resolve_evolution_mode(item, source_analysis)
    evolution_mode = mode_decision["evolution_mode"]
    eligible: List[str] = []
    excluded: Dict[str, str] = {}
    for operator_id, spec in OPERATOR_SPECS.items():
        policy = runtime_policy(operator_id)
        if not bool(getattr(spec, "generates_question", True)) or not bool(policy["generation_enabled"]):
            excluded[operator_id] = "generation_disabled"
        elif bool(policy["validation_only"]):
            excluded[operator_id] = "validation_only"
        elif _clean_text(policy["qualification_status"]) == "suspended":
            excluded[operator_id] = "suspended"
        elif operator_id in avoid:
            excluded[operator_id] = "avoid_operators"
        elif operator_id in completed:
            excluded[operator_id] = "parent_terminal"
        elif operator_id in ledger_exclusions:
            excluded[operator_id] = ledger_exclusions[operator_id]
        elif evolution_mode not in get_execution_contract(operator_id).supported_modes:
            excluded[operator_id] = "mode_not_supported"
        else:
            eligible.append(operator_id)
    return eligible, excluded


def _normalize_reference_materials(item: Mapping[str, Any]) -> List[str]:
    raw_values: List[Any] = []
    meta_info = item.get("meta_info")
    if isinstance(meta_info, Mapping):
        raw_values.extend(meta_info.get("references") if isinstance(meta_info.get("references"), list) else [])
        raw_values.extend(meta_info.get("answers_list") if isinstance(meta_info.get("answers_list"), list) else [])
    for field in ("reference_answer", "answer_from_book"):
        raw_values.append(item.get(field))
    values: List[str] = []
    for raw_value in raw_values:
        if not isinstance(raw_value, str):
            continue
        normalized = " ".join(raw_value.split())
        if normalized and normalized not in values:
            values.append(normalized)
    return values


def _candidate_answer_for_router(item: Mapping[str, Any]) -> str:
    scoring_result = item.get("scoring_result")
    if isinstance(scoring_result, Mapping) and isinstance(scoring_result.get("candidate_answer"), str):
        return scoring_result["candidate_answer"].strip()
    return _clean_text(item.get("candidate_answer"))


def _memory_operator_ids(matches: Mapping[str, Any]) -> List[str]:
    result: List[str] = []
    for group in ("operator", "failure"):
        rows = matches.get(group) if isinstance(matches, Mapping) else []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping):
                operator_id = _operator_from_failure_record(dict(row))
                if operator_id and operator_id not in result:
                    result.append(operator_id)
    return result


def _recommended_operator_ids(item: Mapping[str, Any]) -> List[str]:
    return _recommended_next_methods(dict(item))


def _operator_cards(operator_ids: Sequence[str], adjacency: Mapping[str, Set[str]]) -> List[Dict[str, Any]]:
    cards: List[Dict[str, Any]] = []
    for operator_id in operator_ids:
        spec = OPERATOR_SPECS[operator_id]
        gate = routing_card_gate(operator_id)
        adjacent_boundaries = [
            str(value)
            for value in (getattr(spec, "adjacent_operator_boundaries", ()) or ())
            if str(value).strip()
        ]
        if not adjacent_boundaries:
            adjacent_boundaries = ["与相邻算子的边界由注册表定义。"]
        cards.append(
            {
                "operator_id": operator_id,
                "reasoning_object": _clean_text(getattr(spec, "reasoning_object", "")) or _clean_text(getattr(spec, "ability_axis", "")),
                "when_to_use": _clean_text(getattr(spec, "goal", "")),
                "required_slots": gate["required_slots"],
                "reject_if_missing": gate["reject_if_missing"],
                "adjacent_boundaries": adjacent_boundaries,
            }
        )
    return cards


def _evidence_source_text(payload: Mapping[str, Any]) -> str:
    """Concatenate only sample-input strings, deliberately excluding cards."""

    parts: List[str] = []

    def visit(value: Any, *, include: bool = True) -> None:
        if isinstance(value, Mapping):
            for key, child in value.items():
                visit(
                    child,
                    include=include and key not in {"operator_cards", "frontier_route"},
                )
        elif isinstance(value, list):
            for child in value:
                visit(child, include=include)
        elif include and isinstance(value, str) and value:
            parts.append(value)

    visit(payload)
    return "\n".join(parts)


def _build_compact_router_input(
    item: Mapping[str, Any],
    *,
    settings: RouterSettings,
    rule_route: Mapping[str, Any],
    eligible_ids: Sequence[str],
    adjacency: Mapping[str, Set[str]],
) -> Dict[str, Any]:
    profile = item.get("sample_profile") if isinstance(item.get("sample_profile"), Mapping) else {}
    diagnosis = item.get("overscore_diagnosis") if isinstance(item.get("overscore_diagnosis"), Mapping) else {}
    core_diagnosis = {
        field: diagnosis.get(field)
        for field in ("is_worth_evolving", "candidate_overscore_cause", "target_failure_mode")
        if field in diagnosis
    }
    return {
        "sample_id": item.get("sample_id", item.get("index")),
        "score_rate": get_score_rate(dict(item)),
        "evolution_action": get_evolution_action(dict(item)),
        "prompt": _clean_text(item.get("prompt")),
        "candidate_answer": _candidate_answer_for_router(item),
        "reference_materials": _normalize_reference_materials(item),
        "sample_profile": dict(profile),
        "overscore_diagnosis": core_diagnosis,
        "routing_mode": settings.routing_mode,
        "assignment_mode": settings.assignment_mode,
        "avoid_operator_ids": list(rule_route.get("avoid_operators") or []),
        "recommended_operator_ids": _recommended_operator_ids(item),
        "memory_operator_ids": _memory_operator_ids(rule_route.get("memory_matches") or {}),
        "frontier_route": frontier_route_context(item),
        "eligible_operator_ids": list(eligible_ids),
        "operator_cards": _operator_cards(eligible_ids, adjacency),
    }


def _registry_revision() -> str:
    return _digest_json(
        [
            {
                "operator_id": operator_id,
                "generates_question": bool(getattr(spec, "generates_question", True)),
                "generation_enabled": bool(runtime_policy(operator_id)["generation_enabled"]),
                "validation_only": bool(runtime_policy(operator_id)["validation_only"]),
                "qualification_status": _clean_text(runtime_policy(operator_id)["qualification_status"]),
                "adjacent_operator_boundaries": list(getattr(spec, "adjacent_operator_boundaries", ()) or ()),
            }
            for operator_id, spec in OPERATOR_SPECS.items()
        ]
    )


def _cache_key(compact_input: Mapping[str, Any], settings: RouterSettings) -> str:
    return _digest_json(
        {
            "compact_input": compact_input,
            "cache_policy": settings.cache_policy(),
            "registry_revision": _registry_revision(),
            "memory_snapshot": _digest_json(
                {
                    # The global-memory compiler controls this frozen ID.  It
                    # makes cache reuse safe across Session snapshots without
                    # making global memory an online routing policy.
                    "memory_snapshot_id": os.getenv("MEMORY_SNAPSHOT_ID", ""),
                    "memory_operator_ids": compact_input.get("memory_operator_ids", []),
                    "avoid_operator_ids": compact_input.get("avoid_operator_ids", []),
                    "recommended_operator_ids": compact_input.get("recommended_operator_ids", []),
                }
            ),
        }
    )


def _parsed_response_to_dict(parsed: ParsedRouterResponse) -> Dict[str, Any]:
    return {
        "routing_schema_version": parsed.routing_schema_version,
        "reasoning_objects": parsed.reasoning_objects,
        "valid_candidates": parsed.valid_candidates,
        "rejected_candidates": parsed.rejected_candidates,
        "operator_decision_audit": parsed.operator_decision_audit,
        "not_selected_reasons": parsed.not_selected_reasons,
        "router_comment": parsed.router_comment,
    }


def _parsed_response_from_dict(value: Mapping[str, Any]) -> ParsedRouterResponse:
    return ParsedRouterResponse(
        routing_schema_version=_clean_text(value.get("routing_schema_version")),
        reasoning_objects=list(value.get("reasoning_objects") or []),
        valid_candidates=list(value.get("valid_candidates") or []),
        rejected_candidates=list(value.get("rejected_candidates") or []),
        operator_decision_audit=dict(value.get("operator_decision_audit") or {
            "selected_operator_rationales": [],
            "not_selected_operator_rationales": [],
            "uncertain_operator_rationales": [],
            "operator_improvement_notes": [],
        }),
        not_selected_reasons=list(value.get("not_selected_reasons") or []),
        router_comment=_clean_text(value.get("router_comment")),
    )


def _minimal_operator_filter(
    operator_ids: Iterable[Any],
    *,
    eligible_ids: Sequence[str],
) -> List[str]:
    eligible = set(eligible_ids)
    selected: List[str] = []
    for raw_operator_id in operator_ids:
        operator_id = _clean_text(raw_operator_id)
        if operator_id and operator_id in eligible and operator_id not in selected:
            selected.append(operator_id)
    return selected


def _base_hybrid_route(
    rule_route: Mapping[str, Any],
    *,
    settings: RouterSettings,
    eligible_ids: Sequence[str],
    excluded: Mapping[str, str],
) -> Dict[str, Any]:
    route = dict(rule_route)
    deterministic_fallback_operator_ids = _minimal_operator_filter(
        [rule_route.get("primary_operator"), *(rule_route.get("backup_operators") or [])],
        eligible_ids=eligible_ids,
    )
    route.update(
        {
            "route_revision": ROUTE_REVISION,
            "routing_mode": settings.routing_mode,
            "assignment_mode": settings.assignment_mode,
            "routing_schema_version": ROUTING_SCHEMA_VERSION,
            "router_prompt_version": ROUTER_PROMPT_VERSION,
            "router_transport_policy_version": ROUTER_TRANSPORT_POLICY_VERSION,
            "router_registry_policy_version": ROUTER_REGISTRY_POLICY_VERSION,
            "router_registry_revision": _registry_revision(),
            "router_model": settings.model,
            "router_provider_id": settings.provider_id,
            "router_timeout_seconds": settings.timeout_seconds,
            "router_retries": settings.retries,
            "router_concurrency": settings.concurrency,
            "eligible_operator_ids": list(eligible_ids),
            "executable_operator_ids": list(eligible_ids),
            "excluded_operator_reasons": dict(excluded),
            "selected_operator_ids": [],
            "primary_operator": None,
            "backup_operators": [],
            "deterministic_fallback_operator_ids": deterministic_fallback_operator_ids,
            "logical_task_count": 0,
            "http_attempt_count": 0,
            "router_cache_hit": False,
            "router_input_tokens": None,
            "router_output_tokens": None,
            "router_elapsed_seconds": 0.0,
            "router_rejected_candidates": [],
            "operator_decision_audit": {
                "selected_operator_rationales": [],
                "not_selected_operator_rationales": [],
                "uncertain_operator_rationales": [],
                "operator_improvement_notes": [],
            },
            "router_fallback_used": False,
            "router_raw_candidate_count": 0,
            "router_valid_candidate_count": 0,
        }
    )
    return route


def _fallback_route(
    route: Mapping[str, Any],
    *,
    eligible_ids: Sequence[str],
    error_classification: str,
    error_detail: str,
) -> Dict[str, Any]:
    result = dict(route)
    candidates = _minimal_operator_filter(
        route.get("deterministic_fallback_operator_ids")
        or [route.get("primary_operator"), *(route.get("backup_operators") or [])],
        eligible_ids=eligible_ids,
    )
    result.update(
        {
            "route_source": "deterministic_fallback",
            "router_status": "fallback",
            "router_error_classification": error_classification,
            "router_error_detail": error_detail,
            "router_fallback_used": True,
            "selected_operator_ids": candidates,
            "primary_operator": candidates[0] if candidates else None,
            "backup_operators": candidates[1:],
        }
    )
    if not candidates:
        result["router_status"] = "excluded"
        result["router_error_classification"] = "no_eligible_fallback_candidate"
    return result


async def _call_router(
    client: Any,
    prompt: str,
) -> RouterCallResult:
    result = await client.route(prompt)
    if not isinstance(result, RouterCallResult):
        raise TypeError("Router client must return RouterCallResult")
    return result


async def route_records_hybrid_async(
    records: Sequence[Dict[str, Any]],
    *,
    operator_memory: Sequence[Dict[str, Any]] = (),
    failure_memory: Sequence[Dict[str, Any]] = (),
    full_score_threshold: float = 0.99,
    failure_memory_window_rounds: int = FAILURE_MEMORY_WINDOW_ROUNDS,
    settings: RouterSettings,
    cache: Optional[RouterCache] = None,
    client: Optional[Any] = None,
    trace_store: Optional[TraceStore] = None,
    close_client: bool = False,
) -> List[Dict[str, Any]]:
    """Route all records with LLM success and deterministic-fallback paths."""

    if settings.routing_mode != ROUTING_MODE_HYBRID:
        raise ValueError("hybrid routing requires routing_mode=hybrid")
    operator_memory_index = MemoryMatchIndex(operator_memory)
    failure_memory_index = MemoryMatchIndex(failure_memory)
    adjacency = _operator_adjacency()
    cache = cache or RouterCache(None)
    owned_client = client is None
    if client is None:
        client = HybridRouterClient(settings, _configured_router_api_keys())
    semaphore = asyncio.Semaphore(settings.concurrency)
    cache_key_locks: Dict[str, asyncio.Lock] = {}
    prepared: List[Tuple[Any, ...]] = []
    results: List[Optional[Dict[str, Any]]] = [None] * len(records)

    for index, record in enumerate(records):
        rule_route = build_operator_route(
            record,
            operator_memory=operator_memory,
            failure_memory=failure_memory,
            full_score_threshold=full_score_threshold,
            failure_memory_window_rounds=failure_memory_window_rounds,
            operator_memory_index=operator_memory_index,
            failure_memory_index=failure_memory_index,
        )
        action = get_evolution_action(record)
        if action in NON_EVOLUTION_ACTIONS and not is_frontier_route(record):
            route = _base_hybrid_route(rule_route, settings=settings, eligible_ids=[], excluded={})
            route.update({"route_source": "not_requested", "router_status": "not_requested"})
            updated = dict(record)
            updated["operator_route"] = attach_live_route_integrity(route)
            results[index] = updated
            continue
        if rule_route.get("no_safe_operator") is True:
            route = _base_hybrid_route(rule_route, settings=settings, eligible_ids=[], excluded={})
            route.update({
                "route_source": "no_safe_operator",
                "router_status": "excluded",
                "router_error_classification": "no_safe_operator",
                "selected_operator_ids": [],
                "primary_operator": None,
                "backup_operators": [],
            })
            updated = dict(record)
            updated["operator_route"] = attach_live_route_integrity(route)
            results[index] = updated
            continue
        eligible_ids, excluded = eligible_operator_ids(
            record,
            avoid_operators=rule_route.get("avoid_operators") or [],
        )
        route = _base_hybrid_route(
            rule_route,
            settings=settings,
            eligible_ids=eligible_ids,
            excluded=excluded,
        )
        if not eligible_ids:
            route.update(
                {
                    "route_source": "excluded",
                    "router_status": "excluded",
                    "router_error_classification": "no_eligible_operator",
                }
            )
            updated = dict(record)
            updated["operator_route"] = attach_live_route_integrity(route)
            results[index] = updated
            continue
        compact_input = _build_compact_router_input(
            record,
            settings=settings,
            rule_route=rule_route,
            eligible_ids=eligible_ids,
            adjacency=adjacency,
        )
        evidence_text = _evidence_source_text(compact_input)
        prompt = build_router_prompt(record, compact_input=compact_input)
        prepared.append(
            (
                index,
                dict(record),
                route,
                compact_input,
                eligible_ids,
                excluded,
                {operator_id: adjacency.get(operator_id, set()) for operator_id in eligible_ids},
                evidence_text,
                prompt,
                _cache_key(compact_input, settings),
            )
        )

    async def run_prepared(
        entry: Tuple[Any, ...]
    ) -> None:
        (
            index,
            record,
            base_route,
            compact_input,
            eligible_ids,
            _excluded,
            adjacency_for_eligible,
            evidence_text,
            prompt,
            cache_key,
        ) = entry
        route = dict(base_route)
        raw_candidate_count = 0
        trace_id: Optional[str] = None
        call_result: Optional[RouterCallResult] = None
        try:
            key_lock = cache_key_locks.setdefault(cache_key, asyncio.Lock())
            async with key_lock:
                lock = cache.key_lock(cache_key)
                if lock is not None:
                    await asyncio.to_thread(lock.acquire)
                try:
                    cached = cache.reload_key(cache_key)
                    if cached is not None:
                        parsed = _parsed_response_from_dict(cached["parsed_response"])
                        cache_hit = True
                        call_result = None
                        raw_candidate_count = int(cached.get("raw_candidate_count") or len(parsed.valid_candidates))
                    else:
                        async with semaphore:
                            call_result = await asyncio.wait_for(
                                _call_router(client, prompt),
                                timeout=settings.timeout_seconds,
                            )
                        if trace_store is not None:
                            trace_id = trace_store.add(
                                record_key=stable_record_key(record),
                                raw_text=call_result.raw_response,
                                trace_kind="router_response",
                                metadata={"cache_key": cache_key, "provider_id": settings.provider_id},
                            )
                        raw_candidate_count = 0
                        try:
                            raw_payload = json.loads(call_result.raw_response)
                            if isinstance(raw_payload, Mapping) and isinstance(raw_payload.get("operator_candidates"), list):
                                raw_candidate_count = len(raw_payload["operator_candidates"])
                        except json.JSONDecodeError:
                            pass
                        parsed = parse_router_response(
                            call_result.raw_response,
                            eligible_operator_ids=eligible_ids,
                            adjacent_operator_ids=adjacency_for_eligible,
                            evidence_source_text=evidence_text,
                        )
                        cache.put_success(
                            cache_key,
                            _parsed_response_to_dict(parsed),
                            raw_candidate_count=raw_candidate_count,
                        )
                        cache_hit = False
                finally:
                    if lock is not None:
                        await asyncio.to_thread(lock.release)

            # The parser guarantees that every valid candidate is a hard-slot
            # selected candidate.  Audit records remain attached solely for
            # human review and are deliberately not consulted here.
            selected = [candidate["operator_id"] for candidate in parsed.valid_candidates]
            route.update(
                {
                    "route_source": "llm",
                    "router_status": "succeeded",
                    "router_error_classification": None,
                    "selected_operator_ids": selected,
                    "primary_operator": selected[0] if selected else None,
                    "backup_operators": selected[1:],
                    "router_reasoning_objects": parsed.reasoning_objects,
                    "router_candidates": parsed.valid_candidates,
                    "router_rejected_candidates": parsed.rejected_candidates,
                    "operator_decision_audit": parsed.operator_decision_audit,
                    "router_not_selected_reasons": parsed.not_selected_reasons,
                    "router_comment": parsed.router_comment,
                    "router_fallback_used": False,
                    "router_cache_hit": cache_hit,
                    "logical_task_count": 1,
                    "http_attempt_count": 0 if cache_hit else 1,
                    "router_raw_candidate_count": raw_candidate_count,
                    "router_valid_candidate_count": len(parsed.valid_candidates),
                    "router_input_tokens": call_result.input_tokens if call_result else None,
                    "router_output_tokens": call_result.output_tokens if call_result else None,
                    "router_elapsed_seconds": call_result.elapsed_seconds if call_result else 0.0,
                }
            )
            if trace_id:
                route["router_raw_response_trace_id"] = trace_id
        except RouterContractError as exc:
            route = _fallback_route(
                route,
                eligible_ids=eligible_ids,
                error_classification=exc.classification,
                error_detail=str(exc),
            )
            route["logical_task_count"] = 1
            route["http_attempt_count"] = 1
            route["router_raw_candidate_count"] = raw_candidate_count
            route["router_input_tokens"] = call_result.input_tokens if call_result else None
            route["router_output_tokens"] = call_result.output_tokens if call_result else None
            route["router_elapsed_seconds"] = call_result.elapsed_seconds if call_result else 0.0
            if trace_id:
                route["router_raw_response_trace_id"] = trace_id
        except asyncio.TimeoutError:
            route = _fallback_route(
                route,
                eligible_ids=eligible_ids,
                error_classification="timeout",
                error_detail="router request timed out",
            )
            route["logical_task_count"] = 1
            route["http_attempt_count"] = 1
            route["router_raw_candidate_count"] = raw_candidate_count
            route["router_input_tokens"] = call_result.input_tokens if call_result else None
            route["router_output_tokens"] = call_result.output_tokens if call_result else None
            route["router_elapsed_seconds"] = call_result.elapsed_seconds if call_result else 0.0
            if trace_id:
                route["router_raw_response_trace_id"] = trace_id
        except Exception as exc:
            detail = str(exc)
            classification = "empty_response" if "empty_response" in detail else "network_error"
            route = _fallback_route(
                route,
                eligible_ids=eligible_ids,
                error_classification=classification,
                error_detail=detail,
            )
            route["logical_task_count"] = 1
            route["http_attempt_count"] = 1
            route["router_raw_candidate_count"] = raw_candidate_count
            route["router_input_tokens"] = call_result.input_tokens if call_result else None
            route["router_output_tokens"] = call_result.output_tokens if call_result else None
            route["router_elapsed_seconds"] = call_result.elapsed_seconds if call_result else 0.0
            if trace_id:
                route["router_raw_response_trace_id"] = trace_id
        updated = dict(record)
        updated["operator_route"] = attach_live_route_integrity(route)
        results[index] = updated

    try:
        await asyncio.gather(*(run_prepared(entry) for entry in prepared))
    finally:
        if owned_client or close_client:
            await client.close()
    return [record for record in results if record is not None]


def attach_operator_route(
    item: Dict[str, Any],
    *,
    operator_memory: Sequence[Dict[str, Any]] = (),
    failure_memory: Sequence[Dict[str, Any]] = (),
    full_score_threshold: float = 0.99,
    failure_memory_window_rounds: int = FAILURE_MEMORY_WINDOW_ROUNDS,
    operator_memory_index: Optional[MemoryMatchIndex] = None,
    failure_memory_index: Optional[MemoryMatchIndex] = None,
) -> Dict[str, Any]:
    result = dict(item)
    result["operator_route"] = build_operator_route(
        item,
        operator_memory=operator_memory,
        failure_memory=failure_memory,
        full_score_threshold=full_score_threshold,
        failure_memory_window_rounds=failure_memory_window_rounds,
        operator_memory_index=operator_memory_index,
        failure_memory_index=failure_memory_index,
    )
    return result


def route_records(
    records: Sequence[Dict[str, Any]],
    *,
    operator_memory: Sequence[Dict[str, Any]] = (),
    failure_memory: Sequence[Dict[str, Any]] = (),
    full_score_threshold: float = 0.99,
    failure_memory_window_rounds: int = FAILURE_MEMORY_WINDOW_ROUNDS,
    routing_mode: str = ROUTING_MODE_RULE,
    assignment_mode: str = ASSIGNMENT_MODE_NATURAL,
    router_model: Optional[str] = None,
    router_base_url: Optional[str] = None,
    router_timeout_seconds: float = DEFAULT_ROUTER_TIMEOUT_SECONDS,
    router_retries: int = DEFAULT_ROUTER_RETRIES,
    router_concurrency: int = DEFAULT_ROUTER_CONCURRENCY,
    router_cache: Optional[str] = None,
) -> List[Dict[str, Any]]:
    settings = RouterSettings.from_values(
        routing_mode=routing_mode,
        assignment_mode=assignment_mode,
        model=router_model,
        base_url=router_base_url,
        timeout_seconds=router_timeout_seconds,
        retries=router_retries,
        concurrency=router_concurrency,
    )
    if settings.routing_mode == ROUTING_MODE_HYBRID:
        return asyncio.run(
            route_records_hybrid_async(
                records,
                operator_memory=operator_memory,
                failure_memory=failure_memory,
                full_score_threshold=full_score_threshold,
                failure_memory_window_rounds=failure_memory_window_rounds,
                settings=settings,
                cache=RouterCache(router_cache),
            )
        )
    operator_memory_index = MemoryMatchIndex(operator_memory)
    failure_memory_index = MemoryMatchIndex(failure_memory)
    return [
        attach_operator_route(
            record,
            operator_memory=operator_memory,
            failure_memory=failure_memory,
            full_score_threshold=full_score_threshold,
            failure_memory_window_rounds=failure_memory_window_rounds,
            operator_memory_index=operator_memory_index,
            failure_memory_index=failure_memory_index,
        )
        for record in records
    ]


def build_router_report(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    warn_count = 0
    downrank_count = 0
    avoid_count = 0
    distribution: Counter = Counter()
    router_statuses: Counter = Counter()
    router_errors: Counter = Counter()
    logical_tasks = 0
    http_attempts = 0
    cache_hits = 0
    raw_candidates = 0
    valid_candidates = 0
    fallback_count = 0
    selected_counts: List[int] = []
    for record in records:
        route = record.get("operator_route")
        route = route if isinstance(route, dict) else {}
        warn_count += len(route.get("memory_warnings") or [])
        downrank_count += len(route.get("downrank_operator_surface_forms") or [])
        avoid_count += len(route.get("avoid_operator_surface_forms") or [])
        router_statuses[_clean_text(route.get("router_status")) or "legacy_rule"] += 1
        error = _clean_text(route.get("router_error_classification"))
        if error:
            router_errors[error] += 1
        logical_tasks += int(route.get("logical_task_count") or 0)
        http_attempts += int(route.get("http_attempt_count") or 0)
        cache_hits += int(bool(route.get("router_cache_hit")))
        raw_candidates += int(route.get("router_raw_candidate_count") or 0)
        valid_candidates += int(route.get("router_valid_candidate_count") or 0)
        fallback_count += int(bool(route.get("router_fallback_used")))
        selected_counts.append(len(route.get("selected_operator_ids") or []))
        for field in ("memory_warnings", "downrank_operator_surface_forms", "avoid_operator_surface_forms"):
            for action in route.get(field) or []:
                key = f"{_clean_text(action.get('operator_used'))}+{_clean_text(action.get('surface_form_family'))}+{_clean_text(action.get('failure_type'))}"
                distribution[key] += 1
    return {
        "total_records": len(records),
        "failure_memory_warn_only_count": warn_count,
        "failure_memory_downrank_count": downrank_count,
        "failure_memory_avoid_count": avoid_count,
        "operator_surface_form_failure_distribution": dict(sorted(distribution.items())),
        "router_statuses": dict(sorted(router_statuses.items())),
        "router_error_classifications": dict(sorted(router_errors.items())),
        "router_logical_task_count": logical_tasks,
        "router_http_attempt_count": http_attempts,
        "router_cache_hit_count": cache_hits,
        "router_raw_candidate_count": raw_candidates,
        "router_valid_candidate_count": valid_candidates,
        "router_fallback_count": fallback_count,
        "router_selected_candidate_counts": selected_counts,
    }


def write_json(data: Dict[str, Any], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def _router_manifest_config(
    *,
    args: argparse.Namespace,
    settings: RouterSettings,
    operator_memory_path: str,
    failure_memory_path: str,
    router_cache_path: Optional[str],
) -> Dict[str, Any]:
    """Keep router publication and resume validation on one config contract."""

    return {
        "operator_memory_path": os.path.abspath(operator_memory_path),
        "failure_memory_path": os.path.abspath(failure_memory_path),
        "operator_memory_sha256": (
            sha256_file(operator_memory_path)
            if os.path.isfile(operator_memory_path)
            else None
        ),
        "failure_memory_sha256": (
            sha256_file(failure_memory_path)
            if os.path.isfile(failure_memory_path)
            else None
        ),
        "full_score_threshold": args.full_score_threshold,
        "failure_memory_window_rounds": max(1, args.failure_memory_window_rounds),
        "routing_mode": settings.routing_mode,
        "assignment_mode": settings.assignment_mode,
        "router_model": settings.model,
        "router_provider_id": settings.provider_id,
        "router_timeout_seconds": settings.timeout_seconds,
        "router_retries": settings.retries,
        "router_concurrency": settings.concurrency,
        "router_cache": os.path.abspath(router_cache_path) if router_cache_path else None,
        "memory_snapshot_id": args.memory_snapshot_id or None,
        "route_revision": ROUTE_REVISION if settings.routing_mode == ROUTING_MODE_HYBRID else None,
    }


def _router_integrity_manifest(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    fingerprints: List[str] = []
    for record in records:
        route = record.get("operator_route") if isinstance(record, Mapping) else None
        if not isinstance(route, Mapping):
            continue
        if route.get("routing_mode") == ROUTING_MODE_HYBRID and route.get("assignment_mode") == ASSIGNMENT_MODE_LIVE:
            validate_live_route_integrity(route)
            fingerprint = _clean_text(route.get("route_fingerprint"))
            if fingerprint not in fingerprints:
                fingerprints.append(fingerprint)
    return {
        "route_integrity_version": "live-route-integrity-v1",
        "live_route_fingerprints": sorted(fingerprints),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Route profiled evolution candidates to question operators.")
    parser.add_argument("--input", required=True, help="Input profiled_candidates JSON/JSONL path.")
    parser.add_argument("--output", required=True, help="Output routed JSONL path.")
    parser.add_argument("--memory-dir", default="memory", help="Directory containing memory bank JSONL files.")
    parser.add_argument("--operator-memory", default=None, help="Override operator memory JSONL path.")
    parser.add_argument("--failure-memory", default=None, help="Override failure memory JSONL path.")
    parser.add_argument(
        "--full-score-threshold",
        type=float,
        default=0.99,
        help="Score-rate threshold used by no-repeat rules.",
    )
    parser.add_argument(
        "--failure-memory-window-rounds",
        type=int,
        default=FAILURE_MEMORY_WINDOW_ROUNDS,
        help="Recent round window used for operator+surface-form failure memory convergence.",
    )
    parser.add_argument(
        "--routing-mode",
        choices=[ROUTING_MODE_RULE, ROUTING_MODE_HYBRID],
        default=os.getenv("ROUTING_MODE", ROUTING_MODE_RULE),
    )
    parser.add_argument(
        "--assignment-mode",
        choices=[ASSIGNMENT_MODE_NATURAL, ASSIGNMENT_MODE_LIVE],
        default=os.getenv("ASSIGNMENT_MODE", ASSIGNMENT_MODE_NATURAL),
    )
    parser.add_argument("--router-model", default=os.getenv("ROUTER_MODEL") or None)
    parser.add_argument("--router-base-url", default=os.getenv("ROUTER_BASE_URL") or None)
    parser.add_argument(
        "--router-api-key",
        action="append",
        default=None,
        help="May be supplied repeatedly; otherwise use Router/GPT provider configuration.",
    )
    parser.add_argument(
        "--router-timeout",
        type=float,
        default=float(os.getenv("ROUTER_TIMEOUT", str(DEFAULT_ROUTER_TIMEOUT_SECONDS))),
    )
    parser.add_argument(
        "--router-retries",
        type=int,
        default=int(os.getenv("ROUTER_RETRIES", str(DEFAULT_ROUTER_RETRIES))),
    )
    parser.add_argument(
        "--router-concurrency",
        type=int,
        default=int(os.getenv("ROUTER_CONCURRENCY", str(DEFAULT_ROUTER_CONCURRENCY))),
    )
    parser.add_argument(
        "--router-cache",
        default=None,
        help="Append-only successful-route cache JSONL. Defaults to memory/router_cache.jsonl in hybrid mode.",
    )
    parser.add_argument("--memory-snapshot-id", default=os.getenv("MEMORY_SNAPSHOT_ID", ""), help="Frozen audit snapshot; affects cache identity only.")
    parser.add_argument(
        "--router-trace-output",
        default=None,
        help="Compressed raw Router response trace sidecar; defaults beside --output in hybrid mode.",
    )
    parser.add_argument("--report-output", default=None, help="Optional operator-router memory action report JSON path.")
    parser.add_argument("--performance-events", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stage = "operator_router"
    metrics = StageMetrics(stage)
    metrics.input_bytes = os.path.getsize(args.input)
    operator_memory_path = args.operator_memory or os.path.join(args.memory_dir, "operator_memory_bank.jsonl")
    failure_memory_path = args.failure_memory or os.path.join(args.memory_dir, "failure_memory_bank.jsonl")
    parse_started = time.monotonic()
    records = load_json_or_jsonl(args.input)
    operator_memory = load_jsonl_if_exists(operator_memory_path)
    failure_memory = load_jsonl_if_exists(failure_memory_path)
    metrics.parse_seconds += time.monotonic() - parse_started
    compute_started = time.monotonic()
    settings = RouterSettings.from_values(
        routing_mode=args.routing_mode,
        assignment_mode=args.assignment_mode,
        model=args.router_model,
        base_url=args.router_base_url,
        timeout_seconds=args.router_timeout,
        retries=args.router_retries,
        concurrency=args.router_concurrency,
    )
    if settings.routing_mode == ROUTING_MODE_HYBRID and settings.assignment_mode != ASSIGNMENT_MODE_LIVE:
        raise ValueError("hybrid routing requires --assignment-mode live; historical modes must not be silently upgraded")
    router_cache_path = args.router_cache or (
        os.path.join(args.memory_dir, "router_cache.jsonl")
        if settings.routing_mode == ROUTING_MODE_HYBRID
        else None
    )
    router_trace_path = args.router_trace_output or (
        args.output + ".router_traces.jsonl.gz"
        if settings.routing_mode == ROUTING_MODE_HYBRID
        else None
    )
    manifest_config = _router_manifest_config(
        args=args,
        settings=settings,
        operator_memory_path=operator_memory_path,
        failure_memory_path=failure_memory_path,
        router_cache_path=router_cache_path,
    )
    if os.path.exists(args.output):
        valid, reason = validate_published_artifact(
            args.output,
            stage=stage,
            input_path=args.input,
            config=manifest_config,
        )
        if valid:
            if settings.routing_mode == ROUTING_MODE_HYBRID and settings.assignment_mode == ASSIGNMENT_MODE_LIVE:
                _router_integrity_manifest(load_json_or_jsonl(args.output))
            return
        raise ValueError(
            "existing Router artifact is incompatible or incomplete "
            f"({reason}); start a new experiment instead of mixing route revisions"
        )
    trace_store = (
        TraceStore(stage, recovery_path=router_trace_path + ".partial")
        if router_trace_path
        else None
    )
    if settings.routing_mode == ROUTING_MODE_HYBRID:
        routed = asyncio.run(
            route_records_hybrid_async(
                records,
                operator_memory=operator_memory,
                failure_memory=failure_memory,
                full_score_threshold=args.full_score_threshold,
                failure_memory_window_rounds=max(1, args.failure_memory_window_rounds),
                settings=settings,
                cache=RouterCache(router_cache_path),
                client=HybridRouterClient(settings, _configured_router_api_keys(args.router_api_key)),
                trace_store=trace_store,
                close_client=True,
            )
        )
    else:
        routed = route_records(
            records,
            operator_memory=operator_memory,
            failure_memory=failure_memory,
            full_score_threshold=args.full_score_threshold,
            failure_memory_window_rounds=max(1, args.failure_memory_window_rounds),
        )
    if args.memory_snapshot_id:
        for record in routed:
            route = record.get("operator_route")
            if isinstance(route, dict):
                route["memory_snapshot_id"] = args.memory_snapshot_id
    metrics.compute_seconds += time.monotonic() - compute_started
    sidecars: List[Tuple[str, str, int]] = []
    if trace_store is not None and router_trace_path is not None:
        trace_path, trace_count = trace_store.write(router_trace_path)
        trace_store.finalize_recovery()
        sidecars.append((trace_path, "router_raw_response_trace", trace_count))
    publish_records(
        routed,
        args.output,
        stage=stage,
        input_path=args.input,
        config=manifest_config,
        performance_path=args.performance_events,
        code_paths=[__file__],
        metrics=metrics,
        sidecars=sidecars,
        extra_manifest={"route_integrity": _router_integrity_manifest(routed)},
    )
    if args.report_output:
        write_json(build_router_report(routed), args.report_output)


if __name__ == "__main__":
    main()
