import argparse
import json
import os
from collections import Counter, defaultdict
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple


RISK_SCORE = {"low": 0, "medium": -5, "high": -25}
TEMPLATE_RISK_PENALTIES = {"medium": 0.05, "high": 0.10}
LIGHT_FACTUAL_WARNING_PENALTY = 0.05
DIFFICULTY_GAIN_HARD_REJECT_TAGS = {
    "missing_premise_named",
    "conclusion_hint_revealed",
    "answer_path_scaffolded",
    "external_fact_dependency",
    "format_difficulty_only",
    "axis_shift",
    "unanswerable_or_external",
    "multi_axis",
    "direct_answer_leakage",
}
DIFFICULTY_GAIN_HARD_REJECT_LABELS = {
    "leakage_or_simplification",
    "format_complexity_only",
    "axis_shift",
    "unanswerable_or_external",
}
PASS_DIFFICULTY_GAIN_LABELS = {"clear_gain", "probable_gain", "not_applicable"}
EXPLORATION_DIFFICULTY_GAIN_LABELS = {"weak_gain", "needs_manual_review"}
DIFFICULTY_GAIN_LABEL_ALIASES = {
    "borderline_gain": "weak_gain",
    "uncertain_gain": "needs_manual_review",
}
EXPLORATION_SCORE_CAPS = {
    "weak_gain": 0.60,
    "needs_manual_review": 0.58,
    "no_gain": 0.55,
}
NO_GAIN_EXPLORATION_ACTIONS = {
    "needs_manual_review",
    "manual_review",
    "admit_low_priority",
    "admit_if_no_better_candidate",
    "low_priority_review",
    "explore_low_priority",
}


def _read_nonnegative_int_env(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)) or default)
    except (TypeError, ValueError):
        return default
    return max(0, value)


MAX_EXPLORATION_CANDIDATES_PER_ROUND = _read_nonnegative_int_env(
    "MAX_EXPLORATION_CANDIDATES_PER_ROUND",
    5,
)
BORDERLINE_DIFFICULTY_GAIN_SCORE = 0.65
NO_GAIN_EXPLORATION_MARGIN = 0.05


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


def write_jsonl(records: Iterable[Dict[str, Any]], output_path: str, *, append: bool = False) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    mode = "a" if append else "w"
    with open(output_path, mode, encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _coerce_score(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number < 0:
        return 0.0
    if number > 1:
        return 1.0
    return number


def _metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    meta_info = item.get("meta_info")
    if not isinstance(meta_info, dict):
        return {}
    metadata = meta_info.get("question_evolution_metadata")
    return metadata if isinstance(metadata, dict) else {}


def candidate_group_id(item: Dict[str, Any]) -> str:
    for field in ("candidate_group_id", "sample_id", "index"):
        value = item.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return _clean_text(item.get("prompt"))


def candidate_id(item: Dict[str, Any], fallback_index: int) -> str:
    value = item.get("candidate_id")
    if value is not None and str(value).strip():
        return str(value).strip()
    return f"{candidate_group_id(item)}::cand_{fallback_index}"


def candidate_operator(item: Dict[str, Any]) -> str:
    for field in ("candidate_operator", "operator_used"):
        value = _clean_text(item.get(field))
        if value:
            return value
    return _clean_text(_metadata(item).get("operator_used"))


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


def round_value(item: Dict[str, Any]) -> int:
    try:
        number = int(item.get("round", 0) or 0)
    except (TypeError, ValueError):
        return 0
    return number if number >= 0 else 0


def validation_result(item: Dict[str, Any]) -> Dict[str, Any]:
    result = item.get("validation_result")
    return result if isinstance(result, dict) else {"passed": False, "reject_reason": "missing validation_result"}


def difficulty_gain_validation(item: Dict[str, Any]) -> Dict[str, Any]:
    result = item.get("difficulty_gain_validation")
    return result if isinstance(result, dict) else {}


def light_factual_check(item: Dict[str, Any]) -> Dict[str, Any]:
    result = item.get("light_factual_check")
    return result if isinstance(result, dict) else {"passed": True, "fatal_errors": [], "warnings": [], "risk_tags": []}


def normalized_difficulty_gain_label(validation: Dict[str, Any]) -> str:
    label = _clean_text(validation.get("difficulty_gain_label"))
    return DIFFICULTY_GAIN_LABEL_ALIASES.get(label, label)


def difficulty_gain_risk_tags(validation: Dict[str, Any]) -> List[str]:
    tags = validation.get("risk_tags", [])
    if not isinstance(tags, list):
        return []
    return [_clean_text(tag) for tag in tags if _clean_text(tag)]


def _first_field_value(sources: Sequence[Dict[str, Any]], field: str) -> Any:
    for source in sources:
        if isinstance(source, dict) and field in source:
            return source.get(field)
    return None


def _candidate_generation(item: Dict[str, Any]) -> Dict[str, Any]:
    generation = item.get("candidate_generation")
    return generation if isinstance(generation, dict) else {}


def _risk_level(value: Any) -> str:
    text = _clean_text(value).lower()
    return text if text in {"low", "medium", "high"} else ""


def template_affordance_risk(item: Dict[str, Any], validation: Optional[Dict[str, Any]] = None) -> str:
    validation = validation if isinstance(validation, dict) else difficulty_gain_validation(item)
    value = _first_field_value(
        [validation, validation_result(item), _candidate_generation(item), _metadata(item), item],
        "template_affordance_risk",
    )
    return _risk_level(value)


def rubric_shortcut_risk(item: Dict[str, Any], validation: Optional[Dict[str, Any]] = None) -> str:
    validation = validation if isinstance(validation, dict) else difficulty_gain_validation(item)
    value = _first_field_value(
        [validation, validation_result(item), _candidate_generation(item), _metadata(item), item],
        "rubric_shortcut_risk",
    )
    return _risk_level(value)


def light_factual_fatal_errors(item: Dict[str, Any]) -> List[str]:
    errors = light_factual_check(item).get("fatal_errors", [])
    return [_clean_text(error) for error in errors if _clean_text(error)] if isinstance(errors, list) else []


def light_factual_warnings(item: Dict[str, Any]) -> List[str]:
    warnings = light_factual_check(item).get("warnings", [])
    return [_clean_text(warning) for warning in warnings if _clean_text(warning)] if isinstance(warnings, list) else []


def has_hard_risk(validation: Dict[str, Any]) -> bool:
    return bool(set(difficulty_gain_risk_tags(validation)) & DIFFICULTY_GAIN_HARD_REJECT_TAGS)


def _risk_penalty(validation: Dict[str, Any], field: str) -> int:
    return RISK_SCORE.get(_clean_text(validation.get(field)), -10)


def validation_quality_score(item: Dict[str, Any]) -> float:
    validation = validation_result(item)
    if not validation.get("passed"):
        return 0.0
    score = 0.50
    main_axis_count = int(validation.get("main_axis_count", 1) or 0)
    prompt_chars = int(validation.get("estimated_prompt_chars", len(_clean_text(item.get("prompt")))) or 0)
    output_tasks = int(validation.get("output_tasks_count", 1) or 0)
    candidate_options = int(validation.get("candidate_options_count", 0) or 0)
    counterfactuals = int(validation.get("counterfactual_count", 0) or 0)

    if main_axis_count == 1:
        score += 0.12
    if 120 <= prompt_chars <= 900:
        score += 0.10
    elif prompt_chars <= 1200:
        score += 0.04
    if output_tasks <= 1:
        score += 0.08
    if candidate_options <= 2:
        score += 0.04
    if counterfactuals == 0:
        score += 0.03
    for field in ("external_knowledge_risk", "format_difficulty_risk", "repeat_pattern_risk"):
        risk = _clean_text(validation.get(field))
        if risk == "low":
            score += 0.03
        elif risk == "high":
            score -= 0.10
    return max(0.0, min(1.0, round(score, 4)))


def legacy_score_candidate(item: Dict[str, Any]) -> Tuple[float, List[str]]:
    validation = validation_result(item)
    reasons: List[str] = []
    if not validation.get("passed"):
        return -10_000.0, [_clean_text(validation.get("reject_reason")) or "failed complexity validation"]

    score = 100.0
    quality = validation_quality_score(item)
    score += quality * 40
    if validation.get("main_axis_count") == 1:
        reasons.append("single main axis")
    if validation.get("output_tasks_count", 1) <= 1:
        reasons.append("single output task")
    if validation.get("candidate_options_count", 0) <= 2:
        reasons.append("limited candidate options")
    if validation.get("counterfactual_count", 0) == 0:
        reasons.append("no counterfactual complexity")
    score += _risk_penalty(validation, "external_knowledge_risk")
    score += _risk_penalty(validation, "format_difficulty_risk")
    score += _risk_penalty(validation, "repeat_pattern_risk")

    generation = item.get("candidate_generation")
    if isinstance(generation, dict) and generation.get("operator_source") == "primary":
        score += 2
        reasons.append("from router primary operator")
    focus = _metadata(item).get("expected_evaluation_focus")
    if isinstance(focus, list) and focus:
        score += 3
        reasons.append("keeps expected_evaluation_focus metadata")

    return score, reasons


def weak_probe_score(validation: Dict[str, Any]) -> float:
    weak_probe = validation.get("weak_probe")
    if not isinstance(weak_probe, dict) or weak_probe.get("enabled") is False:
        return 0.5
    if weak_probe.get("target_failure_match") is True:
        return 1.0
    if _clean_text(weak_probe.get("probe_judgment")) in {
        "candidate likely exposes target failure",
        "candidate is harder than original",
    }:
        return 1.0
    return 0.0


def _dict_score(source: Dict[str, Any], field: str) -> Optional[float]:
    if field not in source:
        return None
    return _coerce_score(source.get(field))


def template_simplification_hard_reject(item: Dict[str, Any], validation: Dict[str, Any]) -> bool:
    generic_score = _dict_score(validation, "generic_answer_success_likelihood")
    if generic_score is None:
        generic_score = _dict_score(item, "generic_answer_success_likelihood")
    fact_score = _dict_score(validation, "specific_fact_dependency_score")
    if fact_score is None:
        fact_score = _dict_score(item, "specific_fact_dependency_score")
    return generic_score is not None and fact_score is not None and generic_score >= 0.85 and fact_score <= 0.35


def weak_probe_target_match(validation: Dict[str, Any]) -> bool:
    weak_probe = validation.get("weak_probe")
    return isinstance(weak_probe, dict) and (
        weak_probe.get("target_failure_match") is True
        or _clean_text(weak_probe.get("probe_judgment"))
        in {"candidate likely exposes target failure", "candidate is harder than original"}
    )


def has_surface_change_signal(item: Dict[str, Any]) -> bool:
    generation = item.get("candidate_generation")
    if not isinstance(generation, dict):
        generation = {}
    metadata = _metadata(item)
    for source in (generation, metadata, item):
        for field in (
            "surface_form_family",
            "surface_form",
            "question_surface_form",
            "surface_changed",
            "surface_form_changed",
            "changed_surface_form",
        ):
            value = source.get(field) if isinstance(source, dict) else None
            if isinstance(value, bool) and value:
                return True
            if isinstance(value, str) and value.strip():
                return True
    return False


def has_no_gain_exploration_value(item: Dict[str, Any], validation: Dict[str, Any]) -> bool:
    recommended_action = _clean_text(validation.get("recommended_action"))
    difficulty_gain_score = _coerce_score(validation.get("difficulty_gain_score"))
    return (
        recommended_action in NO_GAIN_EXPLORATION_ACTIONS
        or difficulty_gain_score >= BORDERLINE_DIFFICULTY_GAIN_SCORE - NO_GAIN_EXPLORATION_MARGIN
        or weak_probe_target_match(validation)
        or has_surface_change_signal(item)
    )


def candidate_flow_info(
    item: Dict[str, Any],
    *,
    allow_missing_difficulty_gain: bool = False,
) -> Dict[str, Any]:
    validation = validation_result(item)
    if item.get("question_evolved") is False:
        return {
            "candidate_flow": "pass_through_candidate",
            "selection_status": "pass_through_original",
            "reason": "original sample pass-through",
            "difficulty_gain_label": "",
            "risk_tags": [],
            "score_cap": None,
        }
    if validation.get("passed") is not True:
        return {
            "candidate_flow": "hard_reject",
            "selection_status": "failed_complexity_validation",
            "reason": _clean_text(validation.get("reject_reason")) or "failed complexity validation",
            "difficulty_gain_label": "",
            "risk_tags": [],
            "score_cap": None,
        }

    factual_errors = light_factual_fatal_errors(item)
    if factual_errors:
        return {
            "candidate_flow": "hard_reject",
            "selection_status": "hard_reject_light_factual_check",
            "reason": "light_factual_check fatal errors: " + "; ".join(factual_errors[:3]),
            "difficulty_gain_label": "",
            "risk_tags": light_factual_check(item).get("risk_tags", []),
            "score_cap": None,
        }

    difficulty_validation = difficulty_gain_validation(item)
    if not difficulty_validation:
        if allow_missing_difficulty_gain:
            return {
                "candidate_flow": "main_chain_candidate",
                "selection_status": "selected_legacy_without_difficulty_gain",
                "reason": "legacy mode allows missing difficulty_gain_validation",
                "difficulty_gain_label": "",
                "risk_tags": [],
                "score_cap": None,
            }
        return {
            "candidate_flow": "hard_reject",
            "selection_status": "missing_difficulty_gain_validation",
            "reason": "missing difficulty_gain_validation",
            "difficulty_gain_label": "",
            "risk_tags": [],
            "score_cap": None,
        }

    label = normalized_difficulty_gain_label(difficulty_validation)
    raw_label = _clean_text(difficulty_validation.get("difficulty_gain_label"))
    risk_tags = difficulty_gain_risk_tags(difficulty_validation)
    hard_tags = sorted(set(risk_tags) & DIFFICULTY_GAIN_HARD_REJECT_TAGS)
    if label in DIFFICULTY_GAIN_HARD_REJECT_LABELS:
        return {
            "candidate_flow": "hard_reject",
            "selection_status": "hard_reject_difficulty_gain_label",
            "reason": f"difficulty_gain_label={raw_label or label} is hard reject",
            "difficulty_gain_label": label,
            "risk_tags": risk_tags,
            "score_cap": None,
        }
    if hard_tags:
        return {
            "candidate_flow": "hard_reject",
            "selection_status": "hard_reject_risk_tag",
            "reason": "hard difficulty-gain risk tags: " + ", ".join(hard_tags),
            "difficulty_gain_label": label,
            "risk_tags": risk_tags,
            "score_cap": None,
        }
    if template_simplification_hard_reject(item, difficulty_validation):
        return {
            "candidate_flow": "hard_reject",
            "selection_status": "hard_reject_template_simplification",
            "reason": "template simplification signal: generic success high and fact dependency low",
            "difficulty_gain_label": label,
            "risk_tags": risk_tags,
            "score_cap": None,
        }

    if difficulty_validation.get("passed") is True and label in PASS_DIFFICULTY_GAIN_LABELS:
        return {
            "candidate_flow": "main_chain_candidate",
            "selection_status": "selected_after_difficulty_gain_validation",
            "reason": f"difficulty_gain_label={raw_label or label} passed main chain",
            "difficulty_gain_label": label,
            "risk_tags": risk_tags,
            "score_cap": None,
        }

    if label in EXPLORATION_DIFFICULTY_GAIN_LABELS:
        return {
            "candidate_flow": "exploration_candidate",
            "selection_status": "selected_for_exploration",
            "reason": f"difficulty_gain_label={raw_label or label} enters exploration",
            "difficulty_gain_label": label,
            "risk_tags": risk_tags,
            "score_cap": EXPLORATION_SCORE_CAPS[label],
        }

    if label == "no_gain":
        if has_no_gain_exploration_value(item, difficulty_validation):
            return {
                "candidate_flow": "exploration_candidate",
                "selection_status": "selected_for_exploration",
                "reason": "no_gain has exploration value",
                "difficulty_gain_label": label,
                "risk_tags": risk_tags,
                "score_cap": EXPLORATION_SCORE_CAPS[label],
            }
        return {
            "candidate_flow": "pass_through_candidate",
            "selection_status": "not_selected_no_exploration_value",
            "reason": "no_gain without exploration value",
            "difficulty_gain_label": label,
            "risk_tags": risk_tags,
            "score_cap": None,
        }

    if difficulty_validation.get("passed") is not True:
        return {
            "candidate_flow": "hard_reject",
            "selection_status": "difficulty_gain_validation_failed",
            "reason": _clean_text(difficulty_validation.get("reject_reason"))
            or f"difficulty_gain_label={raw_label or label} did not pass",
            "difficulty_gain_label": label,
            "risk_tags": risk_tags,
            "score_cap": None,
        }

    return {
        "candidate_flow": "hard_reject",
        "selection_status": "unsupported_difficulty_gain_label",
        "reason": f"difficulty_gain_label={raw_label or label} is unsupported",
        "difficulty_gain_label": label,
        "risk_tags": risk_tags,
        "score_cap": None,
    }


def difficulty_gain_reject_reason(item: Dict[str, Any]) -> Optional[str]:
    flow = candidate_flow_info(item)
    if flow["candidate_flow"] == "hard_reject":
        return _clean_text(flow.get("reason")) or "difficulty-gain hard reject"
    return None


def score_candidate(item: Dict[str, Any], *, allow_missing_difficulty_gain: bool = False) -> Tuple[float, List[str]]:
    validation = validation_result(item)
    if not validation.get("passed"):
        return -10_000.0, [_clean_text(validation.get("reject_reason")) or "failed complexity validation"]

    difficulty_validation = difficulty_gain_validation(item)
    if not difficulty_validation and allow_missing_difficulty_gain:
        legacy_score, legacy_reasons = legacy_score_candidate(item)
        legacy_reasons.append("legacy mode allows missing difficulty_gain_validation")
        return legacy_score, legacy_reasons

    flow = candidate_flow_info(item, allow_missing_difficulty_gain=allow_missing_difficulty_gain)
    if flow["candidate_flow"] == "hard_reject":
        return -9_000.0, [_clean_text(flow.get("reason")) or "hard reject"]
    if flow["candidate_flow"] == "pass_through_candidate":
        return -1_000.0, [_clean_text(flow.get("reason")) or "pass-through candidate"]

    difficulty_gain_score = _coerce_score(difficulty_validation.get("difficulty_gain_score"))
    no_leakage_score = _coerce_score(difficulty_validation.get("no_leakage_score"))
    competitive_score = _coerce_score(difficulty_validation.get("competitive_judgment_score"))
    probe_score = weak_probe_score(difficulty_validation)
    validation_score = validation_quality_score(item)
    selection_score = (
        0.45 * difficulty_gain_score
        + 0.20 * no_leakage_score
        + 0.15 * competitive_score
        + 0.10 * probe_score
        + 0.10 * validation_score
    )
    reasons = [
        f"difficulty_gain_score={difficulty_gain_score:.2f}",
        f"no_leakage_score={no_leakage_score:.2f}",
        f"competitive_judgment_score={competitive_score:.2f}",
        f"validation_quality_score={validation_score:.2f}",
    ]
    if probe_score == 1.0:
        reasons.append("weak probe matches target failure")
    elif probe_score == 0.5:
        reasons.append("weak probe not enabled")

    warnings = light_factual_warnings(item)
    if warnings:
        selection_score -= LIGHT_FACTUAL_WARNING_PENALTY
        reasons.append(f"light_factual_check warning penalty -{LIGHT_FACTUAL_WARNING_PENALTY:.2f}")

    template_risk = template_affordance_risk(item, difficulty_validation)
    template_penalty = TEMPLATE_RISK_PENALTIES.get(template_risk, 0.0)
    if template_penalty:
        selection_score -= template_penalty
        reasons.append(f"template_affordance_risk={template_risk} penalty -{template_penalty:.2f}")

    score_cap = flow.get("score_cap")
    if isinstance(score_cap, float):
        selection_score = min(selection_score, score_cap)
        reasons.append(f"exploration score cap {flow.get('difficulty_gain_label')}<={score_cap:.2f}")

    return round(max(0.0, selection_score), 4), reasons


def build_rejected_candidate(item: Dict[str, Any], fallback_index: int, *, forced_reason: Optional[str] = None) -> Dict[str, Any]:
    validation = validation_result(item)
    difficulty_validation = difficulty_gain_validation(item)
    flow = candidate_flow_info(item)
    reason = forced_reason or _clean_text(flow.get("reason")) or _clean_text(validation.get("reject_reason")) or "not selected"
    rejected = {
        "candidate_id": candidate_id(item, fallback_index),
        "operator_used": candidate_operator(item),
        "reject_reason": reason,
        "validation_result": validation,
        "candidate_flow": flow.get("candidate_flow"),
        "selected_for_exploration": False,
    }
    if difficulty_validation:
        rejected["difficulty_gain_validation"] = difficulty_validation
    check = light_factual_check(item)
    if check:
        rejected["light_factual_check"] = check
    template_risk = template_affordance_risk(item, difficulty_validation)
    if template_risk:
        rejected["template_affordance_risk"] = template_risk
    rubric_risk = rubric_shortcut_risk(item, difficulty_validation)
    if rubric_risk:
        rejected["rubric_shortcut_risk"] = rubric_risk
    return rejected


def build_invalid_case(item: Dict[str, Any], fallback_index: int, *, reason: Optional[str] = None) -> Dict[str, Any]:
    validation = validation_result(item)
    metadata = _metadata(item)
    difficulty_validation = difficulty_gain_validation(item)
    flow = candidate_flow_info(item)
    invalid_type = validation.get("invalid_type") or "not_selected"
    if validation.get("passed") is False:
        invalid_type = validation.get("invalid_type") or "validation_failed"
    elif flow.get("candidate_flow") == "hard_reject":
        invalid_type = _clean_text(flow.get("difficulty_gain_label")) or _clean_text(flow.get("selection_status"))
    elif not difficulty_validation and validation.get("passed"):
        invalid_type = "missing_difficulty_gain_validation"
    invalid_case = {
        "sample_id": item.get("sample_id", item.get("index", "")),
        "round": round_value(item),
        "candidate_id": candidate_id(item, fallback_index),
        "operator_used": candidate_operator(item),
        "invalid_type": invalid_type,
        "reason": reason or _clean_text(flow.get("reason")) or _clean_text(validation.get("reject_reason")) or "candidate was not selected",
        "suggested_operator": metadata.get("operator_used") or item.get("candidate_operator"),
        "sample_signature": sample_signature(item),
        "candidate_flow": flow.get("candidate_flow"),
        "selected_for_exploration": False,
    }
    if difficulty_validation:
        invalid_case["difficulty_gain_validation"] = difficulty_validation
        invalid_case["risk_tags"] = difficulty_validation.get("risk_tags", [])
    check = light_factual_check(item)
    if check:
        invalid_case["light_factual_check"] = check
    template_risk = template_affordance_risk(item, difficulty_validation)
    if template_risk:
        invalid_case["template_affordance_risk"] = template_risk
    rubric_risk = rubric_shortcut_risk(item, difficulty_validation)
    if rubric_risk:
        invalid_case["rubric_shortcut_risk"] = rubric_risk
    failure_memory = item.get("failure_memory_candidate")
    if isinstance(failure_memory, dict):
        invalid_case["failure_memory_candidate"] = failure_memory
    return invalid_case


def _strip_candidate_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    result.pop("candidate_generation", None)
    return result


def _sync_operator_metadata(
    item: Dict[str, Any],
    operator_used: str,
    *,
    question_evolved: Optional[bool] = None,
) -> Dict[str, Any]:
    result = dict(item)
    meta_info = result.get("meta_info")
    meta_info = dict(meta_info) if isinstance(meta_info, dict) else {}
    metadata = meta_info.get("question_evolution_metadata")
    metadata = dict(metadata) if isinstance(metadata, dict) else {}
    metadata["operator_used"] = operator_used
    if question_evolved is not None:
        metadata["question_evolved"] = question_evolved
    meta_info["question_evolution_metadata"] = metadata
    result["meta_info"] = meta_info
    if operator_used:
        result["candidate_operator"] = operator_used
    else:
        result.pop("candidate_operator", None)
        result.pop("operator_used", None)
    return result


def _restore_original_passthrough(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    meta_info = result.get("meta_info")
    meta_info = dict(meta_info) if isinstance(meta_info, dict) else {}
    snapshot = meta_info.get("parent_snapshot")
    if isinstance(snapshot, dict):
        result["prompt"] = snapshot.get("prompt") or result.get("prompt")
        for field in ("rubric", "rubric_thought_process", "score_prompt", "scoring_result", "score_rate"):
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
        result["question_evolved"] = False
        meta_info.pop("parent_snapshot", None)
        result["meta_info"] = meta_info
        return result

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
    result["meta_info"] = meta_info
    result["question_evolved"] = False
    return _sync_operator_metadata(result, "", question_evolved=False)


def _main_reject_reasons(records: Sequence[Dict[str, Any]]) -> List[str]:
    counter: DefaultDict[str, int] = defaultdict(int)
    for record in records:
        flow = candidate_flow_info(record)
        reason = _clean_text(flow.get("reason"))
        if flow.get("candidate_flow") == "hard_reject" and reason:
            counter[reason] += 1
            continue
        difficulty_validation = difficulty_gain_validation(record)
        for tag in difficulty_validation.get("risk_tags", []) if difficulty_validation else []:
            text = _clean_text(tag)
            if text:
                counter[text] += 1
        label = normalized_difficulty_gain_label(difficulty_validation) if difficulty_validation else ""
        if label and label not in PASS_DIFFICULTY_GAIN_LABELS:
            counter[label] += 1
        elif not difficulty_validation:
            counter["missing_difficulty_gain_validation"] += 1
    return [
        reason
        for reason, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]


def _selection_record(
    record: Dict[str, Any],
    fallback_index: int,
    score: float,
    reasons: List[str],
    flow: Dict[str, Any],
    rejected_candidates: List[Dict[str, Any]],
    *,
    selected: bool,
    selected_for_exploration: bool,
) -> Dict[str, Any]:
    selected_record = _strip_candidate_fields(record)
    selected_id = candidate_id(record, fallback_index)
    selected_operator = candidate_operator(record)
    selected_record = _sync_operator_metadata(selected_record, selected_operator, question_evolved=bool(selected))
    difficulty_validation = difficulty_gain_validation(record)
    selected_record["candidate_selection"] = {
        "selected": selected,
        "selected_candidate_id": selected_id,
        "selected_operator": selected_operator,
        "selection_score": round(max(score, 0.0), 4),
        "selection_status": flow.get("selection_status"),
        "candidate_flow": flow.get("candidate_flow"),
        "selected_for_exploration": selected_for_exploration,
        "difficulty_gain_score": difficulty_validation.get("difficulty_gain_score"),
        "difficulty_gain_label": difficulty_validation.get("difficulty_gain_label"),
        "risk_tags": difficulty_validation.get("risk_tags", []),
        "light_factual_warning_count": len(light_factual_warnings(record)),
        "template_affordance_risk": template_affordance_risk(record, difficulty_validation) or None,
        "rubric_shortcut_risk": rubric_shortcut_risk(record, difficulty_validation) or None,
        "weak_probe_used": bool(
            isinstance(difficulty_validation.get("weak_probe"), dict)
            and difficulty_validation["weak_probe"].get("enabled")
        ),
        "selection_reason": "; ".join(reasons) if reasons else _clean_text(flow.get("reason")) or "selected",
        "rejected_candidates": rejected_candidates,
    }
    return selected_record


def _fallback_selection(
    records: Sequence[Dict[str, Any]],
    *,
    selection_status: str,
    selection_reason: str,
    rejected_candidates: List[Dict[str, Any]],
    recommended_next_action: str = "retry_with_backup_operator",
) -> Dict[str, Any]:
    selected = _strip_candidate_fields(_restore_original_passthrough(records[0]))
    selected["candidate_selection"] = {
        "selected": False,
        "selected_candidate_id": candidate_id(records[0], 1),
        "selected_operator": "",
        "selection_score": 0.0,
        "selection_status": selection_status,
        "candidate_flow": "pass_through_candidate",
        "selected_for_exploration": False,
        "recommended_next_action": recommended_next_action,
        "rejected_candidate_count": len(rejected_candidates),
        "main_reject_reasons": _main_reject_reasons(records),
        "selection_reason": selection_reason,
        "rejected_candidates": rejected_candidates,
    }
    return selected


def _select_group_with_budget(
    records: Sequence[Dict[str, Any]],
    *,
    allow_missing_difficulty_gain: bool = False,
    exploration_budget_remaining: int = MAX_EXPLORATION_CANDIDATES_PER_ROUND,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], int]:
    if not records:
        raise ValueError("candidate group is empty")

    if len(records) == 1 and records[0].get("question_evolved") is False:
        selected = _sync_operator_metadata(_strip_candidate_fields(records[0]), "", question_evolved=False)
        cid = candidate_id(records[0], 1)
        selected["candidate_selection"] = {
            "selected": True,
            "selected_candidate_id": cid,
            "selected_operator": "",
            "selection_status": "pass_through_original",
            "candidate_flow": "pass_through_candidate",
            "selected_for_exploration": False,
            "selection_reason": "pass-through sample does not enter candidate selection",
            "rejected_candidates": [],
        }
        return selected, [], 0

    scored: List[Tuple[int, float, List[str], int, Dict[str, Any], Dict[str, Any]]] = []
    flow_priority = {
        "main_chain_candidate": 3,
        "exploration_candidate": 2,
        "pass_through_candidate": 1,
        "hard_reject": 0,
    }
    for index, record in enumerate(records, start=1):
        flow = candidate_flow_info(record, allow_missing_difficulty_gain=allow_missing_difficulty_gain)
        score, reasons = score_candidate(record, allow_missing_difficulty_gain=allow_missing_difficulty_gain)
        scored.append((flow_priority.get(_clean_text(flow.get("candidate_flow")), 0), score, reasons, index, record, flow))
    scored.sort(
        key=lambda item: (
            item[0],
            item[1],
            _coerce_score(difficulty_gain_validation(item[4]).get("difficulty_gain_score")),
            -item[3],
        ),
        reverse=True,
    )

    invalid_cases: List[Dict[str, Any]] = []
    hard_rejects = [entry for entry in scored if entry[5].get("candidate_flow") == "hard_reject"]
    pass_throughs = [entry for entry in scored if entry[5].get("candidate_flow") == "pass_through_candidate"]
    main_candidates = [entry for entry in scored if entry[5].get("candidate_flow") == "main_chain_candidate"]
    exploration_candidates = [entry for entry in scored if entry[5].get("candidate_flow") == "exploration_candidate"]

    for _, _, reasons, index, record, flow in hard_rejects:
        invalid_cases.append(build_invalid_case(record, index, reason="; ".join(reasons) or _clean_text(flow.get("reason"))))

    if main_candidates:
        _, best_score, best_reasons, best_index, best_record, best_flow = main_candidates[0]
        rejected_candidates = [
            build_rejected_candidate(record, index, forced_reason="; ".join(reasons) or _clean_text(flow.get("reason")))
            for _, _, reasons, index, record, flow in scored
            if record is not best_record
        ]
        return (
            _selection_record(
                best_record,
                best_index,
                best_score,
                best_reasons,
                best_flow,
                rejected_candidates,
                selected=True,
                selected_for_exploration=False,
            ),
            invalid_cases,
            0,
        )

    if exploration_candidates:
        if exploration_budget_remaining > 0:
            _, best_score, best_reasons, best_index, best_record, best_flow = exploration_candidates[0]
            rejected_candidates = [
                build_rejected_candidate(record, index, forced_reason="; ".join(reasons) or _clean_text(flow.get("reason")))
                for _, _, reasons, index, record, flow in scored
                if record is not best_record
            ]
            return (
                _selection_record(
                    best_record,
                    best_index,
                    best_score,
                    best_reasons,
                    best_flow,
                    rejected_candidates,
                    selected=True,
                    selected_for_exploration=True,
                ),
                invalid_cases,
                1,
            )
        rejected_candidates = [
            build_rejected_candidate(
                record,
                index,
                forced_reason=(
                    "exploration budget exhausted"
                    if flow.get("candidate_flow") == "exploration_candidate"
                    else "; ".join(reasons) or _clean_text(flow.get("reason"))
                ),
            )
            for _, _, reasons, index, record, flow in scored
        ]
        return (
            _fallback_selection(
                records,
                selection_status="exploration_budget_exhausted",
                selection_reason="exploration candidates exist but round exploration budget is exhausted",
                rejected_candidates=rejected_candidates,
                recommended_next_action="continue_without_exploration",
            ),
            invalid_cases,
            0,
        )

    if pass_throughs:
        rejected_candidates = [
            build_rejected_candidate(record, index, forced_reason="; ".join(reasons) or _clean_text(flow.get("reason")))
            for _, _, reasons, index, record, flow in scored
        ]
        return (
            _fallback_selection(
                records,
                selection_status="not_selected_no_exploration_value",
                selection_reason="no main-chain candidate and no exploration-value candidate; pass through original",
                rejected_candidates=rejected_candidates,
                recommended_next_action="continue_without_penalty",
            ),
            invalid_cases,
            0,
        )

    rejected_candidates = [
        build_rejected_candidate(record, index, forced_reason="; ".join(reasons) or _clean_text(flow.get("reason")))
        for _, _, reasons, index, record, flow in scored
    ]
    selection_status = (
        "no_candidate_passed_difficulty_gain"
        if any(validation_result(record).get("passed") for _, _, _, _, record, _ in scored)
        else "no_candidate_passed_validation"
    )
    return (
        _fallback_selection(
            records,
            selection_status=selection_status,
            selection_reason=(
                "no candidate passed difficulty-gain validation; original sample pass-through"
                if selection_status == "no_candidate_passed_difficulty_gain"
                else "no candidate passed complexity validation; original sample pass-through"
            ),
            rejected_candidates=rejected_candidates,
        ),
        invalid_cases,
        0,
    )


def select_group(
    records: Sequence[Dict[str, Any]],
    *,
    allow_missing_difficulty_gain: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    selected, invalid_cases, _ = _select_group_with_budget(
        records,
        allow_missing_difficulty_gain=allow_missing_difficulty_gain,
        exploration_budget_remaining=MAX_EXPLORATION_CANDIDATES_PER_ROUND,
    )
    return selected, invalid_cases


def select_candidates(
    records: Sequence[Dict[str, Any]],
    *,
    allow_missing_difficulty_gain: bool = False,
    max_exploration_candidates_per_round: int = MAX_EXPLORATION_CANDIDATES_PER_ROUND,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    groups: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[candidate_group_id(record)].append(record)

    selected_records: List[Dict[str, Any]] = []
    invalid_cases: List[Dict[str, Any]] = []
    exploration_used = 0
    for group_records in groups.values():
        selected, group_invalid, group_exploration_used = _select_group_with_budget(
            group_records,
            allow_missing_difficulty_gain=allow_missing_difficulty_gain,
            exploration_budget_remaining=max(0, max_exploration_candidates_per_round - exploration_used),
        )
        exploration_used += group_exploration_used
        selected_records.append(selected)
        invalid_cases.extend(group_invalid)
    return selected_records, invalid_cases


def generate_report(
    input_records: Sequence[Dict[str, Any]],
    selected_records: Sequence[Dict[str, Any]],
    invalid_cases: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    template_distribution: Counter = Counter()
    rubric_distribution: Counter = Counter()
    light_factual_fatal_count = 0
    light_factual_warning_count = 0
    for record in input_records:
        if light_factual_fatal_errors(record):
            light_factual_fatal_count += 1
        if light_factual_warnings(record):
            light_factual_warning_count += 1
        template_distribution[template_affordance_risk(record) or "missing"] += 1
        rubric_distribution[rubric_shortcut_risk(record) or "missing"] += 1

    main_selected = 0
    exploration_selected = 0
    scoring_entries = 0
    for record in selected_records:
        selection = record.get("candidate_selection")
        selection = selection if isinstance(selection, dict) else {}
        if selection.get("selected") is True or selection.get("selected_for_exploration") is True:
            scoring_entries += 1
        if selection.get("candidate_flow") == "main_chain_candidate" and selection.get("selected") is True:
            main_selected += 1
        if selection.get("selected_for_exploration") is True:
            exploration_selected += 1

    selected_total = len(selected_records)
    return {
        "total_candidates": len(input_records),
        "selected_record_count": selected_total,
        "invalid_case_count": len(invalid_cases),
        "light_factual_fatal_count": light_factual_fatal_count,
        "light_factual_warning_count": light_factual_warning_count,
        "template_affordance_risk_distribution": dict(sorted(template_distribution.items())),
        "rubric_shortcut_risk_distribution": dict(sorted(rubric_distribution.items())),
        "main_chain_selection_rate": round(main_selected / selected_total, 4) if selected_total else 0.0,
        "exploration_selection_rate": round(exploration_selected / selected_total, 4) if selected_total else 0.0,
        "any_scoring_entry_rate": round(scoring_entries / selected_total, 4) if selected_total else 0.0,
    }


def write_json(data: Dict[str, Any], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Select one validated evolved-question candidate per sample.")
    parser.add_argument("--input", required=True, help="Input validated candidate JSON/JSONL path.")
    parser.add_argument("--output", required=True, help="Output selected evolved JSONL path.")
    parser.add_argument(
        "--invalid-output",
        default=os.path.join("memory", "invalid_generation_cases.jsonl"),
        help="Append rejected invalid candidate cases to this JSONL path.",
    )
    parser.add_argument(
        "--no-invalid-output",
        action="store_true",
        help="Do not write invalid generation cases.",
    )
    parser.add_argument(
        "--allow-missing-difficulty-gain",
        action="store_true",
        help="Legacy compatibility: allow candidates without difficulty_gain_validation.",
    )
    parser.add_argument(
        "--max-exploration-candidates-per-round",
        type=int,
        default=MAX_EXPLORATION_CANDIDATES_PER_ROUND,
        help="Maximum weak/manual/no-gain exploration candidates selected in one round.",
    )
    parser.add_argument("--report-output", default=None, help="Optional candidate-selection report JSON path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_json_or_jsonl(args.input)
    selected, invalid_cases = select_candidates(
        records,
        allow_missing_difficulty_gain=args.allow_missing_difficulty_gain,
        max_exploration_candidates_per_round=args.max_exploration_candidates_per_round,
    )
    write_jsonl(selected, args.output)
    if invalid_cases and not args.no_invalid_output:
        write_jsonl(invalid_cases, args.invalid_output, append=True)
    if args.report_output:
        write_json(generate_report(records, selected, invalid_cases), args.report_output)


if __name__ == "__main__":
    main()
