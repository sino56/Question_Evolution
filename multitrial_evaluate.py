"""Repeated weak-model answer-and-score evaluation for prepared JSONL data.

The default input is the four-scenario test set.  Model settings are read via
``local_api_config.py``, which loads the ignored local ``config.py`` without
printing or storing its credentials.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from local_api_config import get_config_value
from pipeline_runtime import (
    AtomicJsonlStageWriter,
    StageMetrics,
    append_performance_event,
    bounded_async_map,
    iter_json_records,
    load_json_records,
    validate_published_artifact,
)
from scoring import AnswerLLMClient, RotatingAPIClient, ScoringProcessor, compute_score_rate, parse_api_keys


logger = logging.getLogger(__name__)

STAGE = "multitrial_evaluation"
EVALUATION_VERSION = "multitrial_evaluation_v1"
DEFAULT_INPUT = os.path.join("data", "四大场景测试样本.jsonl")


def _round(value: float) -> float:
    return round(float(value), 6)


def _score_rate(trial: Mapping[str, Any]) -> Optional[float]:
    value = trial.get("score_rate")
    try:
        score = float(value)
    except (TypeError, ValueError):
        score = None
    if score is not None and 0.0 <= score <= 1.0:
        return score
    result = trial.get("scoring_result")
    return compute_score_rate(result) if isinstance(result, dict) else None


def summarize_trials(trials: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    scores = [score for score in (_score_rate(trial) for trial in trials) if score is not None]
    if not scores or len(scores) != len(trials):
        raise ValueError("存在未完成评分的 trial")
    median = float(statistics.median(scores))
    representative = min(
        trials,
        key=lambda trial: (abs(float(_score_rate(trial) or 0.0) - median), int(trial["trial_id"])),
    )
    return {
        "requested_trial_count": len(trials),
        "completed_trial_count": len(scores),
        "failed_trial_count": 0,
        "score_aggregation": "median",
        "score_mean": _round(statistics.fmean(scores)),
        "score_median": _round(median),
        "score_std": _round(statistics.pstdev(scores)) if len(scores) > 1 else 0.0,
        "score_min": _round(min(scores)),
        "score_max": _round(max(scores)),
        "score_range": _round(max(scores) - min(scores)),
        "representative_trial_id": representative["trial_id"],
    }


async def evaluate_item(
    item: Mapping[str, Any],
    processor: Any,
    *,
    trials: int,
    configuration: Mapping[str, Any],
) -> Dict[str, Any]:
    """Always generate fresh answers, including pass-through control rows."""

    result = deepcopy(dict(item))
    if not isinstance(result.get("prompt"), str) or not result["prompt"].strip():
        raise ValueError("输入数据缺少非空 prompt")

    rows: List[Dict[str, Any]] = []
    for trial_id in range(1, trials + 1):
        trial_item = deepcopy(result)
        answer = await processor.generate_candidate_answer_with_retry(trial_item)
        scored = await processor.score_candidate_answer(
            trial_item,
            answer,
            trial_index=trial_id,
            answer_source="qwen",
            score_with_qwen=True,
            score_with_gpt=False,
        )
        score = compute_score_rate(scored)
        if score is None:
            raise ValueError(f"trial {trial_id} 的评分结果缺少有效总分")
        rows.append(
            {
                "trial_id": trial_id,
                "candidate_answer": answer,
                "scoring_result": scored,
                "score_rate": score,
            }
        )

    summary = summarize_trials(rows)
    representative = next(row for row in rows if row["trial_id"] == summary["representative_trial_id"])
    result["candidate_answer"] = representative["candidate_answer"]
    result["scoring_result"] = deepcopy(representative["scoring_result"])
    result["score_rate"] = summary["score_median"]
    result["multi_trial_evaluation"] = {
        "evaluation_version": EVALUATION_VERSION,
        "configuration": dict(configuration),
        **summary,
        "trials": rows,
    }
    return result


def _record_key(sequence: int, item: Mapping[str, Any]) -> str:
    """Do not merge separate samples just because their prompts are equal."""

    identity = item.get("candidate_id") or item.get("sample_id") or item.get("index") or "missing-id"
    return f"{sequence}:{identity}"


def allocate_experiment_dir(experiment_root: str, run_date: Optional[str] = None) -> str:
    """Allocate experiments/YYYY-MM-DD/exp, exp1, exp2, ... in order."""

    date_text = run_date or datetime.now().strftime("%Y-%m-%d")
    day_dir = os.path.join(experiment_root, date_text)
    os.makedirs(day_dir, exist_ok=True)
    suffix = 0
    while True:
        name = "exp" if suffix == 0 else f"exp{suffix}"
        candidate = os.path.join(day_dir, name)
        try:
            os.mkdir(candidate)
            return candidate
        except FileExistsError:
            suffix += 1


def find_resumable_output(input_path: str, experiment_root: str, run_date: Optional[str] = None) -> Optional[str]:
    """Return the latest incomplete output for this input, if one exists.

    ``AtomicJsonlStageWriter`` uses a ``.partial`` file plus a checkpoint file.
    Reusing that exact path lets it skip already checkpointed records after an
    interrupted run or a temporary provider/quota failure.
    """

    date_text = run_date or datetime.now().strftime("%Y-%m-%d")
    day_dir = os.path.join(experiment_root, date_text)
    if not os.path.isdir(day_dir):
        return None
    _, filename = os.path.split(input_path)
    stem, _ = os.path.splitext(filename)
    output_name = f"{stem}_multitrial_scored.jsonl"

    candidates = []
    for name in os.listdir(day_dir):
        if name == "exp":
            suffix = 0
        elif name.startswith("exp") and name[3:].isdigit():
            suffix = int(name[3:])
        else:
            continue
        directory = os.path.join(day_dir, name)
        if os.path.isdir(directory):
            candidates.append((suffix, directory))
    for _, directory in sorted(candidates, reverse=True):
        output_path = os.path.join(directory, output_name)
        partial_path = output_path + ".partial"
        checkpoint_path = output_path + ".checkpoint.jsonl"
        if not os.path.exists(output_path) and os.path.isfile(partial_path) and os.path.isfile(checkpoint_path):
            return output_path
    return None


def _default_output(input_path: str, experiment_root: str) -> str:
    resumable_output = find_resumable_output(input_path, experiment_root)
    if resumable_output:
        return resumable_output
    directory = allocate_experiment_dir(experiment_root)
    _, filename = os.path.split(input_path)
    stem, _ = os.path.splitext(filename)
    return os.path.join(directory, f"{stem}_multitrial_scored.jsonl")


def _write_failed(output_path: str, failures: Sequence[Mapping[str, Any]]) -> None:
    with open(output_path + ".failed", "w", encoding="utf-8") as target:
        for record in failures:
            target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


def _build_report(records: Sequence[Mapping[str, Any]], trials: int) -> Dict[str, Any]:
    scores: List[float] = []
    completed_trials = 0
    for record in records:
        evaluation = record.get("multi_trial_evaluation")
        if not isinstance(evaluation, Mapping):
            continue
        completed_trials += int(evaluation.get("completed_trial_count") or 0)
        score = evaluation.get("score_median")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            scores.append(float(score))
    return {
        "evaluation_version": EVALUATION_VERSION,
        "sample_count": len(records),
        "requested_trials_per_sample": trials,
        "completed_trial_count": completed_trials,
        "aggregate_score_mean": _round(statistics.fmean(scores)) if scores else None,
        "aggregate_score_median": _round(statistics.median(scores)) if scores else None,
        "aggregate_score_min": _round(min(scores)) if scores else None,
        "aggregate_score_max": _round(max(scores)) if scores else None,
    }


def _resolve_config(args: argparse.Namespace) -> Dict[str, str]:
    """Use the same weak-model configuration precedence as run_loop.ps1."""

    config_base_url = get_config_value("BASE_URL", "OPENAI_BASE_URL", default="")
    config_qwen_url = get_config_value("QWEN_BASE_URL", default=config_base_url)
    model = (
        args.model
        or os.getenv("WEAK_ANSWER_MODEL")
        or get_config_value("WEAK_ANSWER_MODEL", "QWEN_MODEL", "GPT_MODEL", default="")
    )
    base_url = (
        args.base_url
        or os.getenv("WEAK_ANSWER_BASE_URL")
        or get_config_value(
            "WEAK_ANSWER_BASE_URL",
            "QWEN_BASE_URL",
            "BASE_URL",
            "OPENAI_BASE_URL",
            default=config_qwen_url,
        )
    )
    api_key = (
        args.api_key
        or os.getenv("WEAK_ANSWER_API_KEY")
        or get_config_value("WEAK_ANSWER_API_KEY", "QWEN_API_KEY", default="")
    )
    if not model:
        raise ValueError("未从 config.py / WEAK_ANSWER_MODEL 读取到弱模型名称")
    if not base_url:
        raise ValueError("未从 config.py / WEAK_ANSWER_BASE_URL 读取到弱模型 Base URL")
    return {"model": model, "base_url": base_url, "api_key": api_key}


async def run(args: argparse.Namespace) -> None:
    if args.trials < 1 or args.concurrency < 1 or args.request_concurrency < 1:
        raise ValueError("trials、concurrency 和 request-concurrency 必须 >= 1")
    if args.judge_repeats < 1 or args.retries < 0:
        raise ValueError("judge-repeats 必须 >= 1，retries 必须 >= 0")

    input_path = os.path.abspath(args.input)
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"输入文件不存在: {input_path}")
    output_path = os.path.abspath(args.output or _default_output(input_path, args.experiment_root))
    runtime = _resolve_config(args)
    configuration = {
        "answer_model": runtime["model"],
        "judge_model": runtime["model"],
        "trials": args.trials,
        "judge_repeats": args.judge_repeats,
        "answer_temperature": args.answer_temperature,
        "answer_top_p": args.answer_top_p,
        "judge_temperature": args.judge_temperature,
    }
    valid, _ = validate_published_artifact(output_path, stage=STAGE, input_path=input_path, config=configuration)
    if valid:
        logger.info("已验证输出存在，跳过: %s", output_path)
        return

    metrics = StageMetrics(STAGE)
    metrics.input_bytes = os.path.getsize(input_path)
    answer_client = None
    judge_client = None
    writer = None
    failures: List[Dict[str, Any]] = []
    try:
        answer_client = AnswerLLMClient(
            base_url=runtime["base_url"],
            api_key=runtime["api_key"],
            model=runtime["model"],
            temperature=args.answer_temperature,
            top_p=args.answer_top_p,
        )
        judge_client = RotatingAPIClient(
            base_url=runtime["base_url"],
            api_keys=parse_api_keys([runtime["api_key"]] if runtime["api_key"] else None),
        )
        processor = ScoringProcessor(
            judge_client=judge_client,
            judge_model=runtime["model"],
            answer_mode="llm",
            max_concurrent=args.concurrency,
            max_retries=args.retries,
            answer_client=answer_client,
            answer_model_name=runtime["model"],
            force_generate_answer=True,
            judge_temperature=args.judge_temperature,
            answer_trials=1,
            gpt_answer_trials=0,
            qwen_judge_repeats=args.judge_repeats,
            gpt_judge_repeats=0,
            gpt_score_qwen_answers=False,
            qwen_max_concurrent=args.request_concurrency,
            gpt_max_concurrent=1,
        )
        writer = AtomicJsonlStageWriter(
            output_path,
            stage=STAGE,
            input_path=input_path,
            config=configuration,
            code_paths=[__file__],
            metrics=metrics,
        )

        async def worker(entry: tuple[int, Dict[str, Any]]) -> Dict[str, Any]:
            sequence, item = entry
            try:
                return {
                    "sequence": sequence,
                    "result": await evaluate_item(item, processor, trials=args.trials, configuration=configuration),
                }
            except Exception as exc:
                failed = deepcopy(item)
                failed["multitrial_evaluation_error"] = str(exc)
                return {"sequence": sequence, "failed": failed}

        def pending() -> Iterable[tuple[int, Dict[str, Any]]]:
            for sequence, item in enumerate(iter_json_records(input_path, stage=STAGE)):
                if _record_key(sequence, item) not in writer.processed_keys:
                    yield sequence, item

        async def write_result(_sequence: int, entry: tuple[int, Dict[str, Any]], outcome: Dict[str, Any]) -> None:
            sequence, item = entry
            if "failed" in outcome:
                failures.append(outcome["failed"])
            else:
                writer.add_group(_record_key(sequence, item), [outcome["result"]])

        await bounded_async_map(pending(), worker, concurrency=args.concurrency, on_result=write_result, metrics=metrics)
        metrics.request_pool_peaks = {"weak_model": processor.qwen_request_pool.peak_active}
        if failures:
            writer.close()
            _write_failed(output_path, failures)
            append_performance_event(args.performance_events, metrics.event(status="failed"))
            raise RuntimeError(f"多次评测有 {len(failures)} 条失败；详情见 {output_path}.failed")
        writer.publish()
        report_path = args.report_output or f"{output_path}.report.json"
        with open(report_path, "w", encoding="utf-8") as target:
            json.dump(_build_report(load_json_records(output_path, stage=STAGE), args.trials), target, ensure_ascii=False, indent=2)
            target.write("\n")
        append_performance_event(args.performance_events, metrics.event())
        logger.info("多次评测完成: %s", output_path)
    except BaseException:
        if writer is not None:
            writer.close()
        raise
    finally:
        if judge_client is not None:
            await judge_client.close()
        if answer_client is not None:
            await answer_client.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="对 JSONL 数据进行多次弱模型回答和评分")
    parser.add_argument("--input", default=DEFAULT_INPUT, help=f"输入 JSONL，默认: {DEFAULT_INPUT}")
    parser.add_argument("--output", default="", help="可选：直接指定输出 JSONL 路径")
    parser.add_argument(
        "--experiment-root",
        default="experiments",
        help="未指定 --output 时的实验根目录；会优先恢复当天最新未完成实验",
    )
    parser.add_argument("--trials", type=int, default=3, help="每条题目重新回答和评分的次数")
    parser.add_argument("--judge-repeats", type=int, default=2, help="每个回答的重复评分次数")
    parser.add_argument("--concurrency", type=int, default=4, help="默认 4，低于 StepFun 当前并发上限")
    parser.add_argument("--request-concurrency", type=int, default=4, help="默认 4，避免触发 StepFun 并发限流")
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--model", default="", help="可选覆盖 config.py 的 StepFun 弱模型名称")
    parser.add_argument("--base-url", default="", help="可选覆盖 config.py 的 StepFun Base URL")
    parser.add_argument("--api-key", default="", help="可选覆盖 config.py 的 API Key；不会写入输出")
    parser.add_argument("--answer-temperature", type=float, default=None)
    parser.add_argument("--answer-top-p", type=float, default=None)
    parser.add_argument("--judge-temperature", type=float, default=0.0)
    parser.add_argument("--report-output", default=None)
    parser.add_argument("--performance-events", default=None)
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    asyncio.run(run(parse_args()))


if __name__ == "__main__":
    main()
