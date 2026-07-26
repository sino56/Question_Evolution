"""Aggregate repeated search performance runs into median and ranges."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence


METRICS = (
    "branches_completed_per_wall_clock_hour",
    "decision_evaluations_completed_per_wall_clock_hour",
    "boundary_candidates_per_wall_clock_hour",
    "p50_branch_latency",
    "p95_branch_latency",
    "p50_sample_termination_latency",
    "p95_sample_termination_latency",
    "request_pool_utilization",
    "retry_rate",
)


def aggregate_performance_runs(runs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if len(runs) < 3:
        raise ValueError("performance acceptance requires at least three runs")
    result: Dict[str, Any] = {
        "run_count": len(runs),
        "metrics": {},
    }
    for metric in METRICS:
        values: List[float] = []
        for run in runs:
            value = run.get(metric)
            if value is None:
                continue
            try:
                values.append(float(value))
            except (TypeError, ValueError):
                continue
        result["metrics"][metric] = {
            "median": statistics.median(values) if values else None,
            "min": min(values) if values else None,
            "max": max(values) if values else None,
            "values": values,
        }
    error_rates = []
    for run in runs:
        try:
            error_rates.append(float(run.get("model_error_rate") or 0))
        except (TypeError, ValueError):
            pass
    result["model_error_rate"] = {
        "median": statistics.median(error_rates) if error_rates else None,
        "min": min(error_rates) if error_rates else None,
        "max": max(error_rates) if error_rates else None,
    }
    return result


def build_comparison_report(
    baseline_runs: Sequence[Mapping[str, Any]],
    optimized_runs: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    baseline = aggregate_performance_runs(baseline_runs)
    optimized = aggregate_performance_runs(optimized_runs)
    speedups: Dict[str, Any] = {}
    for metric in (
        "branches_completed_per_wall_clock_hour",
        "decision_evaluations_completed_per_wall_clock_hour",
        "boundary_candidates_per_wall_clock_hour",
    ):
        baseline_median = baseline["metrics"][metric]["median"]
        optimized_median = optimized["metrics"][metric]["median"]
        speedups[metric] = (
            optimized_median / baseline_median
            if baseline_median not in {None, 0} and optimized_median is not None
            else None
        )
    return {
        "baseline": baseline,
        "optimized": optimized,
        "median_speedup": speedups,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate at least three search performance summaries.")
    parser.add_argument("--input", action="append")
    parser.add_argument("--baseline", action="append")
    parser.add_argument("--optimized", action="append")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.input and (args.baseline or args.optimized):
        parser.error("--input cannot be combined with --baseline/--optimized")
    if not args.input and not (args.baseline and args.optimized):
        parser.error("provide --input, or both --baseline and --optimized")
    return args


def main() -> None:
    args = parse_args()
    if args.input:
        runs = [
            json.loads(Path(path).read_text(encoding="utf-8"))
            for path in args.input
        ]
        report = aggregate_performance_runs(runs)
    else:
        baseline_runs = [
            json.loads(Path(path).read_text(encoding="utf-8"))
            for path in args.baseline
        ]
        optimized_runs = [
            json.loads(Path(path).read_text(encoding="utf-8"))
            for path in args.optimized
        ]
        report = build_comparison_report(baseline_runs, optimized_runs)
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
