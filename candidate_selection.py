import argparse
import json
import os
from collections import defaultdict
from typing import Any, DefaultDict, Dict, Iterable, List, Optional, Sequence, Tuple


RISK_SCORE = {"low": 0, "medium": -5, "high": -25}
DIFFICULTY_GAIN_HARD_REJECT_TAGS = {
    "missing_premise_named",
    "conclusion_hint_revealed",
    "answer_path_scaffolded",
    "external_fact_dependency",
    "format_difficulty_only",
    "axis_shift",
}
PASS_DIFFICULTY_GAIN_LABELS = {"clear_gain", "probable_gain", "not_applicable"}


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
    return result if isinstance(result, dict) else {"passed": False, "reject_reason": "缺少 validation_result"}


def difficulty_gain_validation(item: Dict[str, Any]) -> Dict[str, Any]:
    result = item.get("difficulty_gain_validation")
    return result if isinstance(result, dict) else {}


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
        return -10_000.0, [_clean_text(validation.get("reject_reason")) or "未通过复杂度校验"]

    score = 100.0
    quality = validation_quality_score(item)
    score += quality * 40
    if validation.get("main_axis_count") == 1:
        reasons.append("主轴唯一")
    if validation.get("output_tasks_count", 1) <= 1:
        reasons.append("输出任务单一")
    if validation.get("candidate_options_count", 0) <= 2:
        reasons.append("候选项数量克制")
    if validation.get("counterfactual_count", 0) == 0:
        reasons.append("未引入反事实复杂度")
    score += _risk_penalty(validation, "external_knowledge_risk")
    score += _risk_penalty(validation, "format_difficulty_risk")
    score += _risk_penalty(validation, "repeat_pattern_risk")

    generation = item.get("candidate_generation")
    if isinstance(generation, dict) and generation.get("operator_source") == "primary":
        score += 2
        reasons.append("来自 router primary operator")
    focus = _metadata(item).get("expected_evaluation_focus")
    if isinstance(focus, list) and focus:
        score += 3
        reasons.append("保留 expected_evaluation_focus 元数据")

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


def difficulty_gain_reject_reason(item: Dict[str, Any]) -> Optional[str]:
    validation = difficulty_gain_validation(item)
    if not validation:
        return "缺少 difficulty_gain_validation"
    risk_tags = {
        _clean_text(tag)
        for tag in validation.get("risk_tags", [])
        if _clean_text(tag)
    }
    hard_tags = sorted(risk_tags & DIFFICULTY_GAIN_HARD_REJECT_TAGS)
    if hard_tags:
        return "命中难度收益高危标签: " + ", ".join(hard_tags)
    if validation.get("passed") is not True:
        return _clean_text(validation.get("reject_reason")) or "未通过难度收益验证"
    label = _clean_text(validation.get("difficulty_gain_label"))
    if label and label not in PASS_DIFFICULTY_GAIN_LABELS:
        return f"difficulty_gain_label={label} 不允许进入候选选择"
    return None


def score_candidate(item: Dict[str, Any], *, allow_missing_difficulty_gain: bool = False) -> Tuple[float, List[str]]:
    validation = validation_result(item)
    if not validation.get("passed"):
        return -10_000.0, [_clean_text(validation.get("reject_reason")) or "未通过复杂度校验"]

    difficulty_validation = difficulty_gain_validation(item)
    if not difficulty_validation and allow_missing_difficulty_gain:
        legacy_score, legacy_reasons = legacy_score_candidate(item)
        legacy_reasons.append("legacy 模式允许缺少 difficulty_gain_validation")
        return legacy_score, legacy_reasons

    reject_reason = difficulty_gain_reject_reason(item)
    if reject_reason:
        return -9_000.0, [reject_reason]

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
        reasons.append("weak probe 命中目标 failure")
    elif probe_score == 0.5:
        reasons.append("weak probe 未启用")
    return round(selection_score, 4), reasons


def build_rejected_candidate(item: Dict[str, Any], fallback_index: int, *, forced_reason: Optional[str] = None) -> Dict[str, Any]:
    validation = validation_result(item)
    difficulty_validation = difficulty_gain_validation(item)
    reason = forced_reason or _clean_text(validation.get("reject_reason")) or "未被选中"
    rejected = {
        "candidate_id": candidate_id(item, fallback_index),
        "operator_used": candidate_operator(item),
        "reject_reason": reason,
        "validation_result": validation,
    }
    if difficulty_validation:
        rejected["difficulty_gain_validation"] = difficulty_validation
    return rejected


def build_invalid_case(item: Dict[str, Any], fallback_index: int, *, reason: Optional[str] = None) -> Dict[str, Any]:
    validation = validation_result(item)
    metadata = _metadata(item)
    difficulty_validation = difficulty_gain_validation(item)
    invalid_type = validation.get("invalid_type") or "not_selected"
    if validation.get("passed") is False:
        invalid_type = validation.get("invalid_type") or "validation_failed"
    elif difficulty_validation and difficulty_validation.get("passed") is not True:
        invalid_type = difficulty_validation.get("difficulty_gain_label") or "difficulty_gain_validation_failed"
    elif not difficulty_validation and validation.get("passed"):
        invalid_type = "missing_difficulty_gain_validation"
    invalid_case = {
        "sample_id": item.get("sample_id", item.get("index", "")),
        "round": round_value(item),
        "candidate_id": candidate_id(item, fallback_index),
        "operator_used": candidate_operator(item),
        "invalid_type": invalid_type,
        "reason": reason or _clean_text(validation.get("reject_reason")) or "candidate was not selected",
        "suggested_operator": metadata.get("operator_used") or item.get("candidate_operator"),
        "sample_signature": sample_signature(item),
    }
    if difficulty_validation:
        invalid_case["difficulty_gain_validation"] = difficulty_validation
        invalid_case["risk_tags"] = difficulty_validation.get("risk_tags", [])
    failure_memory = item.get("failure_memory_candidate")
    if isinstance(failure_memory, dict):
        invalid_case["failure_memory_candidate"] = failure_memory
    return invalid_case


def _strip_candidate_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    result.pop("candidate_generation", None)
    return result


def _restore_original_passthrough(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    meta_info = result.get("meta_info")
    meta_info = meta_info if isinstance(meta_info, dict) else {}
    old_prompt = meta_info.get("prompt_old")
    if isinstance(old_prompt, str) and old_prompt.strip():
        result["prompt"] = old_prompt.strip()
    stale_fields = {
        "rubric": "stale_rubric",
        "score_prompt": "stale_score_prompt",
        "scoring_result": "stale_scoring_result",
    }
    for field, stale_field in stale_fields.items():
        if field not in result and stale_field in meta_info:
            result[field] = meta_info.get(stale_field)
    result["question_evolved"] = False
    return result


def _main_reject_reasons(records: Sequence[Dict[str, Any]]) -> List[str]:
    counter: DefaultDict[str, int] = defaultdict(int)
    for record in records:
        difficulty_validation = difficulty_gain_validation(record)
        for tag in difficulty_validation.get("risk_tags", []) if difficulty_validation else []:
            text = _clean_text(tag)
            if text:
                counter[text] += 1
        label = _clean_text(difficulty_validation.get("difficulty_gain_label")) if difficulty_validation else ""
        if label and label not in PASS_DIFFICULTY_GAIN_LABELS:
            counter[label] += 1
        elif not difficulty_validation:
            counter["missing_difficulty_gain_validation"] += 1
    return [
        reason
        for reason, _ in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:5]
    ]


def select_group(
    records: Sequence[Dict[str, Any]],
    *,
    allow_missing_difficulty_gain: bool = False,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    if not records:
        raise ValueError("candidate group is empty")

    if len(records) == 1 and records[0].get("question_evolved") is False:
        selected = _strip_candidate_fields(records[0])
        cid = candidate_id(records[0], 1)
        selected["candidate_selection"] = {
            "selected": True,
            "selected_candidate_id": cid,
            "selected_operator": "",
            "selection_status": "pass_through_original",
            "selection_reason": "透传样本不参与候选选择。",
            "rejected_candidates": [],
        }
        return selected, []

    scored: List[Tuple[float, List[str], int, Dict[str, Any]]] = []
    for index, record in enumerate(records, start=1):
        score, reasons = score_candidate(record, allow_missing_difficulty_gain=allow_missing_difficulty_gain)
        scored.append((score, reasons, index, record))
    scored.sort(key=lambda item: (item[0], -item[2]), reverse=True)

    best_score, best_reasons, best_index, best_record = scored[0]
    invalid_cases: List[Dict[str, Any]] = []
    rejected_candidates: List[Dict[str, Any]] = []

    if best_score < 0:
        selected = _strip_candidate_fields(_restore_original_passthrough(records[0]))
        difficulty_failures = [
            record
            for record in records
            if validation_result(record).get("passed") and difficulty_gain_reject_reason(record)
        ]
        selection_status = (
            "no_candidate_passed_difficulty_gain"
            if difficulty_failures
            else "no_candidate_passed_validation"
        )
        selected["candidate_selection"] = {
            "selected": False,
            "selected_candidate_id": candidate_id(records[0], 1),
            "selected_operator": "",
            "selection_score": 0.0,
            "selection_status": selection_status,
            "recommended_next_action": "retry_with_backup_operator",
            "rejected_candidate_count": len(records),
            "main_reject_reasons": _main_reject_reasons(records),
            "selection_reason": (
                "没有候选通过难度收益验证，回退为原题透传。"
                if selection_status == "no_candidate_passed_difficulty_gain"
                else "所有候选均未通过复杂度校验，回退为原题透传。"
            ),
            "rejected_candidates": [
                build_rejected_candidate(record, index, forced_reason="；".join(reasons))
                for _, reasons, index, record in scored
            ],
        }
        for _, reasons, index, record in scored:
            invalid_cases.append(build_invalid_case(record, index, reason="；".join(reasons)))
        return selected, invalid_cases

    selected = _strip_candidate_fields(best_record)
    selected_id = candidate_id(best_record, best_index)
    selected_operator = candidate_operator(best_record)
    best_difficulty_validation = difficulty_gain_validation(best_record)

    for _, _, index, record in scored[1:]:
        reason = "低于入选候选的综合选择分"
        if not validation_result(record).get("passed"):
            reason = _clean_text(validation_result(record).get("reject_reason")) or reason
            invalid_cases.append(build_invalid_case(record, index, reason=reason))
        else:
            difficulty_reason = difficulty_gain_reject_reason(record)
            if difficulty_reason:
                reason = difficulty_reason
                invalid_cases.append(build_invalid_case(record, index, reason=reason))
        rejected_candidates.append(build_rejected_candidate(record, index, forced_reason=reason))

    selected["candidate_selection"] = {
        "selected": True,
        "selected_candidate_id": selected_id,
        "selected_operator": selected_operator,
        "selection_score": round(best_score, 4),
        "selection_status": (
            "selected_after_difficulty_gain_validation"
            if best_difficulty_validation
            else "selected_legacy_without_difficulty_gain"
        ),
        "difficulty_gain_score": best_difficulty_validation.get("difficulty_gain_score"),
        "difficulty_gain_label": best_difficulty_validation.get("difficulty_gain_label"),
        "risk_tags": best_difficulty_validation.get("risk_tags", []),
        "weak_probe_used": bool(isinstance(best_difficulty_validation.get("weak_probe"), dict) and best_difficulty_validation["weak_probe"].get("enabled")),
        "selection_reason": "；".join(best_reasons) if best_reasons else "通过复杂度校验且综合分最高。",
        "rejected_candidates": rejected_candidates,
    }
    return selected, invalid_cases


def select_candidates(
    records: Sequence[Dict[str, Any]],
    *,
    allow_missing_difficulty_gain: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    groups: DefaultDict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[candidate_group_id(record)].append(record)

    selected_records: List[Dict[str, Any]] = []
    invalid_cases: List[Dict[str, Any]] = []
    for group_records in groups.values():
        selected, group_invalid = select_group(
            group_records,
            allow_missing_difficulty_gain=allow_missing_difficulty_gain,
        )
        selected_records.append(selected)
        invalid_cases.extend(group_invalid)
    return selected_records, invalid_cases


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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_json_or_jsonl(args.input)
    selected, invalid_cases = select_candidates(
        records,
        allow_missing_difficulty_gain=args.allow_missing_difficulty_gain,
    )
    write_jsonl(selected, args.output)
    if invalid_cases and not args.no_invalid_output:
        write_jsonl(invalid_cases, args.invalid_output, append=True)


if __name__ == "__main__":
    main()
