import argparse
import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pipeline_runtime import StageMetrics, load_json_records, publish_records


EVOLVE_HIGH_SCORE_OVERSCORE = "evolve_high_score_overscore"
RECONSTRUCT_LOW_SCORE_BOUNDARY = "reconstruct_low_score_boundary"
PROBE_MIDDLE_SCORE_BOUNDARY = "probe_middle_score_boundary"
PASS_THROUGH_OR_SCORING_NOISE = "pass_through_or_scoring_noise"
STOP_EVOLUTION = "stop_evolution"

EVOLUTION_ACTIONS = {
    EVOLVE_HIGH_SCORE_OVERSCORE,
    RECONSTRUCT_LOW_SCORE_BOUNDARY,
    PROBE_MIDDLE_SCORE_BOUNDARY,
    PASS_THROUGH_OR_SCORING_NOISE,
    STOP_EVOLUTION,
}

LOW_SCORE_BOUNDARY_TERMS = (
    "低分真实边界",
    "真实边界",
    "主线抓偏",
    "主线切换",
    "反常线索",
    "抓偏",
    "边界重构",
)
SCORING_NOISE_TERMS = (
    "评分噪声",
    "打分噪声",
    "rubric噪声",
    "rubric 噪声",
    "关键词",
    "格式",
    "负向项",
)
STOP_TERMS = (
    "停止",
    "无需进化",
    "基础边界判断过稳",
    "稳定满分",
    "已稳定",
)
STOP_STATUSES = {
    "stable_high_score_stop",
    "effective_boundary_sample",
    "validated_high_score_sample",
    "invalid_complexity_sample",
    "unanswerable_or_trap_sample",
    "stop_evolution",
}
ROUND0_EVOLVE_HIGH_STATUSES = {"stable_high", "unstable_high"}
ROUND0_BORDERLINE_STATUSES = {"borderline_probe"}
ROUND0_STOP_STATUSES = {"stable_low", "uncertain_low", "review_needed"}
ENABLE_UNCERTAIN_LOW_PROBE = str(os.getenv("ENABLE_UNCERTAIN_LOW_PROBE", "false")).strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
try:
    UNCERTAIN_LOW_PROBE_MIN_SCORE = float(os.getenv("UNCERTAIN_LOW_PROBE_MIN_SCORE", "0.55"))
except ValueError:
    UNCERTAIN_LOW_PROBE_MIN_SCORE = 0.55


def load_json_or_jsonl(input_path: str) -> List[Dict[str, Any]]:
    return load_json_records(input_path, stage="select_evolution_candidates")


def write_jsonl(records: Iterable[Dict[str, Any]], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def coerce_score_rate(value: Any) -> Optional[float]:
    try:
        score_rate = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= score_rate <= 1:
        return score_rate
    return None


def get_score_rate(item: Dict[str, Any]) -> Optional[float]:
    top_level = coerce_score_rate(item.get("score_rate"))
    if top_level is not None:
        return top_level

    scoring_result = item.get("scoring_result")
    if not isinstance(scoring_result, dict):
        return None

    try:
        awarded = float(scoring_result.get("total_awarded", 0) or 0)
        possible = float(scoring_result.get("total_possible", 0) or 0)
    except (TypeError, ValueError):
        return None
    if possible <= 0:
        return None
    return awarded / possible


def _joined_diagnosis_text(item: Dict[str, Any]) -> str:
    diagnosis = item.get("overscore_diagnosis")
    if not isinstance(diagnosis, dict):
        return ""
    return " ".join(
        str(diagnosis.get(field, ""))
        for field in (
            "candidate_overscore_cause",
            "target_failure_mode",
            "why_high_score_is_suspicious",
        )
    )


def _has_any_term(text: str, terms: Tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _stop_status(item: Dict[str, Any]) -> str:
    state = item.get("evolution_state")
    if not isinstance(state, dict):
        return ""
    return str(state.get("stop_status", "")).strip()


def get_round0_summary(item: Dict[str, Any]) -> Dict[str, Any]:
    summary = item.get("round0_score_summary")
    return summary if isinstance(summary, dict) else {}


def decide_action_from_round0_summary(
    item: Dict[str, Any],
    *,
    enable_uncertain_low_probe: bool = ENABLE_UNCERTAIN_LOW_PROBE,
    uncertain_low_probe_min_score: float = UNCERTAIN_LOW_PROBE_MIN_SCORE,
) -> Optional[Tuple[str, str]]:
    summary = get_round0_summary(item)
    if not summary:
        return None

    status = str(summary.get("admission_status", "") or "").strip()
    stable_score = coerce_score_rate(summary.get("stable_score"))
    admission_score = coerce_score_rate(summary.get("admission_score"))
    volatility_level = str(summary.get("volatility_level", "") or "").strip()
    score_bits = []
    if stable_score is not None:
        score_bits.append(f"stable_score={stable_score:.4f}")
    if admission_score is not None:
        score_bits.append(f"admission_score={admission_score:.4f}")
    if volatility_level:
        score_bits.append(f"volatility={volatility_level}")
    score_text = ", ".join(score_bits) if score_bits else "round0 summary present"

    if status in ROUND0_EVOLVE_HIGH_STATUSES:
        return EVOLVE_HIGH_SCORE_OVERSCORE, f"round0 admission_status={status} ({score_text}) admits high-score evolution."

    if status in ROUND0_BORDERLINE_STATUSES:
        return RECONSTRUCT_LOW_SCORE_BOUNDARY, f"round0 admission_status={status} ({score_text}) admits a low-budget boundary probe."

    if status == "uncertain_low":
        diagnosis = item.get("overscore_diagnosis")
        diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
        if (
            enable_uncertain_low_probe
            and bool(diagnosis.get("is_worth_evolving"))
            and stable_score is not None
            and stable_score >= uncertain_low_probe_min_score
        ):
            return (
                RECONSTRUCT_LOW_SCORE_BOUNDARY,
                f"uncertain_low_probe enabled: stable_score={stable_score:.4f} >= {uncertain_low_probe_min_score:.4f}; low-budget probe admitted.",
            )

    if status in ROUND0_STOP_STATUSES:
        return STOP_EVOLUTION, f"round0 admission_status={status} ({score_text}) stops evolution."

    return STOP_EVOLUTION, f"round0 admission_status={status or '<missing>'} ({score_text}) is not recognized; stop for review."


def build_evolution_budget(
    item: Dict[str, Any],
    *,
    action: Optional[str] = None,
    enable_uncertain_low_probe: bool = ENABLE_UNCERTAIN_LOW_PROBE,
    uncertain_low_probe_min_score: float = UNCERTAIN_LOW_PROBE_MIN_SCORE,
) -> Optional[Dict[str, Any]]:
    summary = get_round0_summary(item)
    if not summary:
        return None
    try:
        budget = int(summary.get("recommended_evolution_budget") or 0)
    except (TypeError, ValueError):
        budget = 0
    status = str(summary.get("admission_status", "") or "")
    stable_score = coerce_score_rate(summary.get("stable_score"))
    diagnosis = item.get("overscore_diagnosis")
    diagnosis = diagnosis if isinstance(diagnosis, dict) else {}
    if (
        action == RECONSTRUCT_LOW_SCORE_BOUNDARY
        and status == "uncertain_low"
        and enable_uncertain_low_probe
        and bool(diagnosis.get("is_worth_evolving"))
        and stable_score is not None
        and stable_score >= uncertain_low_probe_min_score
    ):
        budget = max(1, budget)
    return {
        "recommended_num_candidates": max(0, budget),
        "source": "round0_score_summary",
        "admission_status": status,
    }

def _recommended_next_methods(item: Dict[str, Any]) -> List[str]:
    state = item.get("evolution_state")
    if not isinstance(state, dict):
        return []
    values = state.get("recommended_next_methods")
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def validate_profiled_record(item: Dict[str, Any]) -> None:
    if not isinstance(item.get("sample_profile"), dict):
        raise ValueError("record missing sample_profile; run profile_samples.py first")
    if not isinstance(item.get("overscore_diagnosis"), dict):
        raise ValueError("record missing overscore_diagnosis; run profile_samples.py first")


def decide_evolution_action(
    item: Dict[str, Any],
    *,
    high_score_threshold: float = 0.8,
    low_score_threshold: float = 0.6,
    enable_uncertain_low_probe: bool = ENABLE_UNCERTAIN_LOW_PROBE,
    uncertain_low_probe_min_score: float = UNCERTAIN_LOW_PROBE_MIN_SCORE,
) -> Tuple[str, str]:
    validate_profiled_record(item)

    diagnosis = item["overscore_diagnosis"]
    worth_evolving = bool(diagnosis.get("is_worth_evolving"))
    score_rate = get_score_rate(item)
    diagnosis_text = _joined_diagnosis_text(item)
    stop_status = _stop_status(item)
    recommended_next = _recommended_next_methods(item)

    if stop_status in STOP_STATUSES:
        return STOP_EVOLUTION, f"evolution_state.stop_status={stop_status} indicates a terminal state."

    round0_decision = decide_action_from_round0_summary(
        item,
        enable_uncertain_low_probe=enable_uncertain_low_probe,
        uncertain_low_probe_min_score=uncertain_low_probe_min_score,
    )
    if round0_decision is not None:
        return round0_decision
    if score_rate is None:
        return PASS_THROUGH_OR_SCORING_NOISE, "score_rate is missing or invalid."

    # 跨轮状态优先于当前分数区间。上一轮已经要求换算子、局部探索或回滚重路由时，
    # 不能因为当前分数落在中间区间而把样本意外透传。
    if recommended_next or stop_status in {
        "continue_with_new_operator",
        "local_tree_search_needed",
        "rollback_and_reroute",
    }:
        if score_rate >= high_score_threshold:
            action = EVOLVE_HIGH_SCORE_OVERSCORE
        elif (
            score_rate <= low_score_threshold
            and worth_evolving
            and _has_any_term(diagnosis_text, LOW_SCORE_BOUNDARY_TERMS)
        ):
            action = RECONSTRUCT_LOW_SCORE_BOUNDARY
        else:
            action = PROBE_MIDDLE_SCORE_BOUNDARY
        return action, (
            f"evolution_state requests continued exploration "
            f"(stop_status={stop_status or 'continue'}, recommended_next_methods={recommended_next})."
        )

    if _has_any_term(diagnosis_text, STOP_TERMS) and not worth_evolving:
        return STOP_EVOLUTION, "diagnosis says the sample is stable or should stop."

    if score_rate >= high_score_threshold:
        if worth_evolving:
            return EVOLVE_HIGH_SCORE_OVERSCORE, (
                f"score_rate={score_rate:.4f} is high and diagnosis marks the score as worth evolving."
            )
        return PASS_THROUGH_OR_SCORING_NOISE, (
            f"score_rate={score_rate:.4f} is high but diagnosis does not mark a useful overscore."
        )

    if score_rate <= low_score_threshold:
        if worth_evolving and _has_any_term(diagnosis_text, LOW_SCORE_BOUNDARY_TERMS):
            return RECONSTRUCT_LOW_SCORE_BOUNDARY, (
                f"score_rate={score_rate:.4f} is low and diagnosis indicates a real boundary signal."
            )
        if _has_any_term(diagnosis_text, SCORING_NOISE_TERMS):
            return PASS_THROUGH_OR_SCORING_NOISE, "low score appears tied to scoring noise or formatting."
        return PASS_THROUGH_OR_SCORING_NOISE, (
            f"score_rate={score_rate:.4f} is low but diagnosis does not justify boundary reconstruction."
        )

    if worth_evolving:
        return PROBE_MIDDLE_SCORE_BOUNDARY, (
            f"score_rate={score_rate:.4f} is in the middle band and diagnosis identifies a probeable boundary."
        )
    return PASS_THROUGH_OR_SCORING_NOISE, "diagnosis does not mark this sample as worth evolving."


def select_record(
    item: Dict[str, Any],
    *,
    high_score_threshold: float = 0.8,
    low_score_threshold: float = 0.6,
    enable_uncertain_low_probe: bool = ENABLE_UNCERTAIN_LOW_PROBE,
    uncertain_low_probe_min_score: float = UNCERTAIN_LOW_PROBE_MIN_SCORE,
) -> Dict[str, Any]:
    action, reason = decide_evolution_action(
        item,
        high_score_threshold=high_score_threshold,
        low_score_threshold=low_score_threshold,
        enable_uncertain_low_probe=enable_uncertain_low_probe,
        uncertain_low_probe_min_score=uncertain_low_probe_min_score,
    )
    result = dict(item)
    result["evolution_action"] = action
    result["evolution_action_reason"] = reason
    evolution_budget = build_evolution_budget(
        item,
        action=action,
        enable_uncertain_low_probe=enable_uncertain_low_probe,
        uncertain_low_probe_min_score=uncertain_low_probe_min_score,
    )
    if evolution_budget is not None:
        result["evolution_budget"] = evolution_budget
    return result


def process_records(
    records: List[Dict[str, Any]],
    *,
    high_score_threshold: float = 0.8,
    low_score_threshold: float = 0.6,
    enable_uncertain_low_probe: bool = ENABLE_UNCERTAIN_LOW_PROBE,
    uncertain_low_probe_min_score: float = UNCERTAIN_LOW_PROBE_MIN_SCORE,
) -> List[Dict[str, Any]]:
    return [
        select_record(
            record,
            high_score_threshold=high_score_threshold,
            low_score_threshold=low_score_threshold,
            enable_uncertain_low_probe=enable_uncertain_low_probe,
            uncertain_low_probe_min_score=uncertain_low_probe_min_score,
        )
        for record in records
    ]


def generate_report(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    uncertain_low_probe_count = 0
    action_distribution: Dict[str, int] = {}
    for record in records:
        action = str(record.get("evolution_action", "") or "")
        action_distribution[action] = action_distribution.get(action, 0) + 1
        summary = get_round0_summary(record)
        if (
            summary.get("admission_status") == "uncertain_low"
            and action == RECONSTRUCT_LOW_SCORE_BOUNDARY
            and "uncertain_low_probe enabled" in str(record.get("evolution_action_reason", ""))
        ):
            uncertain_low_probe_count += 1
    return {
        "total_records": len(records),
        "action_distribution": action_distribution,
        "uncertain_low_probe_count": uncertain_low_probe_count,
    }


def write_json(data: Dict[str, Any], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Assign evolution_action for profiled samples.")
    parser.add_argument("--input", required=True, help="Input profiled JSON/JSONL path.")
    parser.add_argument("--output", required=True, help="Output profiled_candidates JSONL path.")
    parser.add_argument(
        "--high-score-threshold",
        type=float,
        default=0.8,
        help="Minimum score_rate for high-score overscore evolution.",
    )
    parser.add_argument(
        "--low-score-threshold",
        type=float,
        default=0.6,
        help="Maximum score_rate for low-score boundary reconstruction.",
    )
    parser.add_argument(
        "--enable-uncertain-low-probe",
        action="store_true",
        default=ENABLE_UNCERTAIN_LOW_PROBE,
        help="Allow low-budget probing for uncertain_low round0 samples when they are worth evolving.",
    )
    parser.add_argument(
        "--uncertain-low-probe-min-score",
        type=float,
        default=UNCERTAIN_LOW_PROBE_MIN_SCORE,
        help="Minimum stable_score for uncertain_low_probe when enabled.",
    )
    parser.add_argument("--report-output", default=None, help="Optional evolution candidate selection report JSON path.")
    parser.add_argument("--performance-events", default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stage = "select_evolution_candidates"
    metrics = StageMetrics(stage)
    metrics.input_bytes = os.path.getsize(args.input)
    parse_started = time.monotonic()
    records = load_json_or_jsonl(args.input)
    metrics.parse_seconds += time.monotonic() - parse_started
    compute_started = time.monotonic()
    selected = process_records(
        records,
        high_score_threshold=args.high_score_threshold,
        low_score_threshold=args.low_score_threshold,
        enable_uncertain_low_probe=args.enable_uncertain_low_probe,
        uncertain_low_probe_min_score=args.uncertain_low_probe_min_score,
    )
    metrics.compute_seconds += time.monotonic() - compute_started
    publish_records(
        selected,
        args.output,
        stage=stage,
        input_path=args.input,
        config={
            "high_score_threshold": args.high_score_threshold,
            "low_score_threshold": args.low_score_threshold,
            "enable_uncertain_low_probe": args.enable_uncertain_low_probe,
            "uncertain_low_probe_min_score": args.uncertain_low_probe_min_score,
        },
        performance_path=args.performance_events,
        code_paths=[__file__],
        metrics=metrics,
    )
    if args.report_output:
        write_json(generate_report(selected), args.report_output)


if __name__ == "__main__":
    main()
