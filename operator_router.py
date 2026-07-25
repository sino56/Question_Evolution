import argparse
import json
import os
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pipeline_runtime import StageMetrics, load_json_records, publish_records, sha256_file
from operator_contracts import (
    DISABLED,
    ENABLED,
    QUALIFICATION_ONLY,
    SHADOW_ROUTING,
    VALIDATION_ONLY,
    collect_referenced_fact_ids,
    enabled_generation_operator_ids,
    extract_operator_manifest,
    get_operator_contract,
)

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

ALL_OPERATOR_ORDER = (
    O10_EVIDENCE_SUFFICIENCY_LADDER,
    O11_UNOBSERVED_STATE_ATTRIBUTION,
    O12_CONJUNCTIVE_NECESSITY,
    O13_MINIMAL_DISQUALIFIER,
    O14_INFORMATION_CLOSURE,
    O15_COUNTERFACTUAL_THRESHOLD_SHIFT,
    O16_CLOSE_ALTERNATIVE_NORMALIZATION,
    O17_ACTION_VS_FACT_THRESHOLD,
    O18_BASELINE_SCOPE_MISMATCH,
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
OPERATOR_ORDER = tuple(
    operator_id
    for operator_id in ALL_OPERATOR_ORDER
    if operator_id in set(enabled_generation_operator_ids())
)
OPERATOR_IDS = set(ALL_OPERATOR_ORDER)

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


def _is_enabled_generation_operator(operator_id: Optional[str]) -> bool:
    if not operator_id:
        return False
    try:
        return get_operator_contract(operator_id).status == ENABLED
    except ValueError:
        return False


def _operator_registry_status(operator_id: Optional[str]) -> str:
    if not operator_id:
        return ""
    try:
        return get_operator_contract(operator_id).status
    except ValueError:
        return "unknown"


def _read_nonnegative_round(value: Any) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def get_evolution_action(item: Dict[str, Any]) -> str:
    return _clean_text(item.get("evolution_action"))


def should_route_for_evolution(item: Dict[str, Any]) -> bool:
    return get_evolution_action(item) in EVOLUTION_REQUIRED_ACTIONS


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
    profile = get_sample_profile(item)
    cause = _clean_text(diagnosis.get("candidate_overscore_cause"))
    target = _clean_text(diagnosis.get("target_failure_mode"))
    combined = " ".join(
        (
            cause,
            target,
            _clean_text(profile.get("core_capability")),
            _clean_text(profile.get("problem_shape")),
        )
    )

    # New families are recognized by their primary reasoning object.  Their
    # qualification-only status is applied later, so these rules produce a
    # shadow explanation without silently enabling production traffic.
    if _has_any(combined, ("跨模态", "多源融合", "来源冲突", "信号与视频", "跨来源")):
        return (
            O33_CROSS_MODAL_SUPPORT_BOUNDARY,
            [O23_OBSERVATION_RELIABILITY_CONFLICT, O27_CROSS_LAYER_CONCLUSION_CALIBRATION],
            "reasoning object is cross-source scope, alignment, conflict, and fusion ceiling.",
        )
    if _has_any(combined, ("观测累积", "独立增量", "同源重复", "观测依赖", "累积支持")):
        return (
            O31_OBSERVATION_ACCUMULATION_CALIBRATION,
            [O23_OBSERVATION_RELIABILITY_CONFLICT, O10_EVIDENCE_SUFFICIENCY_LADDER],
            "reasoning object is dependency and incremental support across observations.",
        )
    if _has_any(combined, ("下一观测", "主动判别", "判别观测", "信息选择", "区分力观测")):
        return (
            O30_ACTIVE_DISCRIMINATIVE_OBSERVATION,
            [O24_MULTI_HYPOTHESIS_RESIDUAL_RANKING, O16_CLOSE_ALTERNATIVE_NORMALIZATION],
            "reasoning object is selection of the next discriminative observation.",
        )
    if _has_any(combined, ("同一性冲突", "冲突绑定", "实体消解", "跨镜头同一性", "全程同一")):
        return (
            O29_ENTITY_IDENTITY_CONFLICT_RESOLUTION,
            [O19_MULTI_ENTITY_ROLE_BINDING, O21_OBJECT_PROVENANCE_IDENTITY],
            "reasoning object is identity resolution under conflicting bindings.",
        )
    if _has_any(combined, ("整体链路闭合", "跨节点链路", "跨阶段链路", "多跳链路", "跨跳绑定")):
        return (
            O28_MULTIHOP_CHAIN_CLOSURE,
            [O20_MULTISTAGE_EVENT_BREAKPOINT, O22_PATH_TOPOLOGY_REACHABILITY],
            "reasoning object is whole-chain closure across stages and observation nodes.",
        )
    if _has_any(combined, ("关系图关键边", "角色关系图", "协同关键边", "有向关系边", "共现不等于协同")):
        return (
            O32_ROLE_GRAPH_CRITICAL_EDGE,
            [O19_MULTI_ENTITY_ROLE_BINDING, O13_MINIMAL_DISQUALIFIER],
            "reasoning object is directed role relation graph and critical-edge necessity.",
        )
    if _has_any(combined, ("跨层结论", "结论层级映射", "证据上推", "支持度上推", "最高允许结论")):
        return (
            O27_CROSS_LAYER_CONCLUSION_CALIBRATION,
            [O13_MINIMAL_DISQUALIFIER, O17_ACTION_VS_FACT_THRESHOLD],
            "reasoning object is legal propagation across claim layers.",
        )
    if _has_any(combined, ("误差传播", "不确定区间", "区间阈值", "容差", "单位换算")):
        return (
            O26_QUANTITATIVE_THRESHOLD_PROPAGATION,
            [O18_BASELINE_SCOPE_MISMATCH, O15_COUNTERFACTUAL_THRESHOLD_SHIFT],
            "reasoning object is uncertainty propagation and threshold relation.",
        )
    if _has_any(combined, ("参照系", "程序不变量", "记录映射", "单位不一致", "步骤依赖")):
        return (
            O25_PROCEDURAL_INVARIANT_FRAME,
            [O12_CONJUNCTIVE_NECESSITY],
            "reasoning object is procedural, reference-frame, unit, and record-mapping invariance.",
        )
    if _has_any(combined, ("多假设残差", "覆盖冲突残差", "假设排序", "额外假设成本", "残差矩阵")):
        return (
            O24_MULTI_HYPOTHESIS_RESIDUAL_RANKING,
            [O16_CLOSE_ALTERNATIVE_NORMALIZATION],
            "reasoning object is the coverage-conflict-residual structure of competing hypotheses.",
        )
    if _has_any(combined, ("观测质量", "可见性", "遮挡可靠性", "视角限制", "模糊观测")):
        return (
            O23_OBSERVATION_RELIABILITY_CONFLICT,
            [O10_EVIDENCE_SUFFICIENCY_LADDER],
            "reasoning object is observability and source-internal reliability.",
        )
    if _has_any(combined, ("路径拓扑", "联合可达", "路径图", "通行时间范围", "多入口多出口")):
        return (
            O22_PATH_TOPOLOGY_REACHABILITY,
            [O11_UNOBSERVED_STATE_ATTRIBUTION],
            "reasoning object is path topology with time-window and endpoint constraints.",
        )
    if _has_any(combined, ("物品来源", "物品同一性", "转移缺口", "竞争来源", "物品连续性")):
        return (
            O21_OBJECT_PROVENANCE_IDENTITY,
            [O19_MULTI_ENTITY_ROLE_BINDING],
            "reasoning object is object provenance and identity continuity.",
        )
    if _has_any(combined, ("多阶段断点", "状态转移断点", "事件链断点", "局部链完整", "状态图")):
        return (
            O20_MULTISTAGE_EVENT_BREAKPOINT,
            [O13_MINIMAL_DISQUALIFIER],
            "reasoning object is a multistage state-transition graph and its breakpoint.",
        )
    if _has_any(combined, ("多实体绑定", "角色绑定", "主体交换", "关系方向", "掩护者与实施者")):
        return (
            O19_MULTI_ENTITY_ROLE_BINDING,
            [O10_EVIDENCE_SUFFICIENCY_LADDER],
            "reasoning object is entity-to-observation binding and role direction.",
        )

    if _has_any(combined, ("盲区", "不可见区间", "未出现", "端点事实", "不可见状态")):
        return (
            O11_UNOBSERVED_STATE_ATTRIBUTION,
            [O17_ACTION_VS_FACT_THRESHOLD],
            "diagnosis indicates unobserved-state attribution risk.",
        )

    if _has_any(combined, ("基线", "样本口径", "统计口径", "范围错配", "基准范围")):
        return (
            O18_BASELINE_SCOPE_MISMATCH,
            [O10_EVIDENCE_SUFFICIENCY_LADDER],
            "diagnosis indicates baseline-scope mismatch.",
        )

    if _has_any(combined, ("正常解释", "替代解释", "风险消失", "异常强度下降")):
        return (
            O16_CLOSE_ALTERNATIVE_NORMALIZATION,
            [O15_COUNTERFACTUAL_THRESHOLD_SHIFT],
            "diagnosis indicates over-normalization by a close alternative.",
        )

    if _has_any(combined, ("反事实", "单变量", "变量变化", "门槛迁移", "保留范围")):
        return (
            O15_COUNTERFACTUAL_THRESHOLD_SHIFT,
            [O16_CLOSE_ALTERNATIVE_NORMALIZATION],
            "diagnosis calls for a single-variable threshold shift.",
        )

    if _has_any(combined, ("处置", "事实定性", "行动门槛", "报告表述", "动作层与性质层")):
        return (
            O17_ACTION_VS_FACT_THRESHOLD,
            [O11_UNOBSERVED_STATE_ATTRIBUTION, O12_CONJUNCTIVE_NECESSITY],
            "diagnosis indicates confusion between action and fact thresholds.",
        )

    if _has_any(combined, ("题外补设", "题干外", "隐藏前提", "信息闭包", "泛化罗列", "事实绑定")):
        return (
            O14_INFORMATION_CLOSURE,
            [O10_EVIDENCE_SUFFICIENCY_LADDER],
            "diagnosis indicates an information-closure violation.",
        )

    if _has_any(combined, ("原评价", "新增事实", "推翻", "下调", "最小否决", "最小关键事实", "最关键缺口")):
        return (
            O13_MINIMAL_DISQUALIFIER,
            [O15_COUNTERFACTUAL_THRESHOLD_SHIFT],
            "diagnosis calls for testing whether a new fact changes an existing evaluation.",
        )

    if _has_any(combined, ("强线索", "共同必要", "必要条件", "门槛未闭合", "层级越推", "抓显眼点漏关键层")):
        return (
            O12_CONJUNCTIVE_NECESSITY,
            [O17_ACTION_VS_FACT_THRESHOLD],
            "diagnosis indicates that a strong clue is replacing an unclosed threshold.",
        )

    if _has_any(combined, ("反常线索", "主线切换", "受干扰信息带偏", "近似项分层", "层级混淆")):
        return (
            O10_EVIDENCE_SUFFICIENCY_LADDER,
            [O15_COUNTERFACTUAL_THRESHOLD_SHIFT, O14_INFORMATION_CLOSURE],
            "diagnosis calls for close business-judgment competition.",
        )

    return (
        O10_EVIDENCE_SUFFICIENCY_LADDER,
        [O17_ACTION_VS_FACT_THRESHOLD, O14_INFORMATION_CLOSURE],
        "fallback to close business-judgment competition for an evolvable sample.",
    )


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
    if action in NON_EVOLUTION_ACTIONS:
        return {
            "primary_operator": None,
            "backup_operators": [],
            "avoid_operators": [],
            "routing_reason": f"evolution_action={action} does not require question evolution.",
            "is_high_value_sample": False,
            "should_use_local_tree_search": False,
            "memory_matches": {"operator": [], "failure": []},
        }
    if action and action not in EVOLUTION_REQUIRED_ACTIONS:
        raise ValueError(f"unsupported evolution_action: {action}")

    get_sample_profile(item)
    get_overscore_diagnosis(item)

    rule_primary, rule_backups, reason = _base_rule_route(item)
    rule_contract = get_operator_contract(rule_primary) if rule_primary else None
    rule_manifest = (
        extract_operator_manifest(item, rule_primary)
        if rule_primary
        else {}
    )
    required_slots_satisfied = []
    missing_required_slots = []
    if rule_contract:
        for slot in rule_contract.required_fact_slots:
            current: Any = rule_manifest
            for part in slot.split("."):
                if not isinstance(current, dict) or part not in current:
                    current = None
                    break
                current = current[part]
            if current in (None, "", [], {}):
                missing_required_slots.append(slot)
            else:
                required_slots_satisfied.append(slot)
    shadow_operator_plan = []
    for position, operator_id in enumerate([rule_primary] + list(rule_backups)):
        if not operator_id:
            continue
        shadow_operator_plan.append(
            {
                "operator_id": operator_id,
                "route_position": "primary" if position == 0 else f"backup_{position}",
                "registry_status": _operator_registry_status(operator_id),
            }
        )

    enabled_rule_candidates = [
        operator_id
        for operator_id in [rule_primary] + list(rule_backups)
        if _is_enabled_generation_operator(operator_id)
    ]
    primary = enabled_rule_candidates[0] if enabled_rule_candidates else None
    backups = enabled_rule_candidates[1:]
    avoid: List[str] = []
    reason_parts = [reason]
    if rule_primary and not _is_enabled_generation_operator(rule_primary):
        reason_parts.append(
            f"rule primary {rule_primary} is {_operator_registry_status(rule_primary)} and is shadow-only."
        )
    if primary is None:
        primary = next(iter(OPERATOR_ORDER), None)
        if primary:
            reason_parts.append(
                f"no enabled rule candidate remained; using enabled registry fallback {primary}."
            )
    recommended_next = _recommended_next_methods(item)
    recommended_next = [
        operator_id
        for operator_id in recommended_next
        if _is_enabled_generation_operator(operator_id)
    ]

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
        if (
            memory_operator
            and _is_enabled_generation_operator(memory_operator)
            and memory_operator not in avoid
        ):
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
            primary = next((operator for operator in OPERATOR_ORDER if operator not in avoid), None)

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

    return {
        "primary_operator": primary,
        "backup_operators": backups,
        "avoid_operators": avoid,
        "routing_reason": " ".join(reason_parts),
        "is_high_value_sample": _is_high_value_sample(item),
        "should_use_local_tree_search": should_tree,
        "operator_registry_status": _operator_registry_status(primary),
        "recognized_operator_id": rule_primary,
        "recognized_operator_registry_status": _operator_registry_status(rule_primary),
        "primary_reasoning_object": (
            rule_contract.reasoning_object if rule_contract else ""
        ),
        "required_slots_satisfied": required_slots_satisfied,
        "missing_required_slots": missing_required_slots,
        "supporting_fact_ids": collect_referenced_fact_ids(rule_manifest),
        "excluded_neighbor_operators": [
            {
                "operator_id": operator_id,
                "reason": "less direct match for the recognized primary reasoning object",
            }
            for operator_id in (
                rule_contract.neighbor_operators if rule_contract else ()
            )
            if operator_id != rule_primary
        ],
        "adapter_version": _clean_text(
            rule_manifest.get("adapter_version")
            or rule_manifest.get("adapter_id")
        ),
        "recognized_semantic_version": (
            rule_contract.semantic_version if rule_contract else ""
        ),
        "recognized_applicability_version": (
            rule_contract.applicability_version if rule_contract else ""
        ),
        "shadow_operator_plan": shadow_operator_plan,
        "generation_registry": list(OPERATOR_ORDER),
        "memory_warnings": failure_memory_actions["memory_warnings"],
        "downrank_operator_surface_forms": failure_memory_actions["downrank_operator_surface_forms"],
        "avoid_operator_surface_forms": failure_memory_actions["avoid_operator_surface_forms"],
        "memory_matches": {
            "operator": operator_matches[:3],
            "failure": failure_matches[:3],
        },
    }


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
) -> List[Dict[str, Any]]:
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
    for record in records:
        route = record.get("operator_route")
        route = route if isinstance(route, dict) else {}
        warn_count += len(route.get("memory_warnings") or [])
        downrank_count += len(route.get("downrank_operator_surface_forms") or [])
        avoid_count += len(route.get("avoid_operator_surface_forms") or [])
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
    }


def write_json(data: Dict[str, Any], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


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
    metrics.parse_seconds += max(time.monotonic() - parse_started, 0.000001)
    compute_started = time.monotonic()
    routed = route_records(
        records,
        operator_memory=operator_memory,
        failure_memory=failure_memory,
        full_score_threshold=args.full_score_threshold,
        failure_memory_window_rounds=max(1, args.failure_memory_window_rounds),
    )
    metrics.compute_seconds += max(time.monotonic() - compute_started, 0.000001)
    publish_records(
        routed,
        args.output,
        stage=stage,
        input_path=args.input,
        config={
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
        },
        performance_path=args.performance_events,
        code_paths=[__file__],
        metrics=metrics,
    )
    if args.report_output:
        write_json(build_router_report(routed), args.report_output)


if __name__ == "__main__":
    main()
