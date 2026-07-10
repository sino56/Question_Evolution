import argparse
import asyncio
import json
import os
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple

from local_api_config import get_config_list, get_config_value
from prompts.difficulty_gain_validation import build_difficulty_gain_prompt, build_weak_probe_judgment_prompt
from schema_validation import validate_records_against_schema


DEFAULT_VALIDATOR_MODEL = (
    os.getenv("DIFFICULTY_GAIN_MODEL")
    or os.getenv("PROFILE_MODEL")
    or os.getenv("EVOLVE_MODEL")
    or get_config_value("DIFFICULTY_GAIN_MODEL", "PROFILE_MODEL", "EVOLVE_MODEL", "QA_MODEL", "GPT_MODEL", default="gpt-5.4")
)
DEFAULT_VALIDATOR_BASE_URL = (
    os.getenv("DIFFICULTY_GAIN_BASE_URL")
    or os.getenv("PROFILE_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or get_config_value("DIFFICULTY_GAIN_BASE_URL", "PROFILE_BASE_URL", "EVOLVE_BASE_URL", "BASE_URL", "OPENAI_BASE_URL", default="")
)
DEFAULT_WEAK_ANSWER_MODEL = (
    os.getenv("WEAK_ANSWER_MODEL")
    or os.getenv("QWEN_MODEL")
    or get_config_value("WEAK_ANSWER_MODEL", "QWEN_MODEL", "GPT_MODEL", default="hjl_Qwen3.6-27B")
)
DEFAULT_WEAK_ANSWER_BASE_URL = (
    os.getenv("WEAK_ANSWER_BASE_URL")
    or os.getenv("QWEN_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or get_config_value("WEAK_ANSWER_BASE_URL", "QWEN_BASE_URL", "BASE_URL", "OPENAI_BASE_URL", default="")
)

MIN_GAIN_SCORE = 0.75
BORDERLINE_GAIN_SCORE = 0.65
MIN_NO_LEAKAGE_SCORE = 0.60
MIN_ANTI_CLARITY_TRAP_SCORE = 0.60
MIN_ANSWERABILITY_SCORE = 0.70
MIN_AXIS_CONSISTENCY_SCORE = 0.70
MIN_FORMAT_COMPLEXITY_SCORE = 0.50
MIN_COMPETITIVE_JUDGMENT_SCORE = 0.60

DIMENSION_FIELDS = (
    "axis_consistency_score",
    "no_leakage_score",
    "competitive_judgment_score",
    "anti_clarity_trap_score",
    "answerability_score",
    "format_complexity_score",
)
DIFFICULTY_GAIN_WEIGHTS = {
    "axis_consistency_score": 0.20,
    "no_leakage_score": 0.25,
    "competitive_judgment_score": 0.20,
    "anti_clarity_trap_score": 0.15,
    "answerability_score": 0.10,
    "format_complexity_score": 0.10,
}
VALID_LABELS = {
    "clear_gain",
    "probable_gain",
    "weak_gain",
    "no_gain",
    "leakage_or_simplification",
    "format_complexity_only",
    "axis_shift",
    "unanswerable_or_external",
    "needs_manual_review",
    "not_applicable",
}
PASS_LABELS = {"clear_gain", "probable_gain"}
HARD_REJECT_RISK_TAGS = {
    "external_fact_dependency",
    "answer_path_scaffolded",
    "conclusion_hint_revealed",
    "missing_premise_named",
}
HIGH_RISK_TAGS = HARD_REJECT_RISK_TAGS | {
    "format_difficulty_only",
    "axis_shift",
}
FORMAT_TERMS = ("表格", "复杂编号", "固定句式", "固定编号", "JSON", "yaml", "多层标签", "字数", "格式")
EXTERNAL_TERMS = ("查阅", "搜索", "检索", "外部资料", "自行查询", "司法解释", "证据规则", "未提供的信息")
SCAFFOLD_TERMS = ("先判断", "再判断", "最后说明", "分三步", "逐步判断", "依次判断")
LEAKAGE_TERMS = ("缺少排他性证据", "排他性不足", "闭环未形成", "不能直接认定", "关键缺口", "关键前提")
COMPETITIVE_TERMS = ("A", "B", "二选一", "两个候选", "哪一项", "哪一个", "哪一层", "哪一类", "比较", "相近", "支持哪一层")
WEAK_PROBE_POSITIVE_JUDGMENTS = {
    "candidate likely exposes target failure",
    "candidate is harder than original",
}
WEAK_PROBE_SOFT_RISK_TAGS = {
    "overclarified_prompt",
    "option_gap_too_obvious",
    "layer_boundary_exposed",
    "multi_axis_overload",
    "no_real_gain",
}


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


def write_json(data: Dict[str, Any], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def parse_api_keys(cli_keys: Optional[List[str]] = None) -> List[str]:
    if cli_keys:
        keys = [key.strip() for key in cli_keys if key and key.strip()]
        if keys:
            return keys
    raw = (
        os.getenv("DIFFICULTY_GAIN_API_KEYS")
        or os.getenv("PROFILE_API_KEYS")
        or os.getenv("EVOLVE_API_KEYS")
        or os.getenv("OPENAI_API_KEYS")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if keys:
        return keys
    return get_config_list(
        "DIFFICULTY_GAIN_API_KEYS",
        "PROFILE_API_KEYS",
        "EVOLVE_API_KEYS",
        "GPT_API_KEYS",
        "HIAPI_KEYS_BIG",
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY",
        "API_KEYS",
    )


def parse_weak_answer_api_keys(cli_keys: Optional[List[str]] = None) -> List[str]:
    if cli_keys:
        keys = [key.strip() for key in cli_keys if key and key.strip()]
        if keys:
            return keys
    raw = (
        os.getenv("WEAK_ANSWER_API_KEYS")
        or os.getenv("WEAK_ANSWER_API_KEY")
        or os.getenv("QWEN_API_KEYS")
        or os.getenv("QWEN_API_KEY")
        or os.getenv("OPENAI_API_KEYS")
        or os.getenv("OPENAI_API_KEY")
        or ""
    )
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if keys:
        return keys
    return get_config_list(
        "WEAK_ANSWER_API_KEYS",
        "WEAK_ANSWER_API_KEY",
        "QWEN_API_KEYS",
        "QWEN_API_KEY",
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY",
        "API_KEYS",
    )


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _coerce_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "是"}:
            return True
        if text in {"false", "no", "0", "否"}:
            return False
    return None


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


def _unique_strings(values: Any) -> List[str]:
    if isinstance(values, str):
        candidates: Sequence[Any] = [values]
    elif isinstance(values, Sequence):
        candidates = values
    else:
        return []

    result: List[str] = []
    for value in candidates:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _extract_json_object(response_text: str) -> Dict[str, Any]:
    text = response_text.strip()
    candidates = [text]
    code_match = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text, re.IGNORECASE)
    if code_match:
        candidates.insert(0, code_match.group(1).strip())
    object_match = re.search(r"\{[\s\S]*\}", text)
    if object_match:
        candidates.append(object_match.group(0))
    last_error: Optional[Exception] = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed
        except Exception as exc:
            last_error = exc
    raise ValueError(f"无法解析 difficulty gain validation JSON: {last_error}")


def _record_key(item: Dict[str, Any], index: int) -> str:
    for field in ("candidate_id", "sample_id", "index"):
        value = item.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return f"record_{index}"


def _metadata(item: Dict[str, Any]) -> Dict[str, Any]:
    meta_info = item.get("meta_info")
    if not isinstance(meta_info, dict):
        return {}
    metadata = meta_info.get("question_evolution_metadata")
    return metadata if isinstance(metadata, dict) else {}


def candidate_operator(item: Dict[str, Any]) -> str:
    for field in ("candidate_operator", "operator_used"):
        value = _clean_text(item.get(field))
        if value:
            return value
    return _clean_text(_metadata(item).get("operator_used"))


def compute_difficulty_gain_score(dimension_scores: Dict[str, Any]) -> float:
    score = 0.0
    for field, weight in DIFFICULTY_GAIN_WEIGHTS.items():
        score += weight * _coerce_score(dimension_scores.get(field))
    return round(score, 4)


def hard_reject_reasons(
    validation: Dict[str, Any],
    *,
    min_no_leakage_score: float = MIN_NO_LEAKAGE_SCORE,
    min_anti_clarity_trap_score: float = MIN_ANTI_CLARITY_TRAP_SCORE,
    min_answerability_score: float = MIN_ANSWERABILITY_SCORE,
    min_axis_consistency_score: float = MIN_AXIS_CONSISTENCY_SCORE,
    min_format_complexity_score: float = MIN_FORMAT_COMPLEXITY_SCORE,
    min_competitive_judgment_score: float = MIN_COMPETITIVE_JUDGMENT_SCORE,
) -> List[str]:
    reasons: List[str] = []
    if _coerce_score(validation.get("no_leakage_score")) < min_no_leakage_score:
        reasons.append("no_leakage_score 低于硬阈值")
    if _coerce_score(validation.get("anti_clarity_trap_score")) < min_anti_clarity_trap_score:
        reasons.append("anti_clarity_trap_score 低于硬阈值")
    if _coerce_score(validation.get("answerability_score")) < min_answerability_score:
        reasons.append("answerability_score 低于硬阈值")
    if _coerce_score(validation.get("axis_consistency_score")) < min_axis_consistency_score:
        reasons.append("axis_consistency_score 低于硬阈值")
    if _coerce_score(validation.get("format_complexity_score")) < min_format_complexity_score:
        reasons.append("format_complexity_score 低于硬阈值")
    if _coerce_score(validation.get("competitive_judgment_score")) < min_competitive_judgment_score:
        reasons.append("competitive_judgment_score 低于最低竞争判断阈值")

    risk_tags = set(_unique_strings(validation.get("risk_tags")))
    for tag in sorted(risk_tags & HARD_REJECT_RISK_TAGS):
        reasons.append(f"命中高危风险标签: {tag}")
    return reasons


def has_hard_reject(validation: Dict[str, Any]) -> bool:
    return bool(hard_reject_reasons(validation))


def _risk_level_from_score(score: float) -> str:
    if score < 0.60:
        return "high"
    if score < 0.75:
        return "medium"
    return "low"


def _normalize_dimension_scores(raw: Dict[str, Any]) -> Dict[str, float]:
    nested = raw.get("dimension_scores")
    source = nested if isinstance(nested, dict) else raw
    return {field: _coerce_score(source.get(field)) for field in DIMENSION_FIELDS}


def _label_from_rejection(validation: Dict[str, Any], raw_label: str) -> str:
    tags = set(_unique_strings(validation.get("risk_tags")))
    if "external_fact_dependency" in tags or validation.get("answerability_score", 1.0) < MIN_ANSWERABILITY_SCORE:
        return "unanswerable_or_external"
    if "axis_shift" in tags or validation.get("axis_consistency_score", 1.0) < MIN_AXIS_CONSISTENCY_SCORE:
        return "axis_shift"
    if "format_difficulty_only" in tags or validation.get("format_complexity_score", 1.0) < MIN_FORMAT_COMPLEXITY_SCORE:
        return "format_complexity_only"
    if tags & {"missing_premise_named", "conclusion_hint_revealed", "answer_path_scaffolded", "overclarified_prompt"}:
        return "leakage_or_simplification"
    if "no_real_gain" in tags or validation.get("competitive_judgment_score", 1.0) < MIN_COMPETITIVE_JUDGMENT_SCORE:
        return "no_gain"
    if raw_label in VALID_LABELS and raw_label != "not_applicable":
        return raw_label
    return "no_gain"


def normalize_difficulty_gain_result(
    raw: Optional[Dict[str, Any]],
    *,
    validator_model: str = "",
    raw_response: str = "",
    min_gain_score: float = MIN_GAIN_SCORE,
    borderline_gain_score: float = BORDERLINE_GAIN_SCORE,
    allow_borderline: bool = False,
    min_no_leakage_score: float = MIN_NO_LEAKAGE_SCORE,
    min_anti_clarity_trap_score: float = MIN_ANTI_CLARITY_TRAP_SCORE,
    min_answerability_score: float = MIN_ANSWERABILITY_SCORE,
    min_axis_consistency_score: float = MIN_AXIS_CONSISTENCY_SCORE,
    min_format_complexity_score: float = MIN_FORMAT_COMPLEXITY_SCORE,
    min_competitive_judgment_score: float = MIN_COMPETITIVE_JUDGMENT_SCORE,
) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    dimension_scores = _normalize_dimension_scores(raw)
    risk_tags = _unique_strings(raw.get("risk_tags"))
    score = compute_difficulty_gain_score(dimension_scores)
    raw_label = _clean_text(raw.get("difficulty_gain_label"))
    if raw_label not in VALID_LABELS:
        raw_label = ""

    result: Dict[str, Any] = {
        "passed": False,
        "difficulty_gain_label": raw_label or "needs_manual_review",
        "difficulty_gain_score": score,
        "dimension_scores": dimension_scores,
        "risk_tags": risk_tags,
        "leakage_risk": _risk_level_from_score(dimension_scores["no_leakage_score"]),
        "clarity_trap_risk": _risk_level_from_score(dimension_scores["anti_clarity_trap_score"]),
        "expected_qwen_failure_match": _coerce_bool(raw.get("expected_qwen_failure_match")) is True,
        "reject_reason": _clean_text(raw.get("reject_reason")),
        "recommended_action": _clean_text(raw.get("recommended_action")) or "reject_candidate",
        "validator_model": validator_model,
        "raw_response": raw_response,
    }
    for field, value in dimension_scores.items():
        result[field] = value
    for field in ("leakage_analysis", "clarity_trap_analysis", "competitive_judgment_analysis", "expected_failure_point"):
        value = _clean_text(raw.get(field))
        if value:
            result[field] = value

    reasons = hard_reject_reasons(
        result,
        min_no_leakage_score=min_no_leakage_score,
        min_anti_clarity_trap_score=min_anti_clarity_trap_score,
        min_answerability_score=min_answerability_score,
        min_axis_consistency_score=min_axis_consistency_score,
        min_format_complexity_score=min_format_complexity_score,
        min_competitive_judgment_score=min_competitive_judgment_score,
    )
    if raw_label in {"weak_gain", "no_gain", "leakage_or_simplification", "format_complexity_only", "axis_shift", "unanswerable_or_external", "needs_manual_review"}:
        reasons.append(f"validator label rejects candidate: {raw_label}")

    if reasons:
        result["difficulty_gain_label"] = _label_from_rejection(result, raw_label)
        result["passed"] = False
        if not result["reject_reason"]:
            result["reject_reason"] = "；".join(reasons)
        result["recommended_action"] = "reject_candidate" if raw_label != "needs_manual_review" else "needs_manual_review"
        return result

    if score >= min_gain_score:
        result["passed"] = True
        result["difficulty_gain_label"] = raw_label if raw_label in PASS_LABELS else ("clear_gain" if score >= 0.82 else "probable_gain")
        result["recommended_action"] = "admit_candidate"
        result["reject_reason"] = ""
        return result

    if allow_borderline and score >= borderline_gain_score:
        result["passed"] = True
        result["difficulty_gain_label"] = raw_label if raw_label in PASS_LABELS else "probable_gain"
        result["recommended_action"] = "admit_if_no_better_candidate"
        result["reject_reason"] = ""
        return result

    if score >= borderline_gain_score:
        result["difficulty_gain_label"] = raw_label if raw_label in VALID_LABELS and raw_label != "not_applicable" else "weak_gain"
    else:
        result["difficulty_gain_label"] = raw_label if raw_label in VALID_LABELS and raw_label != "not_applicable" else "no_gain"
    result["passed"] = False
    if not result["reject_reason"]:
        result["reject_reason"] = f"difficulty_gain_score {score:.2f} 低于通过阈值 {min_gain_score:.2f}"
    result["recommended_action"] = "reject_candidate"
    return result


def build_not_applicable_validation(validator_model: str = "") -> Dict[str, Any]:
    dimension_scores = {field: 1.0 for field in DIMENSION_FIELDS}
    result: Dict[str, Any] = {
        "passed": True,
        "difficulty_gain_label": "not_applicable",
        "difficulty_gain_score": 1.0,
        "dimension_scores": dimension_scores,
        "risk_tags": [],
        "leakage_risk": "low",
        "clarity_trap_risk": "low",
        "expected_qwen_failure_match": False,
        "reject_reason": "",
        "recommended_action": "pass_through_original",
        "validator_model": validator_model,
        "raw_response": "",
    }
    for field in DIMENSION_FIELDS:
        result[field] = 1.0
    return result


def build_validator_error_result(error: Exception, validator_model: str = "") -> Dict[str, Any]:
    raw = {
        "difficulty_gain_label": "needs_manual_review",
        "dimension_scores": {field: 0.0 for field in DIMENSION_FIELDS},
        "risk_tags": ["validator_error"],
        "reject_reason": f"difficulty gain validator failed: {error}",
        "recommended_action": "needs_manual_review",
    }
    return normalize_difficulty_gain_result(raw, validator_model=validator_model, raw_response=str(error))


def build_failure_memory_candidate(item: Dict[str, Any], validation: Dict[str, Any]) -> Dict[str, Any]:
    label = _clean_text(validation.get("difficulty_gain_label")) or "difficulty_gain_failed"
    strategy = "switch_operator_family" if label in {"leakage_or_simplification", "axis_shift"} else "retry_with_backup_operator"
    return {
        "operator_id": candidate_operator(item),
        "failure_type": label,
        "risk_tags": _unique_strings(validation.get("risk_tags")),
        "reject_reason": _clean_text(validation.get("reject_reason")),
        "recommended_retry_strategy": strategy,
    }


def attach_difficulty_gain_validation(item: Dict[str, Any], validation: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    result["difficulty_gain_validation"] = validation
    if validation.get("passed") is False:
        result["failure_memory_candidate"] = build_failure_memory_candidate(item, validation)
    return result


def build_rule_only_raw_validation(item: Dict[str, Any]) -> Dict[str, Any]:
    prompt = _clean_text(item.get("prompt"))
    profile = item.get("sample_profile") if isinstance(item.get("sample_profile"), dict) else {}
    diagnosis = item.get("overscore_diagnosis") if isinstance(item.get("overscore_diagnosis"), dict) else {}
    risk_tags: List[str] = []
    scores = {
        "axis_consistency_score": 0.82,
        "no_leakage_score": 0.82,
        "competitive_judgment_score": 0.68,
        "anti_clarity_trap_score": 0.78,
        "answerability_score": 0.85,
        "format_complexity_score": 0.88,
    }

    if any(term in prompt for term in LEAKAGE_TERMS):
        risk_tags.extend(["missing_premise_named", "overclarified_prompt"])
        scores["no_leakage_score"] = 0.35
        scores["anti_clarity_trap_score"] = 0.42
    if any(term in prompt for term in SCAFFOLD_TERMS):
        risk_tags.append("answer_path_scaffolded")
        scores["anti_clarity_trap_score"] = min(scores["anti_clarity_trap_score"], 0.35)
        scores["no_leakage_score"] = min(scores["no_leakage_score"], 0.55)
    if any(term in prompt for term in FORMAT_TERMS):
        risk_tags.append("format_difficulty_only")
        scores["format_complexity_score"] = 0.25
    if any(term in prompt for term in EXTERNAL_TERMS):
        risk_tags.append("external_fact_dependency")
        scores["answerability_score"] = 0.35
    if "法律条文" in prompt or "法条" in prompt:
        risk_tags.append("axis_shift")
        scores["axis_consistency_score"] = 0.35
    if any(term in prompt for term in COMPETITIVE_TERMS):
        scores["competitive_judgment_score"] = 0.82
    if not risk_tags and scores["competitive_judgment_score"] < 0.75:
        risk_tags.append("no_real_gain")

    target_failure = _clean_text(diagnosis.get("target_failure_mode") or diagnosis.get("candidate_overscore_cause"))
    core_capability = _clean_text(profile.get("core_capability"))
    if core_capability and core_capability in prompt:
        scores["axis_consistency_score"] = max(scores["axis_consistency_score"], 0.9)

    label = "probable_gain" if not risk_tags and scores["competitive_judgment_score"] >= 0.75 else "no_gain"
    if set(risk_tags) & {"missing_premise_named", "answer_path_scaffolded"}:
        label = "leakage_or_simplification"
    elif "format_difficulty_only" in risk_tags:
        label = "format_complexity_only"
    elif "external_fact_dependency" in risk_tags:
        label = "unanswerable_or_external"
    elif "axis_shift" in risk_tags:
        label = "axis_shift"

    return {
        "difficulty_gain_label": label,
        "dimension_scores": scores,
        "risk_tags": risk_tags,
        "leakage_analysis": "rule-only heuristic",
        "clarity_trap_analysis": "rule-only heuristic",
        "competitive_judgment_analysis": "rule-only heuristic",
        "expected_qwen_failure_match": bool(target_failure),
        "expected_failure_point": target_failure,
        "reject_reason": "",
        "recommended_action": "admit_candidate" if label in PASS_LABELS else "reject_candidate",
    }


def build_weak_answer_prompt(item: Dict[str, Any]) -> str:
    prompt = _clean_text(item.get("prompt"))
    return (
        "请直接回答下面的问题。保持回答聚焦题干事实，不要解释你是模型，也不要引用题外资料。\n\n"
        f"{prompt}"
    )


def normalize_weak_probe_result(
    raw: Optional[Dict[str, Any]],
    *,
    probe_answer: str,
    mode: str = "light",
    validator_model: str = "",
    raw_response: str = "",
) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    judgment = _clean_text(raw.get("probe_judgment"))
    target_match = _coerce_bool(raw.get("target_failure_match"))
    failure_detected = _coerce_bool(raw.get("probe_failure_detected"))
    if target_match is None:
        target_match = judgment in WEAK_PROBE_POSITIVE_JUDGMENTS
    if failure_detected is None:
        failure_detected = target_match

    result: Dict[str, Any] = {
        "enabled": True,
        "mode": mode,
        "probe_answer": probe_answer,
        "probe_failure_detected": bool(failure_detected),
        "target_failure_match": bool(target_match),
        "probe_judgment": judgment or (
            "candidate likely exposes target failure" if target_match else "weak probe did not expose target failure"
        ),
        "validator_model": validator_model,
        "raw_response": raw_response,
    }
    reason = _clean_text(raw.get("probe_reason") or raw.get("reason"))
    if reason:
        result["probe_reason"] = reason
    for field in ("probe_score_rate", "baseline_stable_score", "estimated_score_drop"):
        if field in raw:
            result[field] = _coerce_score(raw.get(field))
    return result


def build_weak_probe_error_result(error: Exception, *, mode: str, validator_model: str = "") -> Dict[str, Any]:
    return {
        "enabled": True,
        "mode": mode,
        "probe_answer": "",
        "probe_failure_detected": False,
        "target_failure_match": False,
        "probe_judgment": "weak probe failed",
        "probe_reason": f"weak probe failed: {error}",
        "validator_model": validator_model,
        "raw_response": str(error),
    }


def _weak_probe_missed(validation: Dict[str, Any]) -> bool:
    weak_probe = validation.get("weak_probe")
    if not isinstance(weak_probe, dict) or weak_probe.get("enabled") is not True:
        return False
    if weak_probe.get("target_failure_match") is True:
        return False
    return _clean_text(weak_probe.get("probe_judgment")) not in WEAK_PROBE_POSITIVE_JUDGMENTS


def _has_soft_leakage_or_gain_risk(validation: Dict[str, Any]) -> bool:
    tags = set(_unique_strings(validation.get("risk_tags")))
    if tags & WEAK_PROBE_SOFT_RISK_TAGS:
        return True
    return (
        _clean_text(validation.get("leakage_risk")) == "medium"
        or _clean_text(validation.get("clarity_trap_risk")) == "medium"
    )


def apply_weak_probe_result(validation: Dict[str, Any], weak_probe: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(validation)
    result["weak_probe"] = weak_probe
    if result.get("passed") is not True:
        return result
    if not _weak_probe_missed(result):
        return result

    risk_tags = _unique_strings(result.get("risk_tags"))
    if "weak_probe_no_failure" not in risk_tags:
        risk_tags.append("weak_probe_no_failure")
    result["risk_tags"] = risk_tags

    if _has_soft_leakage_or_gain_risk(result):
        result["passed"] = False
        result["difficulty_gain_label"] = (
            "leakage_or_simplification"
            if set(risk_tags) & {"overclarified_prompt", "option_gap_too_obvious", "layer_boundary_exposed"}
            else "no_gain"
        )
        result["recommended_action"] = "reject_candidate"
        result["reject_reason"] = (
            result.get("reject_reason")
            or "weak probe 未命中目标 failure，且候选存在轻微信息泄漏或无真实收益风险。"
        )
    else:
        result["recommended_action"] = "admit_low_priority"
    return result


def build_rule_only_weak_probe(record: Dict[str, Any], validation: Dict[str, Any], mode: str = "light") -> Dict[str, Any]:
    tags = set(_unique_strings(validation.get("risk_tags")))
    target_match = validation.get("passed") is True and "no_real_gain" not in tags
    return {
        "enabled": True,
        "mode": mode,
        "probe_answer": "rule-only weak probe answer placeholder",
        "probe_failure_detected": target_match,
        "target_failure_match": target_match,
        "probe_judgment": (
            "candidate likely exposes target failure"
            if target_match
            else "weak probe did not expose target failure"
        ),
        "probe_reason": "rule-only heuristic",
        "validator_model": "rule-only",
        "raw_response": "",
    }


class DifficultyGainValidationClient:
    def __init__(self, base_url: str, api_keys: List[str], model: str, request_timeout: float = 120.0):
        if not api_keys:
            raise ValueError("启用 difficulty gain validator 时必须提供 DIFFICULTY_GAIN_API_KEYS/OPENAI_API_KEY 或 --api-key")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("Missing dependency: install openai to enable difficulty gain validation.") from exc
        self.model = model
        self.clients = []
        for key in api_keys:
            kwargs: Dict[str, Any] = {"api_key": key, "timeout": request_timeout}
            if base_url:
                kwargs["base_url"] = base_url
            self.clients.append(AsyncOpenAI(**kwargs))
        self.current = 0

    async def close(self) -> None:
        for client in self.clients:
            await client.close()

    async def validate(self, item: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        prompt = build_difficulty_gain_prompt(item)
        last_error: Optional[Exception] = None
        for offset in range(len(self.clients)):
            index = (self.current + offset) % len(self.clients)
            try:
                response = await self.clients[index].chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "Return strict JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                )
                content = response.choices[0].message.content or ""
                self.current = index
                return _extract_json_object(content), content
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"difficulty gain validation failed: {last_error}")

    async def judge_weak_probe(self, item: Dict[str, Any], probe_answer: str) -> Tuple[Dict[str, Any], str]:
        prompt = build_weak_probe_judgment_prompt(item, probe_answer)
        last_error: Optional[Exception] = None
        for offset in range(len(self.clients)):
            index = (self.current + offset) % len(self.clients)
            try:
                response = await self.clients[index].chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": "Return strict JSON only."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0,
                )
                content = response.choices[0].message.content or ""
                self.current = index
                return _extract_json_object(content), content
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"weak probe judgment failed: {last_error}")


class WeakProbeAnswerClient:
    def __init__(self, base_url: str, api_keys: List[str], model: str, request_timeout: float = 120.0):
        if not api_keys:
            raise ValueError("启用 weak probe 时必须提供 WEAK_ANSWER_API_KEYS/WEAK_ANSWER_API_KEY/QWEN_API_KEY 或 --weak-answer-api-key")
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("Missing dependency: install openai to enable weak probe.") from exc
        self.model = model
        self.clients = []
        for key in api_keys:
            kwargs: Dict[str, Any] = {"api_key": key, "timeout": request_timeout}
            if base_url:
                kwargs["base_url"] = base_url
            self.clients.append(AsyncOpenAI(**kwargs))
        self.current = 0

    async def close(self) -> None:
        for client in self.clients:
            await client.close()

    async def answer(self, item: Dict[str, Any]) -> str:
        prompt = build_weak_answer_prompt(item)
        last_error: Optional[Exception] = None
        for offset in range(len(self.clients)):
            index = (self.current + offset) % len(self.clients)
            try:
                response = await self.clients[index].chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.2,
                )
                self.current = index
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
        raise RuntimeError(f"weak probe answer failed: {last_error}")


def normalize_record_validation(
    record: Dict[str, Any],
    raw: Optional[Dict[str, Any]],
    *,
    validator_model: str,
    raw_response: str = "",
    min_gain_score: float = MIN_GAIN_SCORE,
    borderline_gain_score: float = BORDERLINE_GAIN_SCORE,
    allow_borderline: bool = False,
    min_competitive_judgment_score: float = MIN_COMPETITIVE_JUDGMENT_SCORE,
) -> Dict[str, Any]:
    if record.get("question_evolved") is False:
        return build_not_applicable_validation(validator_model)
    return normalize_difficulty_gain_result(
        raw,
        validator_model=validator_model,
        raw_response=raw_response,
        min_gain_score=min_gain_score,
        borderline_gain_score=borderline_gain_score,
        allow_borderline=allow_borderline,
        min_competitive_judgment_score=min_competitive_judgment_score,
    )


def validate_records_rule_only(
    records: Sequence[Dict[str, Any]],
    *,
    validator_model: str = "rule-only",
    min_gain_score: float = MIN_GAIN_SCORE,
    borderline_gain_score: float = BORDERLINE_GAIN_SCORE,
    allow_borderline: bool = False,
    min_competitive_judgment_score: float = MIN_COMPETITIVE_JUDGMENT_SCORE,
    enable_weak_probe: bool = False,
    weak_probe_mode: str = "light",
) -> List[Dict[str, Any]]:
    validated: List[Dict[str, Any]] = []
    for record in records:
        raw = {} if record.get("question_evolved") is False else build_rule_only_raw_validation(record)
        result = normalize_record_validation(
            record,
            raw,
            validator_model=validator_model,
            raw_response=json.dumps(raw, ensure_ascii=False),
            min_gain_score=min_gain_score,
            borderline_gain_score=borderline_gain_score,
            allow_borderline=allow_borderline,
            min_competitive_judgment_score=min_competitive_judgment_score,
        )
        if enable_weak_probe and record.get("question_evolved") is not False:
            result = apply_weak_probe_result(result, build_rule_only_weak_probe(record, result, mode=weak_probe_mode))
        validated.append(attach_difficulty_gain_validation(record, result))
    return validated


async def validate_records_with_llm(
    records: Sequence[Dict[str, Any]],
    *,
    base_url: str,
    api_keys: List[str],
    model: str,
    concurrency: int = 5,
    request_timeout: float = 120.0,
    min_gain_score: float = MIN_GAIN_SCORE,
    borderline_gain_score: float = BORDERLINE_GAIN_SCORE,
    allow_borderline: bool = False,
    min_competitive_judgment_score: float = MIN_COMPETITIVE_JUDGMENT_SCORE,
    enable_weak_probe: bool = False,
    weak_probe_mode: str = "light",
    weak_answer_base_url: str = "",
    weak_answer_api_keys: Optional[List[str]] = None,
    weak_answer_model: str = DEFAULT_WEAK_ANSWER_MODEL,
) -> List[Dict[str, Any]]:
    client = DifficultyGainValidationClient(
        base_url=base_url,
        api_keys=api_keys,
        model=model,
        request_timeout=request_timeout,
    )
    weak_client: Optional[WeakProbeAnswerClient] = None
    if enable_weak_probe:
        weak_client = WeakProbeAnswerClient(
            base_url=weak_answer_base_url,
            api_keys=weak_answer_api_keys or [],
            model=weak_answer_model,
            request_timeout=request_timeout,
        )
    semaphore = asyncio.Semaphore(max(1, concurrency))
    results: List[Optional[Dict[str, Any]]] = [None] * len(records)

    async def run_one(index: int, record: Dict[str, Any]) -> None:
        async with semaphore:
            if record.get("question_evolved") is False:
                validation = build_not_applicable_validation(model)
            else:
                try:
                    raw, raw_response = await client.validate(record)
                    validation = normalize_record_validation(
                        record,
                        raw,
                        validator_model=model,
                        raw_response=raw_response,
                        min_gain_score=min_gain_score,
                        borderline_gain_score=borderline_gain_score,
                        allow_borderline=allow_borderline,
                        min_competitive_judgment_score=min_competitive_judgment_score,
                    )
                    if enable_weak_probe and weak_client is not None and weak_probe_mode == "light":
                        try:
                            probe_answer = await weak_client.answer(record)
                            probe_raw, probe_raw_response = await client.judge_weak_probe(record, probe_answer)
                            weak_probe = normalize_weak_probe_result(
                                probe_raw,
                                probe_answer=probe_answer,
                                mode=weak_probe_mode,
                                validator_model=model,
                                raw_response=probe_raw_response,
                            )
                        except Exception as probe_exc:
                            weak_probe = build_weak_probe_error_result(
                                probe_exc,
                                mode=weak_probe_mode,
                                validator_model=model,
                            )
                        validation = apply_weak_probe_result(validation, weak_probe)
                except Exception as exc:
                    validation = build_validator_error_result(exc, validator_model=model)
            results[index] = attach_difficulty_gain_validation(record, validation)

    try:
        await asyncio.gather(*(run_one(index, record) for index, record in enumerate(records)))
    finally:
        await client.close()
        if weak_client is not None:
            await weak_client.close()

    return [record for record in results if record is not None]


def generate_report(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    label_distribution: Counter = Counter()
    risk_distribution: Counter = Counter()
    operator_stats: DefaultDict[str, Dict[str, Any]] = defaultdict(
        lambda: {"total": 0, "passed": 0, "failure_reasons": Counter()}
    )
    dimension_totals = {field: 0.0 for field in DIMENSION_FIELDS}
    dimension_counts = {field: 0 for field in DIMENSION_FIELDS}

    total_candidates = 0
    passed_count = 0
    for record in records:
        validation = record.get("difficulty_gain_validation")
        validation = validation if isinstance(validation, dict) else {}
        if validation.get("difficulty_gain_label") == "not_applicable":
            continue
        total_candidates += 1
        label_distribution[_clean_text(validation.get("difficulty_gain_label")) or "missing"] += 1
        for tag in _unique_strings(validation.get("risk_tags")):
            risk_distribution[tag] += 1
        operator = candidate_operator(record) or "unknown"
        operator_stats[operator]["total"] += 1
        if validation.get("passed") is True:
            operator_stats[operator]["passed"] += 1
            passed_count += 1
        else:
            label = _clean_text(validation.get("difficulty_gain_label")) or "missing"
            operator_stats[operator]["failure_reasons"][label] += 1
            for tag in _unique_strings(validation.get("risk_tags")):
                operator_stats[operator]["failure_reasons"][tag] += 1
        for field in DIMENSION_FIELDS:
            if field in validation:
                dimension_totals[field] += _coerce_score(validation.get(field))
                dimension_counts[field] += 1

    operator_pass_rate = {}
    for operator, stats in sorted(operator_stats.items()):
        total_for_operator = stats["total"]
        passed_for_operator = stats["passed"]
        operator_pass_rate[operator] = {
            "total": total_for_operator,
            "passed": passed_for_operator,
            "pass_rate": round(passed_for_operator / total_for_operator, 4) if total_for_operator else 0.0,
            "main_failure_reasons": dict(
                sorted(
                    stats["failure_reasons"].items(),
                    key=lambda item: (-item[1], item[0]),
                )[:5]
            ),
        }

    average_dimension_scores = {
        field: round(dimension_totals[field] / dimension_counts[field], 4) if dimension_counts[field] else 0.0
        for field in DIMENSION_FIELDS
    }
    return {
        "total_candidates": total_candidates,
        "passed_count": passed_count,
        "failed_count": total_candidates - passed_count,
        "pass_rate": round(passed_count / total_candidates, 4) if total_candidates else 0.0,
        "difficulty_gain_label_distribution": dict(sorted(label_distribution.items())),
        "risk_tag_distribution": dict(sorted(risk_distribution.items())),
        "operator_pass_rate": operator_pass_rate,
        "average_dimension_scores": average_dimension_scores,
    }


def default_report_output(output_path: str) -> str:
    path = Path(output_path)
    if path.suffix:
        return str(path.with_name(f"{path.stem}.difficulty_report.json"))
    return f"{output_path}.difficulty_report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate evolved-question candidates for real difficulty gain before selection.")
    parser.add_argument("--input", required=True, help="Input validated candidate JSON/JSONL path.")
    parser.add_argument("--output", required=True, help="Output difficulty-validated JSONL path.")
    parser.add_argument("--report-output", default=None, help="Output difficulty gain report JSON path.")
    parser.add_argument("--model", default=DEFAULT_VALIDATOR_MODEL, help="Difficulty gain validator model.")
    parser.add_argument("--base-url", default=DEFAULT_VALIDATOR_BASE_URL, help="OpenAI-compatible validator base URL.")
    parser.add_argument("--api-key", action="append", default=None, help="Validator API key; can be provided multiple times.")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent validator requests.")
    parser.add_argument("--request-timeout", type=float, default=120.0, help="Validator request timeout seconds.")
    parser.add_argument("--min-gain-score", type=float, default=MIN_GAIN_SCORE, help="Minimum aggregate difficulty gain score.")
    parser.add_argument("--borderline-gain-score", type=float, default=BORDERLINE_GAIN_SCORE, help="Borderline aggregate score.")
    parser.add_argument(
        "--min-competitive-judgment-score",
        type=float,
        default=MIN_COMPETITIVE_JUDGMENT_SCORE,
        help="Minimum competitive judgment score; below this the candidate has no real gain.",
    )
    parser.add_argument("--allow-borderline", action="store_true", help="Allow borderline candidates when no hard reject exists.")
    parser.add_argument("--enable-weak-probe", action="store_true", help="Run one weak-model light probe before candidate selection.")
    parser.add_argument("--weak-probe-mode", choices=["light"], default="light", help="Weak probe mode. First version supports light mode.")
    parser.add_argument("--weak-answer-model", default=DEFAULT_WEAK_ANSWER_MODEL, help="Weak model used to answer candidate prompts.")
    parser.add_argument("--weak-answer-base-url", default=DEFAULT_WEAK_ANSWER_BASE_URL, help="OpenAI-compatible weak-answer base URL.")
    parser.add_argument("--weak-answer-api-key", action="append", default=None, help="Weak-answer API key; can be provided multiple times.")
    parser.add_argument("--rule-only", action="store_true", help="Use local heuristics instead of calling an LLM validator. Intended for offline smoke tests.")
    parser.add_argument("--validate-schema", action="store_true", help="Validate input/output records against local JSON schemas.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not 0 <= args.borderline_gain_score <= args.min_gain_score <= 1:
        raise ValueError("score thresholds must satisfy 0 <= borderline <= min <= 1")
    if not 0 <= args.min_competitive_judgment_score <= 1:
        raise ValueError("min competitive judgment score must be in [0, 1]")
    records = load_json_or_jsonl(args.input)
    if args.validate_schema:
        schema_errors = validate_records_against_schema(records, Path("schemas") / "pipeline_record.schema.json")
        if schema_errors:
            raise ValueError(f"input schema validation failed: {schema_errors[0]}")

    if args.rule_only:
        validated = validate_records_rule_only(
            records,
            min_gain_score=args.min_gain_score,
            borderline_gain_score=args.borderline_gain_score,
            allow_borderline=args.allow_borderline,
            min_competitive_judgment_score=args.min_competitive_judgment_score,
            enable_weak_probe=args.enable_weak_probe,
            weak_probe_mode=args.weak_probe_mode,
        )
    else:
        validated = asyncio.run(
            validate_records_with_llm(
                records,
                base_url=args.base_url or DEFAULT_VALIDATOR_BASE_URL,
                api_keys=parse_api_keys(args.api_key),
                model=args.model or DEFAULT_VALIDATOR_MODEL,
                concurrency=args.concurrency,
                request_timeout=args.request_timeout,
                min_gain_score=args.min_gain_score,
                borderline_gain_score=args.borderline_gain_score,
                allow_borderline=args.allow_borderline,
                min_competitive_judgment_score=args.min_competitive_judgment_score,
                enable_weak_probe=args.enable_weak_probe,
                weak_probe_mode=args.weak_probe_mode,
                weak_answer_base_url=args.weak_answer_base_url or DEFAULT_WEAK_ANSWER_BASE_URL,
                weak_answer_api_keys=parse_weak_answer_api_keys(args.weak_answer_api_key),
                weak_answer_model=args.weak_answer_model or DEFAULT_WEAK_ANSWER_MODEL,
            )
        )

    if args.validate_schema:
        schema_errors = validate_records_against_schema(validated, Path("schemas") / "pipeline_record.schema.json")
        if schema_errors:
            raise ValueError(f"output schema validation failed: {schema_errors[0]}")
    write_jsonl(validated, args.output)
    write_json(generate_report(validated), args.report_output or default_report_output(args.output))


if __name__ == "__main__":
    main()
