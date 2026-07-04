import argparse
import asyncio
import hashlib
import json
import logging
import math
import os
import statistics
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from scoring import (
    ANSWER_BASE_URL,
    ANSWER_MODEL,
    JUDGE_BASE_URL,
    JUDGE_MODEL,
    AnswerLLMClient,
    RotatingAPIClient,
    ScoringProcessor,
    compute_score_rate,
    ensure_sample_identity,
    parse_api_keys,
    resolve_answer_api_key,
)


logger = logging.getLogger(__name__)

INITIAL_TRIALS = 3
EXTRA_TRIALS = 2
MAX_TRIALS = 5

HIGH_SCORE_THRESHOLD = 0.80
STRONG_HIGH_THRESHOLD = 0.85
BORDERLINE_LOW = 0.70
EDGE_LOW = 0.72
EDGE_HIGH = 0.83

STD_MEDIUM = 0.04
STD_EXTRA = 0.06
STD_HIGH = 0.08
RANGE_MEDIUM = 0.10
RANGE_EXTRA = 0.15
RANGE_HIGH = 0.20
P75_DISCOUNT = 0.03

STABLE_HIGH = "stable_high"
UNSTABLE_HIGH = "unstable_high"
BORDERLINE_PROBE = "borderline_probe"
STABLE_LOW = "stable_low"
UNCERTAIN_LOW = "uncertain_low"
REVIEW_NEEDED = "review_needed"

PROBE_VERSION = "round0_stability_probe_v1"


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


def write_json(record: Dict[str, Any], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _as_score(value: Any) -> Optional[float]:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= score <= 1:
        return score
    return None


def _round_float(value: float) -> float:
    return round(float(value), 6)


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[int(position)]
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _trial_score(trial: Dict[str, Any]) -> Optional[float]:
    score = _as_score(trial.get("score_rate"))
    if score is not None:
        return score
    scoring_result = trial.get("scoring_result")
    if isinstance(scoring_result, dict):
        return compute_score_rate(scoring_result)
    return None


def _item_scores(scoring_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw_scores = scoring_result.get("item_scores")
    return raw_scores if isinstance(raw_scores, list) else []


def compute_rubric_item_stability(trials: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_index: Dict[int, List[float]] = {}
    title_by_index: Dict[int, str] = {}

    for trial in trials:
        scoring_result = trial.get("scoring_result")
        if not isinstance(scoring_result, dict):
            continue
        for index, item_score in enumerate(_item_scores(scoring_result)):
            if not isinstance(item_score, dict):
                continue
            try:
                awarded = float(item_score.get("awarded", 0) or 0)
                weight = float(item_score.get("weight", 0) or 0)
            except (TypeError, ValueError):
                continue
            hit = 1.0 if weight <= 0 and awarded >= 0 else (1.0 if awarded >= weight else 0.0)
            by_index.setdefault(index, []).append(hit)
            title = item_score.get("title")
            if isinstance(title, str) and title.strip():
                title_by_index[index] = title.strip()

    stability = []
    for index in sorted(by_index):
        awards = by_index[index]
        hit_count = sum(1 for value in awards if value >= 0.5)
        miss_count = len(awards) - hit_count
        row: Dict[str, Any] = {
            "item_index": index,
            "award_mean": _round_float(statistics.fmean(awards)),
            "award_std": _round_float(statistics.pstdev(awards)) if len(awards) > 1 else 0.0,
            "hit_count": hit_count,
            "miss_count": miss_count,
        }
        if index in title_by_index:
            row["title"] = title_by_index[index]
        stability.append(row)
    return stability


def compute_score_summary(
    trials: Sequence[Dict[str, Any]],
    *,
    high_score_threshold: float = HIGH_SCORE_THRESHOLD,
    strong_high_threshold: float = STRONG_HIGH_THRESHOLD,
    borderline_low: float = BORDERLINE_LOW,
    p75_discount: float = P75_DISCOUNT,
) -> Dict[str, Any]:
    scores = [_trial_score(trial) for trial in trials]
    valid_scores = [float(score) for score in scores if score is not None]
    if not valid_scores:
        return {
            "trial_count": 0,
            "stable_score": None,
            "admission_score": None,
            "volatility_level": "unknown",
            "admission_status": REVIEW_NEEDED,
            "recommended_evolution_budget": 0,
            "needs_manual_review": True,
            "rubric_item_stability": [],
        }

    score_mean = statistics.fmean(valid_scores)
    score_median = statistics.median(valid_scores)
    score_std = statistics.pstdev(valid_scores) if len(valid_scores) > 1 else 0.0
    score_min = min(valid_scores)
    score_max = max(valid_scores)
    score_p75 = _percentile(valid_scores, 0.75)
    score_range = score_max - score_min
    stable_score = score_median
    admission_score = max(score_median, score_p75 - p75_discount)
    full_score_count = sum(1 for score in valid_scores if score >= 0.999)
    high_score_count = sum(1 for score in valid_scores if score >= high_score_threshold)

    if len(valid_scores) < INITIAL_TRIALS:
        volatility_level = "insufficient_trials"
    elif score_std >= STD_HIGH or score_range >= RANGE_HIGH:
        volatility_level = "high"
    elif score_std >= STD_MEDIUM or score_range >= RANGE_MEDIUM:
        volatility_level = "medium"
    else:
        volatility_level = "low"

    admission_status, budget = classify_round0_admission(
        {
            "trial_count": len(valid_scores),
            "score_median": score_median,
            "score_max": score_max,
            "score_p75": score_p75,
            "score_range": score_range,
            "stable_score": stable_score,
            "admission_score": admission_score,
            "high_score_count": high_score_count,
            "volatility_level": volatility_level,
        },
        high_score_threshold=high_score_threshold,
        strong_high_threshold=strong_high_threshold,
        borderline_low=borderline_low,
    )

    return {
        "trial_count": len(valid_scores),
        "score_mean": _round_float(score_mean),
        "score_median": _round_float(score_median),
        "score_std": _round_float(score_std),
        "score_min": _round_float(score_min),
        "score_max": _round_float(score_max),
        "score_p75": _round_float(score_p75),
        "score_range": _round_float(score_range),
        "stable_score": _round_float(stable_score),
        "admission_score": _round_float(admission_score),
        "full_score_count": full_score_count,
        "high_score_count": high_score_count,
        "volatility_level": volatility_level,
        "admission_status": admission_status,
        "recommended_evolution_budget": budget,
        "needs_manual_review": admission_status in {UNCERTAIN_LOW, REVIEW_NEEDED},
        "rubric_item_stability": compute_rubric_item_stability(trials),
    }


def needs_extra_trials(
    summary: Dict[str, Any],
    *,
    edge_low: float = EDGE_LOW,
    edge_high: float = EDGE_HIGH,
    strong_high_threshold: float = STRONG_HIGH_THRESHOLD,
) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    score_median = _as_score(summary.get("score_median"))
    score_std = float(summary.get("score_std") or 0.0)
    score_range = float(summary.get("score_range") or 0.0)
    score_max = _as_score(summary.get("score_max")) or 0.0
    score_min = _as_score(summary.get("score_min")) or 0.0
    full_score_count = int(summary.get("full_score_count") or 0)

    if score_median is not None and edge_low <= score_median <= edge_high:
        reasons.append("score_median_near_threshold")
    if score_std >= STD_EXTRA:
        reasons.append("score_std_large")
    if score_range >= RANGE_EXTRA:
        reasons.append("score_range_large")
    if score_max >= strong_high_threshold and (score_median is None or score_median < HIGH_SCORE_THRESHOLD):
        reasons.append("high_score_but_unstable")
    if full_score_count >= 1 and score_min <= HIGH_SCORE_THRESHOLD:
        reasons.append("full_score_with_low_trial")
    return bool(reasons), reasons


def classify_round0_admission(
    summary: Dict[str, Any],
    item: Optional[Dict[str, Any]] = None,
    *,
    high_score_threshold: float = HIGH_SCORE_THRESHOLD,
    strong_high_threshold: float = STRONG_HIGH_THRESHOLD,
    borderline_low: float = BORDERLINE_LOW,
) -> Tuple[str, int]:
    stable_score = _as_score(summary.get("stable_score"))
    admission_score = _as_score(summary.get("admission_score"))
    score_max = _as_score(summary.get("score_max")) or 0.0
    score_p75 = _as_score(summary.get("score_p75")) or 0.0
    score_range = float(summary.get("score_range") or 0.0)
    high_score_count = int(summary.get("high_score_count") or 0)
    trial_count = int(summary.get("trial_count") or 0)
    volatility_level = str(summary.get("volatility_level") or "")

    if stable_score is None or trial_count < INITIAL_TRIALS:
        return REVIEW_NEEDED, 0

    if stable_score >= high_score_threshold and high_score_count >= math.ceil(max(trial_count, 1) / 2):
        return STABLE_HIGH, 2

    if (
        stable_score < high_score_threshold
        and score_max >= strong_high_threshold
        and score_p75 >= high_score_threshold
        and high_score_count >= 2
    ) or (
        stable_score >= 0.70
        and score_max >= strong_high_threshold
        and score_p75 >= high_score_threshold
        and volatility_level in {"medium", "high"}
    ):
        return UNSTABLE_HIGH, 2

    if (
        borderline_low <= stable_score < high_score_threshold
        and score_max >= high_score_threshold
    ) or (
        admission_score is not None
        and borderline_low <= admission_score < high_score_threshold
        and score_range >= RANGE_MEDIUM
    ):
        return BORDERLINE_PROBE, 1

    if stable_score < borderline_low and score_max < high_score_threshold and volatility_level == "low":
        return STABLE_LOW, 0

    return UNCERTAIN_LOW, 0


def _hash_text(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _record_cache_key(item: Dict[str, Any], trial_id: int, config: argparse.Namespace) -> str:
    answer_seed = _trial_seed(config, trial_id)
    payload = {
        "sample_id": item.get("sample_id") or item.get("index"),
        "prompt": item.get("prompt"),
        "rubric": item.get("rubric"),
        "score_prompt": item.get("score_prompt"),
        "answer_model": config.answer_model,
        "judge_model": config.judge_model,
        "answer_temperature": config.answer_temperature,
        "answer_top_p": config.answer_top_p,
        "answer_seed": answer_seed,
        "judge_temperature": config.judge_temperature,
        "probe_version": PROBE_VERSION,
        "trial_id": trial_id,
    }
    return _hash_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


def _trial_seed(config: argparse.Namespace, trial_id: int) -> Optional[int]:
    seed_base = getattr(config, "answer_seed_base", None)
    if seed_base is None:
        return None
    return int(seed_base) + trial_id


def _cache_path(cache_dir: Optional[str], cache_key: str) -> Optional[str]:
    if not cache_dir:
        return None
    return os.path.join(cache_dir, f"{cache_key}.json")


def _read_cached_trial(cache_dir: Optional[str], cache_key: str) -> Optional[Dict[str, Any]]:
    path = _cache_path(cache_dir, cache_key)
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            cached = json.load(f)
        return cached if isinstance(cached, dict) else None
    except Exception as exc:
        logger.warning("读取 round0 cache 失败 key=%s error=%s", cache_key, str(exc)[:200])
        return None


def _write_cached_trial(cache_dir: Optional[str], cache_key: str, trial: Dict[str, Any]) -> None:
    path = _cache_path(cache_dir, cache_key)
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(trial, f, ensure_ascii=False)
        f.write("\n")


def _extract_trial_fields(scoring_result: Dict[str, Any]) -> Tuple[List[Any], List[str]]:
    awards = []
    comments = []
    for item_score in _item_scores(scoring_result):
        if not isinstance(item_score, dict):
            continue
        awards.append(item_score.get("awarded"))
        comments.append(str(item_score.get("brief_reason", "") or ""))
    return awards, comments


async def run_answer_and_score_trial(
    item: Dict[str, Any],
    trial_id: int,
    processor: ScoringProcessor,
    config: argparse.Namespace,
) -> Dict[str, Any]:
    cache_key = _record_cache_key(item, trial_id, config)
    cached = None if getattr(config, "force", False) else _read_cached_trial(getattr(config, "cache_dir", None), cache_key)
    if cached is not None:
        cached["cache_hit"] = True
        return cached

    candidate_answer = await processor.generate_candidate_answer_with_retry(item)
    scoring_result = await processor.score_candidate_answer(item, candidate_answer)
    score_rate = compute_score_rate(scoring_result)
    rubric_item_awards, rubric_item_comments = _extract_trial_fields(scoring_result)
    answer_seed = _trial_seed(config, trial_id)

    trial = {
        "trial_id": trial_id,
        "cache_key": cache_key,
        "answer_model": config.answer_model if config.answer_mode == "llm" else "meta_info.references[0]",
        "judge_model": config.judge_model,
        "answer_temperature": config.answer_temperature,
        "answer_top_p": config.answer_top_p,
        "answer_seed": answer_seed,
        "seed_supported": False,
        "judge_temperature": config.judge_temperature,
        "force_generate_answer": True,
        "cache_hit": False,
        "candidate_answer": candidate_answer.strip(),
        "candidate_answer_hash": _hash_text(candidate_answer.strip()),
        "score_rate": score_rate,
        "scoring_result": scoring_result,
        "rubric_item_awards": rubric_item_awards,
        "rubric_item_comments": rubric_item_comments,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_cached_trial(getattr(config, "cache_dir", None), cache_key, trial)
    return trial


def _award_pattern_distance(trial: Dict[str, Any], averages: Sequence[float]) -> float:
    awards = trial.get("rubric_item_awards")
    if not isinstance(awards, list) or not averages:
        return 0.0
    distance = 0.0
    compared = 0
    for index, average in enumerate(averages):
        if index >= len(awards):
            break
        try:
            award_value = float(awards[index] or 0)
        except (TypeError, ValueError):
            continue
        hit = 1.0 if award_value > 0 else 0.0
        distance += abs(hit - average)
        compared += 1
    return distance / compared if compared else 0.0


def select_representative_trial(
    trials: Sequence[Dict[str, Any]],
    stable_score: Optional[float],
) -> Optional[Dict[str, Any]]:
    answer_lengths = [len(str(trial.get("candidate_answer", "") or "")) for trial in trials]
    median_length = statistics.median(answer_lengths) if answer_lengths else 0
    max_awards = max(
        (len(trial.get("rubric_item_awards", [])) for trial in trials if isinstance(trial.get("rubric_item_awards"), list)),
        default=0,
    )
    award_averages = []
    for index in range(max_awards):
        hits = []
        for trial in trials:
            awards = trial.get("rubric_item_awards")
            if not isinstance(awards, list) or index >= len(awards):
                continue
            try:
                hits.append(1.0 if float(awards[index] or 0) > 0 else 0.0)
            except (TypeError, ValueError):
                continue
        award_averages.append(statistics.fmean(hits) if hits else 0.0)

    scored_trials = []
    for trial in trials:
        score = _trial_score(trial)
        if score is None:
            continue
        answer = str(trial.get("candidate_answer", "") or "")
        try:
            trial_id = int(trial.get("trial_id") or 0)
        except (TypeError, ValueError):
            trial_id = 0
        scored_trials.append((
            abs(score - (stable_score if stable_score is not None else score)),
            _award_pattern_distance(trial, award_averages),
            abs(len(answer) - median_length),
            trial_id,
            trial,
        ))
    if not scored_trials:
        return None
    scored_trials.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return scored_trials[0][4]


def choose_representative_trial(
    trials: Sequence[Dict[str, Any]],
    stable_score: Optional[float],
) -> Optional[Dict[str, Any]]:
    return select_representative_trial(trials, stable_score)


async def process_item_with_stability_probe(
    item: Dict[str, Any],
    processor: ScoringProcessor,
    config: argparse.Namespace,
) -> Dict[str, Any]:
    async with processor.semaphore:
        result = deepcopy(item)
        ensure_sample_identity(result)
        result.pop("scoring_error", None)
        meta_info = result.get("meta_info")
        if not isinstance(meta_info, dict):
            meta_info = {}
        else:
            meta_info = dict(meta_info)
        if "score_rate" in result:
            meta_info.setdefault("pre_stability_score_rate", result.get("score_rate"))
        if isinstance(result.get("scoring_result"), dict):
            meta_info.setdefault("pre_stability_scoring_result", deepcopy(result["scoring_result"]))
        result["meta_info"] = meta_info

        trials: List[Dict[str, Any]] = []
        for trial_id in range(1, config.initial_trials + 1):
            trial_item = deepcopy(result)
            trial_item.pop("scoring_result", None)
            trial_item.pop("score_rate", None)
            trials.append(await run_answer_and_score_trial(trial_item, trial_id, processor, config))

        summary = compute_score_summary(
            trials,
            high_score_threshold=config.score_threshold,
            strong_high_threshold=config.strong_high_threshold,
            borderline_low=config.borderline_low,
        )
        extra_needed, extra_reasons = needs_extra_trials(
            summary,
            edge_low=config.edge_low,
            edge_high=config.edge_high,
            strong_high_threshold=config.strong_high_threshold,
        )

        max_trials = min(config.max_trials, config.initial_trials + config.extra_trials)
        if extra_needed:
            for trial_id in range(len(trials) + 1, max_trials + 1):
                trial_item = deepcopy(result)
                trial_item.pop("scoring_result", None)
                trial_item.pop("score_rate", None)
                trials.append(await run_answer_and_score_trial(trial_item, trial_id, processor, config))
            summary = compute_score_summary(
                trials,
                high_score_threshold=config.score_threshold,
                strong_high_threshold=config.strong_high_threshold,
                borderline_low=config.borderline_low,
            )

        summary["needs_extra_trials"] = extra_needed
        summary["extra_trials_reason"] = extra_reasons
        summary["probe_version"] = PROBE_VERSION

        stable_score = _as_score(summary.get("stable_score"))
        representative = select_representative_trial(trials, stable_score)
        if representative and isinstance(representative.get("scoring_result"), dict):
            result["scoring_result"] = deepcopy(representative["scoring_result"])
            representative_answer = str(representative.get("candidate_answer", "") or "").strip()
            if representative_answer:
                result["scoring_result"]["candidate_answer"] = representative_answer
                result["candidate_answer"] = representative_answer
            result["representative_round0_answer"] = {
                "trial_id": representative.get("trial_id"),
                "score_rate": _trial_score(representative),
                "candidate_answer": representative_answer,
                "candidate_answer_hash": representative.get("candidate_answer_hash"),
                "selection_reason": "closest_to_stable_score",
            }
            summary["representative_trial_id"] = representative.get("trial_id")
        if stable_score is not None:
            result["score_rate"] = stable_score

        result["round0_score_trials"] = trials
        result["round0_score_summary"] = summary
        result["rubric_item_stability"] = summary.get("rubric_item_stability", [])
        logger.info(
            "round0 stability sample=%s trials=%s status=%s stable=%.4f extra=%s reasons=%s",
            result.get("sample_id") or result.get("index"),
            summary.get("trial_count"),
            summary.get("admission_status"),
            stable_score if stable_score is not None else -1,
            extra_needed,
            ",".join(extra_reasons),
        )
        return result


async def process_records(
    records: Sequence[Dict[str, Any]],
    processor: ScoringProcessor,
    config: argparse.Namespace,
) -> List[Dict[str, Any]]:
    tasks = [process_item_with_stability_probe(record, processor, config) for record in records]
    try:
        from tqdm.asyncio import tqdm

        return await tqdm.gather(*tasks)
    except ImportError:
        return await asyncio.gather(*tasks)


def _stable_admitted(status: str) -> bool:
    return status in {STABLE_HIGH, UNSTABLE_HIGH, BORDERLINE_PROBE}


def build_stability_report(records: Sequence[Dict[str, Any]], score_threshold: float) -> Dict[str, Any]:
    total_samples = len(records)
    trial_counts = []
    extra_count = 0
    classification_distribution: Dict[str, int] = {}
    legacy_admit_count = 0
    stable_admit_count = 0
    rescued_by_stability = 0
    removed_by_stability = 0
    score_diffs = []

    for record in records:
        summary = record.get("round0_score_summary")
        summary = summary if isinstance(summary, dict) else {}
        status = str(summary.get("admission_status") or REVIEW_NEEDED)
        classification_distribution[status] = classification_distribution.get(status, 0) + 1

        try:
            trial_count = int(summary.get("trial_count") or 0)
        except (TypeError, ValueError):
            trial_count = 0
        trial_counts.append(trial_count)
        if bool(summary.get("needs_extra_trials")):
            extra_count += 1

        stable_score = _as_score(summary.get("stable_score"))
        stable_admitted = _stable_admitted(status)
        if stable_admitted:
            stable_admit_count += 1

        meta_info = record.get("meta_info")
        pre_score = None
        if isinstance(meta_info, dict):
            pre_score = _as_score(meta_info.get("pre_stability_score_rate"))
        if pre_score is not None:
            legacy_admitted = pre_score >= score_threshold
            if legacy_admitted:
                legacy_admit_count += 1
            if stable_score is not None:
                score_diffs.append(abs(pre_score - stable_score))
            if stable_admitted and not legacy_admitted:
                rescued_by_stability += 1
            if legacy_admitted and not stable_admitted:
                removed_by_stability += 1

    average_trial_count = statistics.fmean(trial_counts) if trial_counts else 0.0
    return {
        "total_samples": total_samples,
        "average_trial_count": _round_float(average_trial_count),
        "extra_trial_rate": _round_float(extra_count / total_samples) if total_samples else 0.0,
        "estimated_cost_per_100_samples": {
            "answer_calls": _round_float(average_trial_count * 100),
            "judge_calls": _round_float(average_trial_count * 100),
        },
        "classification_distribution": classification_distribution,
        "legacy_vs_stable_admission": {
            "legacy_admit_count": legacy_admit_count,
            "stable_admit_count": stable_admit_count,
            "rescued_by_stability": rescued_by_stability,
            "removed_by_stability": removed_by_stability,
        },
        "score_shift_summary": {
            "mean_abs_difference_between_legacy_score_and_stable_score": (
                _round_float(statistics.fmean(score_diffs)) if score_diffs else 0.0
            ),
            "max_difference": _round_float(max(score_diffs)) if score_diffs else 0.0,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run multi-trial stable round0 scoring.")
    parser.add_argument("--input", required=True, help="Input JSON/JSONL path.")
    parser.add_argument("--output", required=True, help="Output JSONL path.")
    parser.add_argument("--initial-trials", type=int, default=INITIAL_TRIALS)
    parser.add_argument("--extra-trials", type=int, default=EXTRA_TRIALS)
    parser.add_argument("--max-trials", type=int, default=MAX_TRIALS)
    parser.add_argument("--max-concurrent", "--concurrency", dest="max_concurrent", type=int, default=10)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--answer-mode", choices=["reference", "llm"], default="llm")
    parser.add_argument("--answer-model", default=ANSWER_MODEL)
    parser.add_argument("--answer-base-url", default=ANSWER_BASE_URL)
    parser.add_argument("--answer-api-key", default="")
    parser.add_argument("--answer-temperature", type=float, default=None)
    parser.add_argument("--answer-top-p", type=float, default=None)
    parser.add_argument("--answer-seed-base", type=int, default=None)
    parser.add_argument("--judge-model", default=JUDGE_MODEL)
    parser.add_argument("--judge-base-url", default=JUDGE_BASE_URL)
    parser.add_argument("--judge-api-key", action="append", default=None)
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--score-threshold", type=float, default=HIGH_SCORE_THRESHOLD)
    parser.add_argument("--strong-high-threshold", type=float, default=STRONG_HIGH_THRESHOLD)
    parser.add_argument("--borderline-low", type=float, default=BORDERLINE_LOW)
    parser.add_argument("--edge-low", type=float, default=EDGE_LOW)
    parser.add_argument("--edge-high", type=float, default=EDGE_HIGH)
    parser.add_argument("--cache-dir", default=None, help="Optional directory for per-trial round0 cache files.")
    parser.add_argument("--force", action="store_true", help="Regenerate trials even when cache entries exist.")
    parser.add_argument("--report-output", default=None, help="Optional JSON path for the stability summary report.")
    return parser.parse_args()


def validate_config(args: argparse.Namespace) -> None:
    if args.initial_trials < INITIAL_TRIALS:
        raise ValueError(f"--initial-trials must be >= {INITIAL_TRIALS}")
    if args.extra_trials < 0:
        raise ValueError("--extra-trials must be >= 0")
    if args.max_trials < args.initial_trials:
        raise ValueError("--max-trials must be >= --initial-trials")
    if args.answer_mode == "llm" and not (args.answer_base_url or "").strip():
        raise ValueError("--answer-base-url is required when --answer-mode llm")
    if args.answer_mode == "llm" and not (args.answer_model or "").strip():
        raise ValueError("--answer-model is required when --answer-mode llm")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    args = parse_args()
    validate_config(args)
    records = load_json_or_jsonl(args.input)

    answer_client = None
    answer_model_name = ""
    if args.answer_mode == "llm":
        answer_client = AnswerLLMClient(
            base_url=args.answer_base_url,
            api_key=resolve_answer_api_key(args.answer_api_key),
            model=args.answer_model,
            temperature=args.answer_temperature,
            top_p=args.answer_top_p,
        )
        answer_model_name = args.answer_model

    judge_client = RotatingAPIClient(
        base_url=args.judge_base_url,
        api_keys=parse_api_keys(args.judge_api_key),
    )
    processor = ScoringProcessor(
        judge_client=judge_client,
        judge_model=args.judge_model,
        answer_mode=args.answer_mode,
        max_concurrent=args.max_concurrent,
        max_retries=args.retries,
        answer_client=answer_client,
        answer_model_name=answer_model_name,
        force_generate_answer=True,
        judge_temperature=args.judge_temperature,
    )

    processed = await process_records(records, processor, args)
    write_jsonl(processed, args.output)
    report_output = args.report_output or f"{args.output}.report.json"
    write_json(build_stability_report(processed, args.score_threshold), report_output)
    logger.info("round0 stability scoring complete: %s", args.output)
    logger.info("round0 stability report complete: %s", report_output)


if __name__ == "__main__":
    asyncio.run(main())
