import argparse
import json
import os
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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

OPERATOR_ORDER = (
    O10_EVIDENCE_SUFFICIENCY_LADDER,
    O11_UNOBSERVED_STATE_ATTRIBUTION,
    O12_CONJUNCTIVE_NECESSITY,
    O13_MINIMAL_DISQUALIFIER,
    O14_INFORMATION_CLOSURE,
    O15_COUNTERFACTUAL_THRESHOLD_SHIFT,
    O16_CLOSE_ALTERNATIVE_NORMALIZATION,
    O17_ACTION_VS_FACT_THRESHOLD,
    O18_BASELINE_SCOPE_MISMATCH,
)
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
}
FAILURE_MEMORY_WARN_THRESHOLD = 1
FAILURE_MEMORY_DOWNRANK_THRESHOLD = 2
FAILURE_MEMORY_AVOID_THRESHOLD = 3


def load_json_or_jsonl(input_path: str) -> List[Dict[str, Any]]:
    with open(input_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []
    if content.startswith("["):
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError("JSON input must be an array")
        return data
    return [json.loads(line) for line in content.splitlines() if line.strip()]


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
) -> Dict[str, List[Dict[str, Any]]]:
    signature = build_sample_signature(item)
    current_round = _round_value(item)
    min_round = current_round - window_rounds + 1 if current_round is not None else None
    grouped: Counter = Counter()

    for record in failure_memory:
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


def find_memory_matches(
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


def _base_rule_route(item: Dict[str, Any]) -> Tuple[Optional[str], List[str], str]:
    diagnosis = get_overscore_diagnosis(item)
    cause = _clean_text(diagnosis.get("candidate_overscore_cause"))
    target = _clean_text(diagnosis.get("target_failure_mode"))
    combined = f"{cause} {target}"

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

    primary, backups, reason = _base_rule_route(item)
    avoid: List[str] = []
    reason_parts = [reason]
    recommended_next = _recommended_next_methods(item)

    signature = build_sample_signature(item)
    operator_matches = find_memory_matches(signature, operator_memory)
    failure_matches = find_memory_matches(signature, failure_memory)
    failure_memory_actions = build_failure_memory_actions(
        item,
        failure_memory,
        window_rounds=failure_memory_window_rounds,
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
) -> Dict[str, Any]:
    result = dict(item)
    result["operator_route"] = build_operator_route(
        item,
        operator_memory=operator_memory,
        failure_memory=failure_memory,
        full_score_threshold=full_score_threshold,
        failure_memory_window_rounds=failure_memory_window_rounds,
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
    return [
        attach_operator_route(
            record,
            operator_memory=operator_memory,
            failure_memory=failure_memory,
            full_score_threshold=full_score_threshold,
            failure_memory_window_rounds=failure_memory_window_rounds,
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    operator_memory_path = args.operator_memory or os.path.join(args.memory_dir, "operator_memory_bank.jsonl")
    failure_memory_path = args.failure_memory or os.path.join(args.memory_dir, "failure_memory_bank.jsonl")
    records = load_json_or_jsonl(args.input)
    routed = route_records(
        records,
        operator_memory=load_jsonl_if_exists(operator_memory_path),
        failure_memory=load_jsonl_if_exists(failure_memory_path),
        full_score_threshold=args.full_score_threshold,
        failure_memory_window_rounds=max(1, args.failure_memory_window_rounds),
    )
    write_jsonl(routed, args.output)
    if args.report_output:
        write_json(build_router_report(routed), args.report_output)


if __name__ == "__main__":
    main()
