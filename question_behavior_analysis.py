"""Shadow-only group-relative question behavior diagnostics (22A-0/1/2).

This module deliberately reads scored JSONL and writes independent artifacts.  It
never mutates a scored record, routing plan, state machine, or memory bank.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import statistics
import time
from collections import Counter
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Tuple

from openai import AsyncOpenAI

from local_api_config import get_config_list, get_config_value
from pipeline_runtime import iter_json_records, publish_records


ANALYSIS_VERSION = "question-behavior-v1"
MAX_WITHIN_ANSWER_JUDGE_RANGE = 0.10
MIN_BETWEEN_ANSWER_GAP = 0.15
MIN_VALID_TRIALS = 2
UNIFORMLY_HARD_MAX = 0.35
UNIFORMLY_EASY_MIN = 0.85


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and 0.0 <= number <= 1.0 else None


def _sample_id(item: Dict[str, Any]) -> str:
    for key in ("root_sample_id", "sample_id", "node_id", "index"):
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return _fingerprint(item.get("prompt", ""))[:16]


def _node_id(item: Dict[str, Any]) -> str:
    return str(item.get("node_id") or item.get("branch_id") or _sample_id(item))


def _evaluation_config(item: Dict[str, Any]) -> Dict[str, Any]:
    result = item.get("scoring_result") if isinstance(item.get("scoring_result"), dict) else {}
    return {
        "evaluation_protocol": item.get("evaluation_protocol") or result.get("evaluation_protocol"),
        "answer_model": result.get("answer_model"),
        "judge_model": result.get("judge_model"),
        "qwen_summary": result.get("qwen_score_summary") or item.get("qwen_score_summary"),
        "gpt_summary": result.get("gpt_score_summary") or item.get("gpt_score_summary"),
    }


def _reference_answer(item: Dict[str, Any]) -> str:
    direct = item.get("reference_answer")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    meta = item.get("meta_info") if isinstance(item.get("meta_info"), dict) else {}
    references = meta.get("references")
    if isinstance(references, list) and references and isinstance(references[0], str) and references[0].strip():
        return references[0].strip()
    return ""


def _rubric_index(rubric: Any) -> Tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    if not isinstance(rubric, list) or not rubric:
        return {}, "rubric_missing"
    indexed: Dict[str, Dict[str, Any]] = {}
    for entry in rubric:
        if not isinstance(entry, dict) or not isinstance(entry.get("title"), str) or not entry["title"].strip():
            return {}, "rubric_title_missing"
        title = entry["title"].strip()
        if title in indexed:
            return {}, "rubric_title_duplicate"
        try:
            weight = float(entry.get("weight", 0))
        except (TypeError, ValueError):
            return {}, "rubric_weight_invalid"
        indexed[title] = {"title": title, "weight": weight}
    return indexed, None


def _judge_rates(results: Any) -> Tuple[List[float], List[Dict[str, Any]]]:
    if not isinstance(results, list):
        return [], []
    usable: List[Dict[str, Any]] = []
    rates: List[float] = []
    for result in results:
        if not isinstance(result, dict) or result.get("error"):
            continue
        rate = _number(result.get("score_rate"))
        if rate is None:
            continue
        usable.append(result)
        rates.append(rate)
    return rates, usable


def _trial_stats(trial: Dict[str, Any], rubric: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    answer = trial.get("candidate_answer")
    index = trial.get("trial_index")
    if not isinstance(answer, str) or not answer.strip() or not isinstance(index, int):
        return None
    qwen_rates, qwen_results = _judge_rates(trial.get("qwen_judge_results"))
    if not qwen_rates:
        return None
    declared = _number(trial.get("qwen_score_rate_mean"))
    qwen_mean = declared if declared is not None else statistics.fmean(qwen_rates)
    gpt_rates, _ = _judge_rates(trial.get("gpt_judge_results"))
    item_values: Dict[str, List[float]] = {title: [] for title in rubric}
    alignment_error = False
    for result in qwen_results:
        scores = result.get("item_scores")
        if not isinstance(scores, list):
            alignment_error = True
            continue
        seen = set()
        for score in scores:
            title = score.get("title") if isinstance(score, dict) else None
            if not isinstance(title, str) or title not in rubric or title in seen:
                alignment_error = True
                continue
            seen.add(title)
            weight = rubric[title]["weight"]
            try:
                awarded = float(score.get("awarded", 0))
            except (TypeError, ValueError):
                alignment_error = True
                continue
            if weight > 0:
                item_values[title].append(max(0.0, min(1.0, awarded / weight)))
            elif weight < 0:
                item_values[title].append(max(0.0, min(1.0, abs(awarded) / abs(weight))))
            # Zero-weight observations are retained but never used as attribution.
    item_statistics = []
    for title, definition in rubric.items():
        values = item_values[title]
        item_statistics.append({
            "title": title,
            "weight": definition["weight"],
            "kind": "positive" if definition["weight"] > 0 else "penalty" if definition["weight"] < 0 else "observation",
            "mean_ratio": statistics.fmean(values) if values else None,
        })
    return {
        "trial_index": index,
        "answer_fragment_id": f"trial_{index}_full",
        "candidate_answer": answer,
        "qwen_score_rate_mean": qwen_mean,
        "qwen_score_min": min(qwen_rates),
        "qwen_score_max": max(qwen_rates),
        "qwen_score_range": max(qwen_rates) - min(qwen_rates),
        "qwen_repeat_count": len(qwen_rates),
        "gpt_score_rate_mean": statistics.fmean(gpt_rates) if gpt_rates else None,
        "gpt_repeat_count": len(gpt_rates),
        "gpt_complete": bool(gpt_rates) and len(gpt_rates) == len(trial.get("gpt_judge_results") or []),
        "item_statistics": item_statistics,
        "item_alignment_error": alignment_error,
    }


def _label_order(label: str) -> int:
    order = ["insufficient_trials", "judge_unstable", "cross_judge_disputed", "rubric_or_question_risk",
             "informative_answer_gap", "uniformly_hard", "uniformly_easy", "near_group"]
    return order.index(label) if label in order else len(order)


def analyze_item(item: Dict[str, Any], *, min_valid_trials: int = MIN_VALID_TRIALS,
                 max_judge_range: float = MAX_WITHIN_ANSWER_JUDGE_RANGE,
                 min_answer_gap: float = MIN_BETWEEN_ANSWER_GAP) -> Dict[str, Any]:
    """Produce deterministic 22A-0/1 evidence without modifying ``item``."""
    prompt = item.get("prompt") if isinstance(item.get("prompt"), str) else ""
    rubric_raw = item.get("rubric")
    rubric, rubric_error = _rubric_index(rubric_raw)
    scoring = item.get("scoring_result") if isinstance(item.get("scoring_result"), dict) else {}
    trials_raw = scoring.get("answer_trials") if isinstance(scoring.get("answer_trials"), list) else []
    trials = [_trial_stats(trial, rubric) for trial in trials_raw if isinstance(trial, dict)] if rubric else []
    trials = sorted([trial for trial in trials if trial], key=lambda trial: trial["trial_index"])
    labels: List[str] = []
    reason: Optional[str] = None
    if not scoring or item.get("decision_evaluation_status") not in (None, "completed"):
        labels.append("insufficient_trials")
        reason = "decision_evaluation_incomplete"
    elif len(trials) < min_valid_trials:
        labels.append("insufficient_trials")
        reason = "insufficient_valid_trials"
    if rubric_error:
        labels.append("rubric_or_question_risk")
        reason = reason or rubric_error
    if not _reference_answer(item):
        labels.append("rubric_or_question_risk")
        reason = reason or "reference_answer_missing"
    if trials and any(trial["qwen_score_range"] > max_judge_range for trial in trials):
        labels.append("judge_unstable")
        reason = reason or "within_answer_judge_range_exceeded"
    if trials and any(trial["item_alignment_error"] for trial in trials):
        labels.append("rubric_or_question_risk")
        reason = reason or "item_score_title_unaligned"

    rates = [trial["qwen_score_rate_mean"] for trial in trials]
    group_mean = statistics.fmean(rates) if rates else None
    for trial in trials:
        trial["relative_advantage"] = trial["qwen_score_rate_mean"] - group_mean if group_mean is not None else None
        trial["relative_class"] = "high" if trial["relative_advantage"] and trial["relative_advantage"] > 0 else "low" if trial["relative_advantage"] and trial["relative_advantage"] < 0 else "near_mean"
    group_gap = max(rates) - min(rates) if rates else None
    high = max(trials, key=lambda trial: (trial["qwen_score_rate_mean"], -trial["trial_index"]), default=None)
    low = min(trials, key=lambda trial: (trial["qwen_score_rate_mean"], trial["trial_index"]), default=None)

    cross_disputed = False
    gpt_incomplete = False
    if high and low:
        if high["gpt_complete"] and low["gpt_complete"]:
            high_gpt, low_gpt = high["gpt_score_rate_mean"], low["gpt_score_rate_mean"]
            cross_disputed = bool(high_gpt is not None and low_gpt is not None and high_gpt < low_gpt)
        else:
            gpt_incomplete = True
    if cross_disputed:
        labels.append("cross_judge_disputed")
        reason = reason or "qwen_gpt_order_reversed"

    item_differences: List[Dict[str, Any]] = []
    if high and low:
        high_items = {entry["title"]: entry for entry in high["item_statistics"]}
        low_items = {entry["title"]: entry for entry in low["item_statistics"]}
        for title, definition in rubric.items():
            high_ratio, low_ratio = high_items[title]["mean_ratio"], low_items[title]["mean_ratio"]
            if definition["weight"] == 0 or high_ratio is None or low_ratio is None:
                continue
            item_differences.append({
                "title": title, "weight": definition["weight"],
                "kind": "positive" if definition["weight"] > 0 else "penalty",
                "high_ratio": high_ratio, "low_ratio": low_ratio,
                "difference": high_ratio - low_ratio,
            })
    localizable = any(abs(entry["difference"]) > 1e-9 for entry in item_differences)
    blocked = {"insufficient_trials", "judge_unstable", "cross_judge_disputed", "rubric_or_question_risk"}
    if not any(label in blocked for label in labels):
        if group_gap is not None and group_gap >= min_answer_gap and localizable:
            labels.append("informative_answer_gap")
        elif rates and max(rates) <= UNIFORMLY_HARD_MAX:
            labels.append("uniformly_hard")
        elif rates and min(rates) >= UNIFORMLY_EASY_MIN:
            labels.append("uniformly_easy")
        else:
            labels.append("near_group")
    labels = sorted(set(labels), key=_label_order)
    qualified = (
        bool({"informative_answer_gap", "uniformly_hard"} & set(labels))
        and not any(label in blocked for label in labels)
        and localizable and bool(prompt.strip()) and bool(rubric) and bool(_reference_answer(item))
    )
    prompt_fp = _fingerprint(prompt)
    rubric_fp = _fingerprint(rubric_raw)
    config_fp = _fingerprint(_evaluation_config(item))
    identity = {
        "analysis_version": ANALYSIS_VERSION, "root_sample_id": _sample_id(item), "node_id": _node_id(item),
        "prompt_fingerprint": prompt_fp, "rubric_fingerprint": rubric_fp,
        "evaluation_config_fingerprint": config_fp,
    }
    source_trial_ids = [trial["trial_index"] for trial in trials]
    return {
        "analysis_id": _fingerprint(identity)[:32], **identity,
        "source_trial_ids": source_trial_ids,
        "group_statistics": {
            "valid_trial_count": len(trials), "group_mean": group_mean, "group_gap": group_gap,
            "gpt_incomplete": gpt_incomplete, "trials": trials, "item_differences": item_differences,
            "high_trial_index": high["trial_index"] if high else None,
            "low_trial_index": low["trial_index"] if low else None,
        },
        "behavior_labels": labels,
        "qualification": {"observer_eligible": qualified, "reason": None if qualified else reason or labels[0]},
        "observer_result": {}, "observer_status": "not_requested", "analysis_status": "shadow",
        "created_at": int(time.time()),
    }


def analysis_is_stale(existing: Dict[str, Any], item: Dict[str, Any]) -> bool:
    """Return whether a prior shadow record belongs to an older question version."""
    current = analyze_item(item)
    return any(
        existing.get(field) != current.get(field)
        for field in ("analysis_version", "root_sample_id", "node_id", "prompt_fingerprint",
                      "rubric_fingerprint", "evaluation_config_fingerprint")
    )


def validate_shadow_record(record: Dict[str, Any]) -> Tuple[bool, str]:
    required = (
        "analysis_id", "root_sample_id", "node_id", "prompt_fingerprint", "rubric_fingerprint",
        "evaluation_config_fingerprint", "source_trial_ids", "group_statistics", "behavior_labels",
        "observer_result", "analysis_status", "analysis_version", "created_at",
    )
    if any(field not in record for field in required):
        return False, "shadow_required_field_missing"
    if record.get("analysis_status") != "shadow" or record.get("analysis_version") != ANALYSIS_VERSION:
        return False, "shadow_version_or_status_invalid"
    if not isinstance(record.get("source_trial_ids"), list) or not isinstance(record.get("group_statistics"), dict):
        return False, "shadow_statistics_invalid"
    allowed_labels = {
        "insufficient_trials", "judge_unstable", "cross_judge_disputed", "rubric_or_question_risk",
        "informative_answer_gap", "uniformly_hard", "uniformly_easy", "near_group",
    }
    labels = record.get("behavior_labels")
    if not isinstance(labels, list) or not labels or not set(labels).issubset(allowed_labels):
        return False, "shadow_labels_invalid"
    return True, "ok"


def build_report(records: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(records)
    labels = Counter(label for row in rows for label in row.get("behavior_labels", []))
    reasons = Counter((row.get("qualification") or {}).get("reason") or "qualified" for row in rows)
    trial_counts = [row.get("group_statistics", {}).get("valid_trial_count", 0) for row in rows]
    gaps = [row.get("group_statistics", {}).get("group_gap") for row in rows]
    gaps = sorted(float(gap) for gap in gaps if isinstance(gap, (int, float)))
    return {
        "analysis_version": ANALYSIS_VERSION, "sample_count": len(rows),
        "valid_trial_coverage": sum(count >= MIN_VALID_TRIALS for count in trial_counts) / len(rows) if rows else 0.0,
        "informative_answer_gap_coverage": labels["informative_answer_gap"] / len(rows) if rows else 0.0,
        "observer_eligible_coverage": sum(
            bool((row.get("qualification") or {}).get("observer_eligible")) for row in rows
        ) / len(rows) if rows else 0.0,
        "label_counts": dict(sorted(labels.items())), "non_qualification_reasons": dict(sorted(reasons.items())),
        "threshold_calibration": {
            "max_within_answer_judge_range": MAX_WITHIN_ANSWER_JUDGE_RANGE,
            "min_between_answer_gap": MIN_BETWEEN_ANSWER_GAP,
            "observed_group_gap_median": statistics.median(gaps) if gaps else None,
            "observed_group_gap_p90": gaps[min(len(gaps) - 1, int(0.9 * (len(gaps) - 1)))] if gaps else None,
        },
    }


def _extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("observer response must be a JSON object")
    return parsed


def validate_observer_result(result: Dict[str, Any], record: Dict[str, Any]) -> Tuple[bool, str]:
    if result.get("analysis_status") != "completed":
        return False, "analysis_status_not_completed"
    if result.get("confidence") not in {"low", "medium", "high"}:
        return False, "confidence_invalid"
    if not isinstance(result.get("difference_summary"), str) or "question_or_rubric_risk" not in result:
        return False, "required_observer_fields_missing"
    for key in ("behavior_labels", "high_answer_strengths", "low_answer_failures", "rubric_evidence", "candidate_mechanisms"):
        if not isinstance(result.get(key), list):
            return False, f"{key}_invalid"
    valid_trials = set(record.get("source_trial_ids") or [])
    valid_fragments = {
        trial.get("answer_fragment_id")
        for trial in record.get("group_statistics", {}).get("trials", [])
    }
    valid_titles = {entry.get("title") for entry in record.get("group_statistics", {}).get("item_differences", [])}
    evidence = result.get("rubric_evidence", [])
    for entry in evidence:
        if not isinstance(entry, dict) or entry.get("trial_index") not in valid_trials:
            return False, "evidence_trial_missing"
        if entry.get("rubric_title") not in valid_titles:
            return False, "evidence_rubric_missing"
        if entry.get("answer_fragment_id") not in valid_fragments:
            return False, "evidence_fragment_missing"
    if result["confidence"] in {"medium", "high"} and not evidence:
        return False, "high_confidence_without_evidence"
    return True, "ok"


def observer_prompt(record: Dict[str, Any], source: Dict[str, Any]) -> str:
    stats = record["group_statistics"]
    selected = {stats.get("high_trial_index"), stats.get("low_trial_index")}
    trials = [
        {"trial_index": t["trial_index"], "answer_fragment_id": t["answer_fragment_id"], "candidate_answer": t["candidate_answer"],
         "qwen_score_rate_mean": t["qwen_score_rate_mean"], "gpt_score_rate_mean": t["gpt_score_rate_mean"]}
        for t in stats.get("trials", []) if t.get("trial_index") in selected
    ]
    allowed = {
        "question": source.get("prompt"), "reference_answer": _reference_answer(source),
        "rubric": source.get("rubric"), "selected_answers": trials,
        "rubric_item_differences": stats.get("item_differences", []),
    }
    return (
        "You are a single-question behavior observer. Use only the JSON evidence below. "
        "Do not infer hidden profiles, prior operators, memory, or judge prose. Explain concrete answer differences or risks. "
        "Every conclusion must be grounded in rubric_evidence with trial_index, rubric_title, and answer_fragment_id. "
        "Use unknown when evidence is insufficient. Return JSON only with analysis_status, behavior_labels, difference_summary, "
        "high_answer_strengths, low_answer_failures, rubric_evidence, candidate_mechanisms, question_or_rubric_risk, confidence.\n\n"
        + _canonical(allowed)
    )


async def call_observer(client: Any, model: str, prompt: str, timeout: float) -> Dict[str, Any]:
    response = await client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": prompt}], temperature=0,
        response_format={"type": "json_object"}, timeout=timeout,
    )
    return _extract_json(response.choices[0].message.content)


async def observe_records(records: List[Dict[str, Any]], sources: Dict[str, Dict[str, Any]], *, model: str,
                          base_url: str, api_key: str, timeout: float, concurrency: int,
                          prior: Dict[str, Dict[str, Any]], min_eligible_coverage: float = 0.0) -> List[Dict[str, Any]]:
    eligible_coverage = sum(bool(row.get("qualification", {}).get("observer_eligible")) for row in records) / len(records) if records else 0.0
    if eligible_coverage < min_eligible_coverage:
        outputs = []
        for record in records:
            output = deepcopy(record)
            output["observer_status"] = "skipped"
            output["observer_result"] = {"analysis_status": "skipped", "reason": "eligible_coverage_below_threshold"}
            outputs.append(output)
        return outputs
    client = AsyncOpenAI(api_key=api_key or "EMPTY_KEY", base_url=base_url or None, timeout=timeout)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(record: Dict[str, Any]) -> Dict[str, Any]:
        output = deepcopy(record)
        prior_record = prior.get(record["analysis_id"])
        if prior_record and prior_record.get("observer_status") in {"completed", "failed"}:
            return deepcopy(prior_record)
        if not record.get("qualification", {}).get("observer_eligible"):
            output["observer_status"] = "skipped"
            output["observer_result"] = {"analysis_status": "skipped", "reason": record["qualification"].get("reason")}
            return output
        source = sources.get(record["analysis_id"])
        if source is None:
            output["observer_status"] = "failed"
            output["observer_result"] = {"analysis_status": "failed", "error_type": "source_not_found"}
            return output
        started_at = int(time.time())
        started = time.monotonic()
        try:
            async with semaphore:
                result = await call_observer(client, model, observer_prompt(record, source), timeout)
            valid, reason = validate_observer_result(result, record)
            if not valid:
                raise ValueError(reason)
            output["observer_status"] = "completed"
            output["observer_result"] = result
            output["observer_call"] = {
                "model": model, "requested_at": started_at,
                "duration_ms": round((time.monotonic() - started) * 1000, 3), "status": "completed",
            }
        except Exception as exc:  # Failure is recorded, never published as a partial conclusion.
            output["observer_status"] = "failed"
            output["observer_result"] = {"analysis_status": "failed", "error_type": type(exc).__name__, "error": str(exc)[:300]}
            output["observer_call"] = {
                "model": model, "requested_at": started_at,
                "duration_ms": round((time.monotonic() - started) * 1000, 3), "status": "failed",
            }
        return output

    return await asyncio.gather(*(one(record) for record in records))


def _load_records(path: str) -> List[Dict[str, Any]]:
    return [record for record in iter_json_records(path, stage="question_behavior_analysis")]


def _write_report(path: str, report: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as target:
        json.dump(report, target, ensure_ascii=False, indent=2, sort_keys=True)
        target.write("\n")
    os.replace(temporary, path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="22A shadow-only group-relative behavior diagnostics.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("statistics", "diagnose"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--input", required=True)
        cmd.add_argument("--output", required=True)
        cmd.add_argument("--report-output")
        cmd.add_argument("--previous-output", help="older behavior_analysis.jsonl; report stale records only")
    observe = sub.add_parser("observe")
    observe.add_argument("--input", required=True, help="22A-1 behavior_analysis.jsonl")
    observe.add_argument("--source-input", required=True, help="the scored JSONL used by diagnose")
    observe.add_argument("--output", required=True)
    observe.add_argument("--prior-observations")
    observe.add_argument("--model", default=os.getenv("QUESTION_BEHAVIOR_OBSERVER_MODEL") or get_config_value("QUESTION_BEHAVIOR_OBSERVER_MODEL", "GPT_MODEL", default=""))
    observe.add_argument("--base-url", default=os.getenv("QUESTION_BEHAVIOR_OBSERVER_BASE_URL") or get_config_value("QUESTION_BEHAVIOR_OBSERVER_BASE_URL", "OPENAI_BASE_URL", "BASE_URL", default=""))
    observe.add_argument("--api-key", default=os.getenv("QUESTION_BEHAVIOR_OBSERVER_API_KEY") or (get_config_list("QUESTION_BEHAVIOR_OBSERVER_API_KEY", "OPENAI_API_KEY", "API_KEYS") or [""])[0])
    observe.add_argument("--timeout", type=float, default=120.0)
    observe.add_argument("--concurrency", type=int, default=1)
    observe.add_argument("--min-eligible-coverage", type=float, default=0.0,
                         help="Freeze this per-run threshold after 22A-0 calibration; below it no observer calls are made.")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    if args.command in {"statistics", "diagnose"}:
        source = _load_records(args.input)
        records = [analyze_item(item) for item in source]
        if args.command == "statistics":
            report = build_report(records)
            _write_report(args.output, report)
            return
        for record in records:
            valid, reason = validate_shadow_record(record)
            if not valid:
                raise ValueError(f"invalid question behavior shadow record: {reason}")
        publish_records(records, args.output, stage="question_behavior_diagnosis", input_path=args.input,
                        config={"analysis_version": ANALYSIS_VERSION}, code_paths=[__file__])
        if args.report_output:
            report = build_report(records)
            if args.previous_output:
                previous = _load_records(args.previous_output)
                previous_by_identity = {
                    (row.get("root_sample_id"), row.get("node_id")): row for row in previous
                }
                report["stale_prior_record_count"] = sum(
                    analysis_is_stale(previous_by_identity[(record["root_sample_id"], record["node_id"])], item)
                    for record, item in zip(records, source)
                    if (record["root_sample_id"], record["node_id"]) in previous_by_identity
                )
            _write_report(args.report_output, report)
        return
    if not args.model:
        raise SystemExit("--model or QUESTION_BEHAVIOR_OBSERVER_MODEL is required for observe")
    records = _load_records(args.input)
    sources = {analyze_item(item)["analysis_id"]: item for item in _load_records(args.source_input)}
    prior = {row.get("analysis_id"): row for row in _load_records(args.prior_observations)} if args.prior_observations else {}
    observed = asyncio.run(observe_records(records, sources, model=args.model, base_url=args.base_url, api_key=args.api_key,
                                            timeout=args.timeout, concurrency=args.concurrency, prior=prior,
                                            min_eligible_coverage=max(0.0, min(1.0, args.min_eligible_coverage))))
    publish_records(observed, args.output, stage="question_behavior_observer", input_path=args.input,
                    config={"analysis_version": ANALYSIS_VERSION, "model": args.model}, code_paths=[__file__])


if __name__ == "__main__":
    main()
