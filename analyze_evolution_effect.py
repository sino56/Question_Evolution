import argparse
import json
import os
import time
import re
from collections import Counter, defaultdict
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

from pipeline_runtime import StageMetrics, load_json_records, publish_records, sha256_file
from governance import scope_allows


DEFAULT_FULL_SCORE_THRESHOLD = 0.99
DEFAULT_SCORE_DROP_THRESHOLD = 0.15
DEFAULT_REVIEW_DROP_THRESHOLD = 0.05
DEFAULT_SCORE_INCREASE_THRESHOLD = 0.05
FOCUS_STOPWORDS = {
    "是否",
    "判断",
    "说明",
    "指出",
    "真正",
    "当前",
    "仍然",
    "没有",
    "不能",
    "可以",
    "需要",
    "一个",
    "什么",
    "为什么",
}


def load_json_or_jsonl(input_path: str) -> List[Dict[str, Any]]:
    return load_json_records(input_path, stage="analyze_evolution_effect")


def write_jsonl(records: Iterable[Dict[str, Any]], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _coerce_score_rate(value: Any) -> Optional[float]:
    try:
        score_rate = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= score_rate <= 1:
        return score_rate
    return None


def record_key(item: Dict[str, Any]) -> str:
    for field in ("sample_id", "index"):
        value = item.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return _clean_text(item.get("prompt"))


def records_by_key(records: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    return {record_key(record): record for record in records if record_key(record)}


def get_score_rate(item: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(item, dict):
        return None

    top_level_score_rate = _coerce_score_rate(item.get("score_rate"))
    if top_level_score_rate is not None:
        return top_level_score_rate

    scoring_result = item.get("scoring_result")
    if isinstance(scoring_result, dict):
        try:
            awarded = float(scoring_result.get("total_awarded", 0) or 0)
            possible = float(scoring_result.get("total_possible", 0) or 0)
        except (TypeError, ValueError):
            possible = 0
            awarded = 0
        if possible > 0:
            return max(0.0, min(1.0, awarded / possible))

    summary = item.get("round0_score_summary")
    if isinstance(summary, dict):
        stable_score = _coerce_score_rate(summary.get("stable_score"))
        if stable_score is not None:
            return stable_score
    return None


def get_score_source(item: Optional[Dict[str, Any]]) -> str:
    if not isinstance(item, dict):
        return "missing"
    if _coerce_score_rate(item.get("score_rate")) is not None:
        return "score_rate"
    scoring_result = item.get("scoring_result")
    if isinstance(scoring_result, dict):
        try:
            possible = float(scoring_result.get("total_possible", 0) or 0)
        except (TypeError, ValueError):
            possible = 0
        if possible > 0:
            return "scoring_result.total_awarded/total_possible"
    summary = item.get("round0_score_summary")
    if isinstance(summary, dict) and _coerce_score_rate(summary.get("stable_score")) is not None:
        return "round0_score_summary.stable_score"
    if isinstance(scoring_result, dict):
        return "scoring_result.total_awarded/total_possible"
    return "missing"


def get_metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    meta_info = item.get("meta_info")
    if not isinstance(meta_info, dict):
        return {}
    metadata = meta_info.get("question_evolution_metadata")
    return metadata if isinstance(metadata, dict) else {}


def get_validation_result(item: Dict[str, Any]) -> Dict[str, Any]:
    validation = item.get("validation_result")
    return validation if isinstance(validation, dict) else {}


def is_question_evolved(item: Dict[str, Any]) -> bool:
    if item.get("question_evolved") is True:
        return True
    if item.get("question_evolved") is False:
        return False
    return bool(get_metadata(item).get("question_evolved"))


def get_score_rate_before(item: Dict[str, Any], previous_item: Optional[Dict[str, Any]]) -> Optional[float]:
    metadata = get_metadata(item)
    trigger_score_rate = _coerce_score_rate(metadata.get("trigger_score_rate"))
    if trigger_score_rate is not None:
        return trigger_score_rate

    previous_score_rate = get_score_rate(previous_item)
    if previous_score_rate is not None:
        return previous_score_rate

    meta_info = item.get("meta_info")
    if isinstance(meta_info, dict):
        stale_scoring = meta_info.get("stale_scoring_result")
        if isinstance(stale_scoring, dict):
            stale_score_rate = get_score_rate({"scoring_result": stale_scoring})
            if stale_score_rate is not None:
                return stale_score_rate

    state = item.get("evolution_state")
    if isinstance(state, dict):
        state_score_rate = _coerce_score_rate(state.get("previous_score_rate"))
        if state_score_rate is not None:
            return state_score_rate

    return get_score_rate(item)


def get_score_rate_before_source(item: Dict[str, Any], previous_item: Optional[Dict[str, Any]]) -> str:
    metadata = get_metadata(item)
    if _coerce_score_rate(metadata.get("trigger_score_rate")) is not None:
        return "question_evolution_metadata.trigger_score_rate"
    if previous_item is not None and get_score_rate(previous_item) is not None:
        return get_score_source(previous_item)
    meta_info = item.get("meta_info")
    if isinstance(meta_info, dict) and isinstance(meta_info.get("stale_scoring_result"), dict):
        return "meta_info.stale_scoring_result"
    state = item.get("evolution_state")
    if isinstance(state, dict) and _coerce_score_rate(state.get("previous_score_rate")) is not None:
        return "evolution_state.previous_score_rate"
    return get_score_source(item)


def get_operator_used(item: Dict[str, Any]) -> str:
    selection = item.get("candidate_selection")
    if isinstance(selection, dict):
        selected_operator = _clean_text(selection.get("selected_operator"))
        if selected_operator or selection.get("candidate_flow") == "pass_through_candidate" or selection.get("selected") is False:
            return selected_operator

    metadata = get_metadata(item)
    for value in (
        item.get("candidate_operator"),
        item.get("operator_used"),
        metadata.get("operator_used"),
    ):
        text = _clean_text(value)
        if text:
            return text
    return ""


def get_expected_focus(item: Dict[str, Any]) -> List[str]:
    focus = get_metadata(item).get("expected_evaluation_focus")
    if isinstance(focus, list):
        return [_clean_text(value) for value in focus if _clean_text(value)]
    if isinstance(focus, str) and focus.strip():
        return [focus.strip()]
    return []


def get_candidate_answer(item: Dict[str, Any]) -> str:
    scoring_result = item.get("scoring_result")
    if isinstance(scoring_result, dict):
        return _clean_text(scoring_result.get("candidate_answer"))
    return _clean_text(item.get("candidate_answer"))


def has_candidate_answer(item: Dict[str, Any]) -> bool:
    return bool(get_candidate_answer(item))


def _focus_terms(texts: Sequence[str]) -> List[str]:
    terms: List[str] = []
    for text in texts:
        for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", text):
            token = token.strip()
            if token and token not in FOCUS_STOPWORDS and token not in terms:
                terms.append(token)
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", token):
                for size in (4, 3, 2):
                    for start in range(0, len(token) - size + 1):
                        gram = token[start:start + size]
                        if gram and gram not in FOCUS_STOPWORDS and gram not in terms:
                            terms.append(gram)
    return terms


def analyze_focus_answer_alignment(focus: Sequence[str], candidate_answer: str) -> Dict[str, Any]:
    focus_terms = _focus_terms(focus)
    answer_terms = set(_focus_terms([candidate_answer]))
    matched_terms = [term for term in focus_terms if term in candidate_answer or term in answer_terms]
    if not focus_terms:
        return {
            "matches": False,
            "confidence": "low",
            "matched_terms": [],
            "reason": "缺少 expected_evaluation_focus，无法确认候选答案错误方向。",
        }
    if not candidate_answer.strip():
        return {
            "matches": False,
            "confidence": "low",
            "matched_terms": [],
            "reason": "缺少 candidate_answer，无法确认错误方向。",
        }

    coverage = len(matched_terms) / max(1, len(focus_terms))
    if len(matched_terms) >= 2 or coverage >= 0.35:
        confidence = "medium" if coverage < 0.65 else "high"
        return {
            "matches": True,
            "confidence": confidence,
            "matched_terms": matched_terms,
            "reason": "candidate_answer 的错误表述与 expected_evaluation_focus 存在关键词和语义方向重合。",
        }

    return {
        "matches": False,
        "confidence": "low",
        "matched_terms": matched_terms,
        "reason": "candidate_answer 的主要错误方向未能匹配 expected_evaluation_focus。",
    }


def validation_passed_for_effect(item: Dict[str, Any]) -> bool:
    if not is_question_evolved(item):
        return True
    validation = get_validation_result(item)
    if not validation:
        return False
    return validation.get("passed") is True


def completed_score_summary(summary: Any, *, minimum_successes: int = 1) -> bool:
    """Require actual completed trials before an effect can be confirmed."""

    if not isinstance(summary, dict):
        return False
    try:
        successful = int(summary.get("successful_count", 0) or 0)
        failed = int(summary.get("failed_count", 0) or 0)
    except (TypeError, ValueError):
        return False
    return summary.get("status", "completed") == "completed" and successful >= minimum_successes and failed == 0


def is_repeated_pattern(item: Dict[str, Any]) -> bool:
    validation = get_validation_result(item)
    return validation.get("repeat_pattern_risk") == "high" or bool(
        validation.get("repeated_pattern_with_previous_round")
    )


def semantic_economy_observation(item: Dict[str, Any]) -> Dict[str, Any]:
    validation = get_validation_result(item)
    if not validation:
        return {
            "mode": None,
            "evaluated": None,
            "llm_status": "unknown",
            "risk": "not_evaluated",
            "semantic_redundancy_dominant": None,
            "shared_context_repeated": None,
            "answer_hint_expansion": None,
            "surface_leak_risk": None,
            "surface_leak_type": [],
            "manual_review_status": "not_reviewed",
            "estimated_prompt_chars": len(_clean_text(item.get("prompt"))),
            "prompt_char_delta": None,
            "prompt_char_growth_ratio": None,
        }
    leak_types = validation.get("surface_leak_type")
    return {
        "mode": validation.get("semantic_economy_mode"),
        "evaluated": validation.get("semantic_economy_evaluated"),
        "llm_status": validation.get("semantic_economy_llm_status", "unknown"),
        "risk": validation.get("semantic_economy_risk", "not_evaluated"),
        "semantic_redundancy_dominant": validation.get("semantic_redundancy_dominant"),
        "shared_context_repeated": validation.get("shared_context_repeated"),
        "answer_hint_expansion": validation.get("answer_hint_expansion"),
        "surface_leak_risk": validation.get("surface_leak_risk"),
        "surface_leak_type": list(leak_types) if isinstance(leak_types, list) else [],
        "manual_review_status": validation.get("semantic_economy_manual_review_status", "not_reviewed"),
        "estimated_prompt_chars": validation.get("estimated_prompt_chars", len(_clean_text(item.get("prompt")))),
        "prompt_char_delta": validation.get("prompt_char_delta"),
        "prompt_char_growth_ratio": validation.get("prompt_char_growth_ratio"),
    }


def _hit_confidence(
    score_drop: float,
    *,
    focus_matches: bool,
    answer_present: bool,
    focus_alignment_confidence: str,
    score_drop_threshold: float,
) -> str:
    if (
        score_drop >= max(0.30, score_drop_threshold * 2)
        and focus_matches
        and answer_present
        and focus_alignment_confidence in {"medium", "high"}
    ):
        return "high"
    if score_drop >= score_drop_threshold and focus_matches:
        return "medium"
    return "low"


def build_effect_analysis(
    item: Dict[str, Any],
    previous_item: Optional[Dict[str, Any]] = None,
    *,
    full_score_threshold: float = DEFAULT_FULL_SCORE_THRESHOLD,
    score_drop_threshold: float = DEFAULT_SCORE_DROP_THRESHOLD,
    review_drop_threshold: float = DEFAULT_REVIEW_DROP_THRESHOLD,
    score_increase_threshold: float = DEFAULT_SCORE_INCREASE_THRESHOLD,
) -> Dict[str, Any]:
    score_rate_after = get_score_rate(item)
    score_rate_before = get_score_rate_before(item, previous_item)
    if score_rate_after is None:
        raise ValueError(f"record {record_key(item)!r} missing score_rate_after")
    if score_rate_before is None:
        raise ValueError(f"record {record_key(item)!r} missing score_rate_before")

    delta_score_rate = score_rate_after - score_rate_before
    score_drop = score_rate_before - score_rate_after
    evolved = is_question_evolved(item)
    complexity_passed = validation_passed_for_effect(item)
    repeated = is_repeated_pattern(item)
    focus = get_expected_focus(item)
    focus_present = bool(focus)
    candidate_answer = get_candidate_answer(item)
    answer_present = bool(candidate_answer)
    focus_alignment = analyze_focus_answer_alignment(focus, candidate_answer)
    focus_matches = bool(focus_alignment.get("matches"))
    is_full_score = score_rate_after >= full_score_threshold
    full_score_broken = score_rate_before >= full_score_threshold and score_rate_after < full_score_threshold
    strong_drop = score_drop >= score_drop_threshold
    review_drop = score_drop >= review_drop_threshold
    score_increased_after_evolution = evolved and delta_score_rate > score_increase_threshold

    lightweight_boundary_hit = (
        evolved
        and complexity_passed
        and not repeated
        and focus_present
        and focus_matches
        and (strong_drop or full_score_broken or review_drop)
    )
    confidence = (
        _hit_confidence(
            score_drop,
            focus_matches=focus_matches,
            answer_present=answer_present,
            focus_alignment_confidence=_clean_text(focus_alignment.get("confidence")),
            score_drop_threshold=score_drop_threshold,
        )
        if lightweight_boundary_hit
        else "low"
    )

    if evolved and not complexity_passed:
        effect_label = "invalid_complexity"
        reason = "候选题未通过复杂度或可回答性校验。"
    elif not evolved:
        effect_label = "pass_through"
        reason = "透传样本未进入 question evolution。"
    elif repeated:
        effect_label = "repeated_pattern"
        reason = "题型与上一轮重复，不应作为有效边界命中沉淀。"
    elif lightweight_boundary_hit and confidence in {"medium", "high"}:
        effect_label = "effective_boundary_probe"
        reason = "题目通过复杂度校验、分数下降，且 candidate_answer 错误方向与 expected_evaluation_focus 基本一致；仍需人工复核。"
    elif lightweight_boundary_hit:
        effect_label = "needs_manual_review"
        reason = "题目通过复杂度校验但分数下降幅度较小，仅能作为低置信命中候选。"
    elif evolved and complexity_passed and (strong_drop or full_score_broken or review_drop) and (not focus_present or not focus_matches):
        effect_label = "needs_manual_review"
        reason = _clean_text(focus_alignment.get("reason")) or "分数下降但无法确认错误方向压中预期 focus。"
    elif score_increased_after_evolution:
        effect_label = "score_increased"
        reason = "score rate increased after evolution; current rewrite did not expose a clearer boundary."
    elif is_full_score:
        effect_label = "full_score_no_drop"
        reason = "新一轮评分仍为满分，当前算子未形成有效压测。"
    elif delta_score_rate > score_increase_threshold:
        effect_label = "score_increased"
        reason = "新一轮得分率升高，当前改写未带来更清晰边界。"
    elif is_full_score:
        effect_label = "full_score_no_drop"
        reason = "新一轮评分仍为满分，当前算子未形成有效压测。"
    else:
        effect_label = "no_clear_effect"
        reason = "未观察到足够清晰的得分变化。"

    semantic_observation = semantic_economy_observation(item)
    metadata = get_metadata(item)
    reference_rebuild = metadata.get("reference_rebuild") if isinstance(metadata, dict) else {}
    reference_verification = reference_rebuild.get("verification") if isinstance(reference_rebuild, dict) else {}
    reference_verified = bool(isinstance(reference_verification, dict) and reference_verification.get("verified") is True)
    scoring_result = item.get("scoring_result") if isinstance(item.get("scoring_result"), dict) else {}
    # Qwen must have repeatable completed judgement observations.  A single
    # representative score is useful for routing, but not enough to confirm a
    # boundary effect.  The strong-answer negative-control also has to finish.
    scoring_stable = completed_score_summary(scoring_result.get("qwen_score_summary"), minimum_successes=2)
    strong_answer_checked = completed_score_summary(scoring_result.get("gpt_answer_score_summary"), minimum_successes=1)
    effect_scope_allowed = scope_allows(item, "effect_claim")
    validation_disposition = get_validation_result(item).get("validation_disposition")
    validation_disposition = validation_disposition if isinstance(validation_disposition, dict) else {}
    invalidating_risk = validation_disposition.get("status") == "technical_block"
    confirmed_effect = bool(lightweight_boundary_hit and reference_verified and scoring_stable and strong_answer_checked and not invalidating_risk and effect_scope_allowed)
    if confirmed_effect:
        next_round_decision = ["retain_as_boundary", "stop_branch_success"]
    elif score_increased_after_evolution:
        next_round_decision = ["backtrack_to_parent", "switch_operator"]
    elif effect_label == "needs_manual_review":
        next_round_decision = ["manual_review", "retry_generation_strategy"]
    elif effect_label in {"pass_through", "invalid_complexity"}:
        next_round_decision = ["pass_through"]
    else:
        next_round_decision = ["switch_operator", "retry_generation_strategy"]
    return {
        "score_rate_before": score_rate_before,
        "score_rate_after": score_rate_after,
        "baseline_score_source": get_score_rate_before_source(item, previous_item),
        "score_after_source": get_score_source(item),
        "delta_score_rate": delta_score_rate,
        "operator_used": get_operator_used(item),
        "question_length": len(_clean_text(item.get("prompt"))),
        "semantic_economy_observation": semantic_observation,
        "is_full_score": is_full_score,
        "score_increased_after_evolution": score_increased_after_evolution,
        "complexity_passed": complexity_passed,
        "repeated_pattern_with_previous_round": repeated,
        "lightweight_boundary_hit": lightweight_boundary_hit,
        "hit_confidence": confidence,
        "needs_manual_review": bool(lightweight_boundary_hit) or effect_label == "needs_manual_review",
        "focus_answer_alignment": focus_alignment,
        "lightweight_hit_reason": reason,
        "effect_label": effect_label,
        "effect_confirmation": {
            "status": "confirmed" if confirmed_effect else ("provisional" if lightweight_boundary_hit else "not_confirmed"),
            "reference_answer_verified": reference_verified,
            "strong_answer_checked": strong_answer_checked,
            "scoring_stable": scoring_stable,
            "execution_scope_allows_effect_claim": effect_scope_allowed,
            "invalidating_validation_risk": invalidating_risk,
        },
        "next_round_decision": next_round_decision,
    }


def attach_effect_analysis(
    item: Dict[str, Any],
    previous_item: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    result = dict(item)
    result["effect_analysis"] = build_effect_analysis(item, previous_item, **kwargs)
    return result


def analyze_records(
    records: Sequence[Dict[str, Any]],
    *,
    previous_records: Optional[Sequence[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> List[Dict[str, Any]]:
    previous_by_key = records_by_key(previous_records or [])
    analyzed: List[Dict[str, Any]] = []
    for record in records:
        analyzed.append(
            attach_effect_analysis(
                record,
                previous_by_key.get(record_key(record)),
                **kwargs,
            )
        )
    return analyzed


def _signature_field(item: Dict[str, Any], source: str, field: str) -> str:
    value = item.get(source)
    if not isinstance(value, dict):
        return ""
    return _clean_text(value.get(field))


def build_effect_matrix(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    grouped: DefaultDict[Tuple[str, str, str, str], Dict[str, Any]] = defaultdict(
        lambda: {
            "sample_count": 0,
            "delta_score_rate_sum": 0.0,
            "lightweight_boundary_hit_count": 0,
            "effective_boundary_probe_count": 0,
            "score_increased_after_evolution_count": 0,
            "full_score_count": 0,
            "invalid_complexity_count": 0,
            "repeated_pattern_count": 0,
            "semantic_risk_count": 0,
            "semantic_redundancy_count": 0,
            "shared_context_repeated_count": 0,
            "answer_hint_expansion_count": 0,
            "surface_leak_count": 0,
            "surface_leak_types": Counter(),
            "manual_review_statuses": Counter(),
            "prompt_char_deltas": [],
        }
    )
    for record in records:
        effect = record.get("effect_analysis")
        if not isinstance(effect, dict):
            continue
        key = (
            _signature_field(record, "sample_profile", "core_capability"),
            _signature_field(record, "overscore_diagnosis", "candidate_overscore_cause"),
            _signature_field(record, "overscore_diagnosis", "target_failure_mode"),
            _clean_text(effect.get("operator_used")),
        )
        bucket = grouped[key]
        bucket["sample_count"] += 1
        bucket["delta_score_rate_sum"] += float(effect.get("delta_score_rate", 0) or 0)
        if effect.get("lightweight_boundary_hit"):
            bucket["lightweight_boundary_hit_count"] += 1
        if effect.get("effect_label") == "effective_boundary_probe":
            bucket["effective_boundary_probe_count"] += 1
        if effect.get("score_increased_after_evolution"):
            bucket["score_increased_after_evolution_count"] += 1
        if effect.get("is_full_score"):
            bucket["full_score_count"] += 1
        if not effect.get("complexity_passed"):
            bucket["invalid_complexity_count"] += 1
        if effect.get("repeated_pattern_with_previous_round"):
            bucket["repeated_pattern_count"] += 1
        semantic = effect.get("semantic_economy_observation")
        if isinstance(semantic, dict):
            if semantic.get("risk") in {"medium", "high"}:
                bucket["semantic_risk_count"] += 1
            for field, counter_name in (
                ("semantic_redundancy_dominant", "semantic_redundancy_count"),
                ("shared_context_repeated", "shared_context_repeated_count"),
                ("answer_hint_expansion", "answer_hint_expansion_count"),
                ("surface_leak_risk", "surface_leak_count"),
            ):
                if semantic.get(field) is True:
                    bucket[counter_name] += 1
            bucket["surface_leak_types"].update(semantic.get("surface_leak_type") or [])
            bucket["manual_review_statuses"].update([_clean_text(semantic.get("manual_review_status")) or "not_reviewed"])
            delta = semantic.get("prompt_char_delta")
            if isinstance(delta, (int, float)):
                bucket["prompt_char_deltas"].append(delta)

    matrix: List[Dict[str, Any]] = []
    for (
        core_capability,
        candidate_overscore_cause,
        target_failure_mode,
        operator_used,
    ), bucket in sorted(grouped.items()):
        sample_count = bucket["sample_count"]
        hit_count = bucket["lightweight_boundary_hit_count"]
        matrix.append(
            {
                "core_capability": core_capability,
                "candidate_overscore_cause": candidate_overscore_cause,
                "target_failure_mode": target_failure_mode,
                "operator_used": operator_used,
                "sample_count": sample_count,
                "avg_delta_score_rate": bucket["delta_score_rate_sum"] / sample_count if sample_count else 0,
                "lightweight_boundary_hit_count": hit_count,
                "lightweight_boundary_hit_rate": hit_count / sample_count if sample_count else 0,
                "effective_boundary_probe_count": bucket["effective_boundary_probe_count"],
                "score_increased_after_evolution_count": bucket["score_increased_after_evolution_count"],
                "full_score_count": bucket["full_score_count"],
                "invalid_complexity_count": bucket["invalid_complexity_count"],
                "repeated_pattern_count": bucket["repeated_pattern_count"],
                "semantic_risk_count": bucket["semantic_risk_count"],
                "semantic_redundancy_count": bucket["semantic_redundancy_count"],
                "shared_context_repeated_count": bucket["shared_context_repeated_count"],
                "answer_hint_expansion_count": bucket["answer_hint_expansion_count"],
                "surface_leak_count": bucket["surface_leak_count"],
                "surface_leak_type_counts": dict(sorted(bucket["surface_leak_types"].items())),
                "semantic_manual_review_status_counts": dict(sorted(bucket["manual_review_statuses"].items())),
                "avg_prompt_char_delta_observation": (
                    sum(bucket["prompt_char_deltas"]) / len(bucket["prompt_char_deltas"])
                    if bucket["prompt_char_deltas"] else None
                ),
            }
        )
    return matrix


def build_semantic_economy_report(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize semantic risks by operator; character fields stay observational."""

    by_operator: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        effect = record.get("effect_analysis")
        if not isinstance(effect, dict):
            continue
        observation = effect.get("semantic_economy_observation")
        if isinstance(observation, dict):
            by_operator[_clean_text(effect.get("operator_used")) or "unknown"].append(observation)

    operator_rows = []
    for operator_id, observations in sorted(by_operator.items()):
        leak_types = Counter(
            leak_type
            for observation in observations
            for leak_type in observation.get("surface_leak_type", [])
            if _clean_text(leak_type)
        )
        risks = Counter(_clean_text(observation.get("risk")) or "not_evaluated" for observation in observations)
        reviews = Counter(
            _clean_text(observation.get("manual_review_status")) or "not_reviewed"
            for observation in observations
        )
        chars = [
            value for value in (observation.get("estimated_prompt_chars") for observation in observations)
            if isinstance(value, (int, float))
        ]
        operator_rows.append({
            "operator_id": operator_id,
            "sample_count": len(observations),
            "semantic_economy_risk_counts": dict(sorted(risks.items())),
            "semantic_redundancy_count": sum(observation.get("semantic_redundancy_dominant") is True for observation in observations),
            "shared_context_repeated_count": sum(observation.get("shared_context_repeated") is True for observation in observations),
            "answer_hint_expansion_count": sum(observation.get("answer_hint_expansion") is True for observation in observations),
            "surface_leak_count": sum(observation.get("surface_leak_risk") is True for observation in observations),
            "surface_leak_type_counts": dict(sorted(leak_types.items())),
            "semantic_manual_review_status_counts": dict(sorted(reviews.items())),
            "character_observation": {
                "count": len(chars),
                "min": min(chars) if chars else None,
                "max": max(chars) if chars else None,
                "mean": sum(chars) / len(chars) if chars else None,
                "decision_use": "record_only",
            },
        })
    return {
        "report_version": "semantic_economy_report_v1",
        "character_metrics_policy": "record_only",
        "by_operator": operator_rows,
    }


def write_json(value: Dict[str, Any], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, indent=2)
        target.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Analyze lightweight question-evolution effects after scoring.")
    parser.add_argument("--input", required=True, help="Input current scored JSON/JSONL path.")
    parser.add_argument("--output", required=True, help="Output analyzed JSONL path.")
    parser.add_argument("--before", default=None, help="Optional previous scored JSON/JSONL path for score_rate_before.")
    parser.add_argument("--matrix-output", default=None, help="Optional Sample Type x Operator matrix JSONL path.")
    parser.add_argument("--semantic-report-output", default=None, help="Optional semantic economy JSON report path.")
    parser.add_argument("--full-score-threshold", type=float, default=DEFAULT_FULL_SCORE_THRESHOLD)
    parser.add_argument("--score-drop-threshold", type=float, default=DEFAULT_SCORE_DROP_THRESHOLD)
    parser.add_argument("--review-drop-threshold", type=float, default=DEFAULT_REVIEW_DROP_THRESHOLD)
    parser.add_argument("--score-increase-threshold", type=float, default=DEFAULT_SCORE_INCREASE_THRESHOLD)
    parser.add_argument("--performance-events", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stage = "analyze_evolution_effect"
    metrics = StageMetrics(stage)
    metrics.input_bytes = os.path.getsize(args.input)
    parse_started = time.monotonic()
    records = load_json_or_jsonl(args.input)
    previous_records = load_json_or_jsonl(args.before) if args.before else None
    metrics.parse_seconds += time.monotonic() - parse_started
    compute_started = time.monotonic()
    analyzed = analyze_records(
        records,
        previous_records=previous_records,
        full_score_threshold=args.full_score_threshold,
        score_drop_threshold=args.score_drop_threshold,
        review_drop_threshold=args.review_drop_threshold,
        score_increase_threshold=args.score_increase_threshold,
    )
    metrics.compute_seconds += time.monotonic() - compute_started
    publish_records(
        analyzed,
        args.output,
        stage=stage,
        input_path=args.input,
        config={
            "before": os.path.abspath(args.before) if args.before else None,
            "before_sha256": sha256_file(args.before) if args.before else None,
            "full_score_threshold": args.full_score_threshold,
            "score_drop_threshold": args.score_drop_threshold,
            "review_drop_threshold": args.review_drop_threshold,
            "score_increase_threshold": args.score_increase_threshold,
        },
        performance_path=args.performance_events,
        code_paths=[__file__],
        metrics=metrics,
    )
    if args.matrix_output:
        write_jsonl(build_effect_matrix(analyzed), args.matrix_output)
    if args.semantic_report_output:
        write_json(build_semantic_economy_report(analyzed), args.semantic_report_output)


if __name__ == "__main__":
    main()
