import argparse
import json
import os
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from pipeline_runtime import StageMetrics, publish_records, sha256_file

from analyze_evolution_effect import (
    get_metadata,
    get_operator_used,
    is_question_evolved,
    load_json_or_jsonl,
)


FAILURE_EFFECT_LABELS = {
    "full_score_no_drop",
    "no_clear_effect",
    "score_increased",
    "repeated_pattern",
}

TERMINAL_STOP_STATUSES = {
    "effective_boundary_sample",
    "stable_high_score_stop",
    "invalid_complexity_sample",
    "unanswerable_or_trap_sample",
}

OPERATOR_AVOID_METHODS = {
    "O10_evidence_sufficiency_ladder": ["继续复用同一组近似判断竞争"],
    "O11_unobserved_state_attribution": ["继续把端点事实直接改写成盲区内状态判断"],
    "O12_conjunctive_necessity": ["继续用同一强线索替代未闭合门槛"],
    "O13_minimal_disqualifier": ["继续复用同一新增事实改变原评价的问法"],
    "O14_information_closure": ["继续显式询问题外前提或信息闭包标签"],
    "O15_counterfactual_threshold_shift": ["继续改变同一变量做整体保留或整体撤回"],
    "O16_close_alternative_normalization": ["继续询问正常解释是否排除全部风险"],
    "O17_action_vs_fact_threshold": ["继续显式标注处置门槛与事实门槛"],
    "O18_baseline_scope_mismatch": ["继续明示样本口径或基线范围错配"],
}

NEXT_OPERATOR_HINTS = {
    "O10_evidence_sufficiency_ladder": ["O17_action_vs_fact_threshold", "O14_information_closure", "O13_minimal_disqualifier"],
    "O11_unobserved_state_attribution": ["O17_action_vs_fact_threshold", "O16_close_alternative_normalization"],
    "O12_conjunctive_necessity": ["O17_action_vs_fact_threshold", "O13_minimal_disqualifier"],
    "O13_minimal_disqualifier": ["O15_counterfactual_threshold_shift", "O16_close_alternative_normalization"],
    "O14_information_closure": ["O10_evidence_sufficiency_ladder", "O18_baseline_scope_mismatch"],
    "O15_counterfactual_threshold_shift": ["O16_close_alternative_normalization", "O13_minimal_disqualifier"],
    "O16_close_alternative_normalization": ["O15_counterfactual_threshold_shift", "O17_action_vs_fact_threshold"],
    "O17_action_vs_fact_threshold": ["O11_unobserved_state_attribution", "O12_conjunctive_necessity"],
    "O18_baseline_scope_mismatch": ["O10_evidence_sufficiency_ladder", "O14_information_closure"],
}
OPERATOR_SURFACE_FORM_FAMILY = {
    "O1_gap_choice": "evidence_relation_comparison",
    "O2_subclaim_localization": "fact_conclusion_support_review",
    "O3_step_jump": "step_jump_review",
    "O4_near_level_ranking": "near_level_comparison",
    "O5_extra_premise_detection": "external_premise_review",
    "O6_single_variable_counterfactual": "counterfactual_boundary",
    "O7_fact_binding_constraint": "fact_binding_review",
    "O8_double_threshold_claim": "conclusion_strength_boundary",
    "O9_abnormal_clue_mainline_switch": "abnormal_mainline_switch",
}


def write_jsonl(records: Iterable[Dict[str, Any]], output_path: str, *, append: bool = False) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    mode = "a" if append else "w"
    with open(output_path, mode, encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def append_unique_jsonl(records: Iterable[Dict[str, Any]], output_path: str) -> int:
    """Append only records not already present, making round-end memory writes retry-safe."""
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    seen = set()
    if os.path.isfile(output_path):
        with open(output_path, "r", encoding="utf-8") as existing:
            for line_number, line in enumerate(existing, start=1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    item = json.loads(stripped)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"[update_sample_state:memory] {output_path}:{line_number}: {exc.msg}"
                    ) from exc
                seen.add(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))

    appended = 0
    with open(output_path, "a", encoding="utf-8") as output:
        for record in records:
            canonical = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if canonical in seen:
                continue
            output.write(json.dumps(record, ensure_ascii=False) + "\n")
            seen.add(canonical)
            appended += 1
        if appended:
            output.flush()
            os.fsync(output.fileno())
    return appended


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _sample_id(item: Dict[str, Any]) -> Any:
    return item.get("sample_id", item.get("index", ""))


def _round_value(item: Dict[str, Any], previous_state: Dict[str, Any]) -> int:
    for value in (item.get("round"), previous_state.get("round")):
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return 0


def _effect(item: Dict[str, Any]) -> Dict[str, Any]:
    effect = item.get("effect_analysis")
    if not isinstance(effect, dict):
        raise ValueError("record missing effect_analysis; run analyze_evolution_effect.py first")
    return effect


def _validation(item: Dict[str, Any]) -> Dict[str, Any]:
    validation = item.get("validation_result")
    return validation if isinstance(validation, dict) else {}


def _difficulty_gain_validation(item: Dict[str, Any]) -> Dict[str, Any]:
    validation = item.get("difficulty_gain_validation")
    return validation if isinstance(validation, dict) else {}


def surface_form_family(item: Dict[str, Any], operator_used: str = "") -> str:
    for source in (item, item.get("candidate_generation"), get_metadata(item)):
        if isinstance(source, dict):
            for field in ("surface_form_family", "question_surface_form"):
                value = _clean_text(source.get(field))
                if value:
                    return value
    return OPERATOR_SURFACE_FORM_FAMILY.get(operator_used, "unknown")


def _previous_state(item: Dict[str, Any]) -> Dict[str, Any]:
    state = item.get("evolution_state")
    return dict(state) if isinstance(state, dict) else {}


def _append_unique(items: List[str], values: Sequence[str]) -> List[str]:
    for value in values:
        text = _clean_text(value)
        if text and text not in items:
            items.append(text)
    return items


def sample_signature(item: Dict[str, Any]) -> Dict[str, str]:
    profile = item.get("sample_profile")
    diagnosis = item.get("overscore_diagnosis")
    profile = profile if isinstance(profile, dict) else {}
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    return {
        "core_capability": _clean_text(profile.get("core_capability")),
        "claim_level": _clean_text(profile.get("claim_level")),
        "problem_shape": _clean_text(profile.get("problem_shape")),
        "candidate_overscore_cause": _clean_text(diagnosis.get("candidate_overscore_cause")),
    }


def _sample_signature_from_invalid_case(item: Dict[str, Any]) -> Dict[str, Any]:
    signature = item.get("sample_signature")
    if isinstance(signature, dict):
        return signature
    profile = item.get("sample_profile")
    diagnosis = item.get("overscore_diagnosis")
    profile = profile if isinstance(profile, dict) else {}
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    return {
        "core_capability": _clean_text(profile.get("core_capability")),
        "claim_level": _clean_text(profile.get("claim_level")),
        "problem_shape": _clean_text(profile.get("problem_shape")),
        "candidate_overscore_cause": _clean_text(diagnosis.get("candidate_overscore_cause")),
    }


def _expected_failure_mode(item: Dict[str, Any]) -> str:
    metadata = get_metadata(item)
    expected = _clean_text(metadata.get("expected_qwen_failure"))
    if expected:
        return expected
    diagnosis = item.get("overscore_diagnosis")
    if isinstance(diagnosis, dict):
        return _clean_text(diagnosis.get("target_failure_mode") or diagnosis.get("candidate_overscore_cause"))
    return ""


def _stop_status(
    item: Dict[str, Any],
    full_score_count: int,
    same_operator_count: int,
) -> str:
    effect = _effect(item)
    label = _clean_text(effect.get("effect_label"))
    previous_stop = _clean_text(_previous_state(item).get("stop_status"))
    previous_recommended = list(_previous_state(item).get("recommended_next_methods") or [])

    if label == "effective_boundary_probe":
        return "effective_boundary_sample"
    if label == "invalid_complexity":
        invalid_type = _clean_text(_validation(item).get("invalid_type"))
        if invalid_type in {"external_knowledge_required", "empty_prompt"}:
            return "unanswerable_or_trap_sample"
        return "invalid_complexity_sample"
    if label == "pass_through":
        return previous_stop or "continue"
    if label == "score_increased":
        return "rollback_and_reroute"
    if label == "full_score_no_drop":
        if full_score_count >= 2:
            return "local_tree_search_needed"
        if previous_recommended:
            return "continue_with_new_operator"
        return "continue_with_new_operator"
    if label == "repeated_pattern":
        return "stable_high_score_stop" if same_operator_count >= 2 else "continue_with_new_operator"
    if label in {"needs_manual_review", "no_clear_effect", "score_increased"}:
        return "continue_with_new_operator"
    return previous_stop or "continue"


def _recommended_next_methods(operator_used: str, label: str, full_score_count: int) -> List[str]:
    if label == "effective_boundary_probe":
        return []
    hints = list(NEXT_OPERATOR_HINTS.get(operator_used, []))
    if full_score_count >= 2 and "O10_evidence_sufficiency_ladder" not in hints:
        hints.append("O10_evidence_sufficiency_ladder")
    return hints


def build_next_state(item: Dict[str, Any]) -> Dict[str, Any]:
    effect = _effect(item)
    previous_state = _previous_state(item)
    operator_used = _clean_text(effect.get("operator_used")) or get_operator_used(item)
    previous_operator = _clean_text(previous_state.get("previous_operator"))
    previous_same_count = int(previous_state.get("consecutive_same_operator_count", 0) or 0)
    previous_full_count = int(previous_state.get("consecutive_full_score_count", 0) or 0)
    current_full = bool(effect.get("is_full_score"))
    full_score_count = previous_full_count + 1 if current_full else 0
    same_operator_count = previous_same_count + 1 if operator_used and operator_used == previous_operator else (1 if operator_used else 0)
    label = _clean_text(effect.get("effect_label"))

    avoid_methods = list(previous_state.get("avoid_methods") or [])
    if label in FAILURE_EFFECT_LABELS or label == "needs_manual_review":
        _append_unique(avoid_methods, OPERATOR_AVOID_METHODS.get(operator_used, []))

    recommended = _recommended_next_methods(operator_used, label, full_score_count)
    if not recommended and label != "effective_boundary_probe":
        recommended = list(previous_state.get("recommended_next_methods") or [])

    stop_status = _stop_status(
        item,
        full_score_count,
        same_operator_count,
    )
    if stop_status in TERMINAL_STOP_STATUSES:
        recommended = []

    return {
        "round": _round_value(item, previous_state),
        "previous_operator": operator_used or None,
        "previous_score_rate": (
            effect.get("score_rate_before")
            if label == "score_increased"
            else effect.get("score_rate_after")
        ),
        "previous_effect_status": label or None,
        "previous_failure_mode": _expected_failure_mode(item) or None,
        "consecutive_full_score_count": full_score_count,
        "consecutive_same_operator_count": same_operator_count,
        "avoid_methods": avoid_methods,
        "recommended_next_methods": recommended,
        "stop_status": stop_status,
        "rollback_applied": label == "score_increased",
    }


def _restore_parent_after_score_increase(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    meta_info = result.get("meta_info")
    meta_info = dict(meta_info) if isinstance(meta_info, dict) else {}
    snapshot = meta_info.get("parent_snapshot")

    if isinstance(snapshot, dict):
        if snapshot.get("prompt"):
            result["prompt"] = snapshot.get("prompt")
        for field in ("rubric", "rubric_thought_process", "score_prompt", "scoring_result"):
            if snapshot.get(field) is None:
                result.pop(field, None)
            else:
                result[field] = snapshot.get(field)
        if snapshot.get("references") is not None:
            meta_info["references"] = snapshot.get("references")
        if snapshot.get("prompt_old") is None:
            meta_info.pop("prompt_old", None)
        else:
            meta_info["prompt_old"] = snapshot.get("prompt_old")
        if snapshot.get("question_evolution_metadata") is None:
            meta_info.pop("question_evolution_metadata", None)
        else:
            meta_info["question_evolution_metadata"] = snapshot.get("question_evolution_metadata")
    else:
        old_prompt = meta_info.get("prompt_old")
        if isinstance(old_prompt, str) and old_prompt.strip():
            result["prompt"] = old_prompt.strip()
        stale_fields = {
            "rubric": "stale_rubric",
            "rubric_thought_process": "stale_rubric_thought_process",
            "score_prompt": "stale_score_prompt",
            "scoring_result": "stale_scoring_result",
        }
        for field, stale_field in stale_fields.items():
            if stale_field in meta_info:
                result[field] = meta_info.get(stale_field)
        if "stale_references" in meta_info:
            meta_info["references"] = meta_info.get("stale_references")

    effect = _effect(item)
    result["score_rate"] = effect.get("score_rate_before")
    result["question_evolved"] = False
    meta_info.pop("parent_snapshot", None)
    result["meta_info"] = meta_info
    return result


def attach_next_state(item: Dict[str, Any]) -> Dict[str, Any]:
    state = build_next_state(item)
    result = (
        _restore_parent_after_score_increase(item)
        if state.get("rollback_applied")
        else dict(item)
    )
    result["evolution_state"] = state
    return result


def build_operator_memory_entry(item: Dict[str, Any]) -> Dict[str, Any]:
    effect = _effect(item)
    metadata = get_metadata(item)
    confidence = _clean_text(effect.get("hit_confidence")) or "low"
    operator_used = _clean_text(effect.get("operator_used"))
    reuse_note = "自动轻量命中，进入下一轮路由前建议人工复核。"
    if confidence == "low":
        reuse_note = "低置信命中，仅供人工复核和后续对照，不应沉淀为强成功经验。"
    return {
        "sample_id": _sample_id(item),
        "round": _round_value(item, _previous_state(item)),
        "sample_signature": sample_signature(item),
        "operator_used": operator_used,
        "surface_form_family": surface_form_family(item, operator_used),
        "expected_qwen_failure": _clean_text(metadata.get("expected_qwen_failure")),
        "score_rate_before": effect.get("score_rate_before"),
        "score_rate_after": effect.get("score_rate_after"),
        "delta_score_rate": effect.get("delta_score_rate"),
        "question_length": effect.get("question_length"),
        "validation_passed": bool(effect.get("complexity_passed")),
        "hit_confidence": confidence,
        "needs_manual_review": bool(effect.get("needs_manual_review", True)),
        "effect_label": _clean_text(effect.get("effect_label")),
        "reuse_note": reuse_note,
    }


def build_failure_memory_entry(item: Dict[str, Any]) -> Dict[str, Any]:
    effect = _effect(item)
    operator_used = _clean_text(effect.get("operator_used"))
    recommended = _recommended_next_methods(
        operator_used,
        _clean_text(effect.get("effect_label")),
        int(build_next_state(item).get("consecutive_full_score_count", 0) or 0),
    )
    return {
        "sample_id": _sample_id(item),
        "round": _round_value(item, _previous_state(item)),
        "sample_signature": sample_signature(item),
        "operator_used": operator_used,
        "surface_form_family": surface_form_family(item, operator_used),
        "score_rate_before": effect.get("score_rate_before"),
        "score_rate_after": effect.get("score_rate_after"),
        "failure_type": _clean_text(effect.get("effect_label")) or "operator_ineffective",
        "failure_reason": _clean_text(effect.get("lightweight_hit_reason")) or "未形成清晰降分。",
        "avoid_note": "建议切换到：" + "、".join(recommended) if recommended else "建议避免重复当前问法。",
    }


def build_invalid_generation_case(item: Dict[str, Any]) -> Dict[str, Any]:
    effect = _effect(item)
    validation = _validation(item)
    state = build_next_state(item)
    suggested = ""
    recommended = state.get("recommended_next_methods")
    if isinstance(recommended, list) and recommended:
        suggested = _clean_text(recommended[0])
    return {
        "sample_id": _sample_id(item),
        "round": _round_value(item, _previous_state(item)),
        "operator_used": _clean_text(effect.get("operator_used")),
        "surface_form_family": surface_form_family(item, _clean_text(effect.get("operator_used"))),
        "invalid_type": _clean_text(validation.get("invalid_type")) or "invalid_complexity",
        "reason": _clean_text(validation.get("reject_reason")) or _clean_text(effect.get("lightweight_hit_reason")),
        "suggested_operator": suggested,
    }


def normalize_preselection_invalid_case(item: Dict[str, Any]) -> Dict[str, Any]:
    failure_memory = item.get("failure_memory_candidate")
    failure_memory = failure_memory if isinstance(failure_memory, dict) else {}
    difficulty_validation = _difficulty_gain_validation(item)
    risk_tags = item.get("risk_tags")
    if not isinstance(risk_tags, list):
        risk_tags = difficulty_validation.get("risk_tags", [])
    return {
        "sample_id": _sample_id(item),
        "round": _round_value(item, {}),
        "operator_used": _clean_text(item.get("operator_used") or failure_memory.get("operator_id")),
        "surface_form_family": _clean_text(item.get("surface_form_family") or failure_memory.get("surface_form_family")),
        "invalid_type": _clean_text(item.get("invalid_type") or difficulty_validation.get("difficulty_gain_label")) or "difficulty_gain_validation_failed",
        "reason": _clean_text(item.get("reason") or failure_memory.get("reject_reason") or difficulty_validation.get("reject_reason")),
        "suggested_operator": _clean_text(item.get("suggested_operator") or failure_memory.get("recommended_retry_strategy")),
        "sample_signature": _sample_signature_from_invalid_case(item),
        "difficulty_gain_validation": difficulty_validation,
        "risk_tags": risk_tags if isinstance(risk_tags, list) else [],
        "failure_memory_candidate": failure_memory,
        "source_stage": "candidate_selection",
    }


def build_preselection_failure_memory_entry(item: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_preselection_invalid_case(item)
    failure_memory = normalized.get("failure_memory_candidate")
    failure_memory = failure_memory if isinstance(failure_memory, dict) else {}
    risk_tags = normalized.get("risk_tags")
    risk_tags = risk_tags if isinstance(risk_tags, list) else []
    retry_strategy = _clean_text(failure_memory.get("recommended_retry_strategy") or normalized.get("suggested_operator"))
    return {
        "sample_id": normalized["sample_id"],
        "round": normalized["round"],
        "sample_signature": normalized.get("sample_signature", {}),
        "operator_used": _clean_text(failure_memory.get("operator_id") or normalized.get("operator_used")),
        "surface_form_family": _clean_text(
            failure_memory.get("surface_form_family")
            or normalized.get("surface_form_family")
            or OPERATOR_SURFACE_FORM_FAMILY.get(_clean_text(failure_memory.get("operator_id") or normalized.get("operator_used")), "unknown")
        ),
        "score_rate_before": None,
        "score_rate_after": None,
        "failure_type": _clean_text(failure_memory.get("failure_type") or normalized.get("invalid_type")),
        "failure_reason": _clean_text(failure_memory.get("reject_reason") or normalized.get("reason")),
        "avoid_note": (
            "难度收益验证失败，建议策略：" + retry_strategy
            if retry_strategy
            else "难度收益验证失败，建议切换候选生成策略。"
        ),
        "risk_tags": risk_tags,
        "source_stage": "difficulty_gain_validation",
    }


def classify_preselection_invalid_cases(
    records: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    invalid_entries = [normalize_preselection_invalid_case(record) for record in records]
    failure_entries = [
        build_preselection_failure_memory_entry(record)
        for record in records
        if isinstance(record.get("failure_memory_candidate"), dict)
        or isinstance(record.get("difficulty_gain_validation"), dict)
    ]
    return failure_entries, invalid_entries


def classify_memory_entries(
    records: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    operator_entries: List[Dict[str, Any]] = []
    failure_entries: List[Dict[str, Any]] = []
    invalid_entries: List[Dict[str, Any]] = []

    for record in records:
        effect = _effect(record)
        label = _clean_text(effect.get("effect_label"))
        if label == "effective_boundary_probe" and effect.get("complexity_passed") and is_question_evolved(record):
            operator_entries.append(build_operator_memory_entry(record))
        if label in FAILURE_EFFECT_LABELS and effect.get("complexity_passed") and is_question_evolved(record):
            failure_entries.append(build_failure_memory_entry(record))
        if label == "invalid_complexity" or effect.get("complexity_passed") is False:
            invalid_entries.append(build_invalid_generation_case(record))

    return operator_entries, failure_entries, invalid_entries


def generate_report(
    updated_records: Sequence[Dict[str, Any]],
    operator_entries: Sequence[Dict[str, Any]],
    failure_entries: Sequence[Dict[str, Any]],
    invalid_entries: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    failure_distribution: Counter = Counter()
    for entry in failure_entries:
        key = (
            f"{_clean_text(entry.get('operator_used'))}+"
            f"{_clean_text(entry.get('surface_form_family'))}+"
            f"{_clean_text(entry.get('failure_type'))}"
        )
        failure_distribution[key] += 1
    return {
        "updated_record_count": len(updated_records),
        "operator_memory_write_count": len(operator_entries),
        "failure_memory_write_count": len(failure_entries),
        "invalid_memory_write_count": len(invalid_entries),
        "operator_surface_form_failure_distribution": dict(sorted(failure_distribution.items())),
    }


def update_records(records: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    updated = [attach_next_state(record) for record in records]
    operator_entries, failure_entries, invalid_entries = classify_memory_entries(records)
    return updated, operator_entries, failure_entries, invalid_entries


def load_optional_json_or_jsonl(input_path: str) -> List[Dict[str, Any]]:
    if not input_path or not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
        return []
    return load_json_or_jsonl(input_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update evolution_state and append Stage 5 memory-bank entries.")
    parser.add_argument("--input", required=True, help="Input analyzed JSON/JSONL path.")
    parser.add_argument("--output", required=True, help="Output state-updated JSONL path.")
    parser.add_argument("--memory-dir", default="memory", help="Directory containing memory bank JSONL files.")
    parser.add_argument("--operator-memory", default=None, help="Override operator memory output path.")
    parser.add_argument("--failure-memory", default=None, help="Override failure memory output path.")
    parser.add_argument("--invalid-output", default=None, help="Override invalid generation case output path.")
    parser.add_argument(
        "--preselection-invalid-input",
        default=None,
        help="Optional candidate_selection invalid_generation_cases.jsonl to append into memory banks.",
    )
    parser.add_argument("--report-output", default=None, help="Optional state-update memory report JSON path.")
    parser.add_argument("--no-memory-output", action="store_true", help="Do not append memory-bank entries.")
    parser.add_argument("--performance-events", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stage = "update_sample_state"
    metrics = StageMetrics(stage)
    metrics.input_bytes = os.path.getsize(args.input)
    parse_started = time.monotonic()
    records = load_json_or_jsonl(args.input)
    preselection_invalid_cases = load_optional_json_or_jsonl(args.preselection_invalid_input)
    metrics.parse_seconds += time.monotonic() - parse_started
    compute_started = time.monotonic()
    updated, operator_entries, failure_entries, invalid_entries = update_records(records)
    if preselection_invalid_cases:
        preselection_failure_entries, preselection_invalid_entries = classify_preselection_invalid_cases(preselection_invalid_cases)
        failure_entries.extend(preselection_failure_entries)
        invalid_entries.extend(preselection_invalid_entries)
    operator_memory = args.operator_memory or os.path.join(args.memory_dir, "operator_memory_bank.jsonl")
    failure_memory = args.failure_memory or os.path.join(args.memory_dir, "failure_memory_bank.jsonl")
    invalid_output = args.invalid_output or os.path.join(args.memory_dir, "invalid_generation_cases.jsonl")

    # Memory is a round-end side effect. Persist it idempotently before publishing the
    # formal state artifact so a crash can never leave a skippable output with missing memory.
    if not args.no_memory_output:
        if operator_entries:
            append_unique_jsonl(operator_entries, operator_memory)
        if failure_entries:
            append_unique_jsonl(failure_entries, failure_memory)
        if invalid_entries:
            append_unique_jsonl(invalid_entries, invalid_output)
    metrics.compute_seconds += time.monotonic() - compute_started

    publish_records(
        updated,
        args.output,
        stage=stage,
        input_path=args.input,
        config={
            "memory_dir": os.path.abspath(args.memory_dir),
            "preselection_invalid_input": (
                os.path.abspath(args.preselection_invalid_input)
                if args.preselection_invalid_input
                else None
            ),
            "preselection_invalid_sha256": (
                sha256_file(args.preselection_invalid_input)
                if args.preselection_invalid_input
                and os.path.isfile(args.preselection_invalid_input)
                else None
            ),
            "no_memory_output": args.no_memory_output,
        },
        performance_path=args.performance_events,
        code_paths=[__file__],
        metrics=metrics,
    )
    if args.report_output:
        output_dir = os.path.dirname(os.path.abspath(args.report_output))
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
        with open(args.report_output, "w", encoding="utf-8") as f:
            json.dump(generate_report(updated, operator_entries, failure_entries, invalid_entries), f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")

if __name__ == "__main__":
    main()
