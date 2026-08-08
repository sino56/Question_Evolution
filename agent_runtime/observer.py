"""Read-only summaries of experiment artifacts and local M1 memory."""

from __future__ import annotations

import json
import hashlib
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from pipeline_runtime import StageJsonError, load_json_records, validate_published_artifact


OBSERVATION_TYPES = {
    "environment_ready", "pipeline_started", "pipeline_completed", "candidate_invalid",
    "score_decreased", "score_unchanged", "score_increased", "not_applicable",
    "boundary_candidate_found", "budget_warning", "tool_retryable_failure",
    "tool_fatal_failure", "artifact_missing", "manifest_corrupted", "review_report_ready",
}


def _observation(
    source_tool: str,
    observation_type: str,
    summary: str,
    *,
    severity: str = "info",
    evidence_refs: Iterable[Mapping[str, Any]] = (),
    metrics: Mapping[str, Any] | None = None,
    recommended_actions: Iterable[str] = (),
    requires_replan: bool = False,
    requires_human_review: bool = False,
) -> Dict[str, Any]:
    if observation_type not in OBSERVATION_TYPES:
        raise ValueError(f"unsupported observation type: {observation_type}")
    evidence = [dict(item) for item in evidence_refs][:40]
    seed = json.dumps({"source_tool": source_tool, "type": observation_type, "summary": summary, "evidence": evidence}, ensure_ascii=False, sort_keys=True)
    return {
        "observation_id": "obs-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16],
        "source_tool": source_tool,
        "type": observation_type,
        "severity": severity,
        "summary": summary,
        "evidence_refs": evidence,
        "metrics": dict(metrics or {}),
        "recommended_actions": list(recommended_actions),
        "requires_replan": requires_replan,
        "requires_human_review": requires_human_review,
    }


def normalize_tool_result(tool_result: Mapping[str, Any], *, experiment_observation: Mapping[str, Any] | None = None) -> List[Dict[str, Any]]:
    """Convert tool-specific results into the Phase-3 stable Observation API."""

    tool = str(tool_result.get("tool") or "unknown_tool")
    if not tool_result.get("ok", False) and not (tool == "observe_experiment" and experiment_observation is not None):
        retryable = bool(tool_result.get("recoverable"))
        return [_observation(
            tool,
            "tool_retryable_failure" if retryable else "tool_fatal_failure",
            str(tool_result.get("artifact_validation") or tool_result.get("stderr_summary") or tool_result.get("failure_category") or "registered tool failed"),
            severity="warning" if retryable else "error",
            metrics={"return_code": tool_result.get("return_code"), "retry_count": tool_result.get("retry_count", 0), "duration_seconds": tool_result.get("duration_seconds", 0)},
            recommended_actions=["retry_or_suspend"] if retryable else ["block_and_report"],
            requires_human_review=not retryable,
        )]
    if tool == "check_environment":
        return [_observation("check_environment", "environment_ready", "runtime preflight completed", metrics={"ready": bool(tool_result.get("ready"))})]
    if tool in {"run_full_loop", "resume_full_loop"}:
        return [
            _observation(tool, "pipeline_started", "registered pipeline invocation started", metrics={"experiment_dir": tool_result.get("experiment_dir")}),
            _observation(tool, "pipeline_completed", "registered pipeline invocation returned published artifacts", metrics={"duration_seconds": tool_result.get("duration_seconds", 0), "retry_count": tool_result.get("retry_count", 0)}),
        ]
    if tool == "write_agent_report":
        return [_observation(tool, "review_report_ready", "agent report written", metrics={"report_path": tool_result.get("report_path")})]
    aggregate = experiment_observation or tool_result.get("observation") or {}
    observations: List[Dict[str, Any]] = []
    evidence = aggregate.get("evidence_refs") or []
    counts = {
        "score_decreased": int(aggregate.get("status_counts", {}).get("score_decreased", 0)),
        "score_unchanged": int(aggregate.get("status_counts", {}).get("score_unchanged", 0)),
        "score_increased": int(aggregate.get("score_increased_count", 0)),
        "not_applicable": int(aggregate.get("not_applicable_count", 0)),
        "candidate_invalid": int(aggregate.get("validation_failed_count", 0)),
        "boundary_candidate_found": int(aggregate.get("boundary_candidate_count", 0)),
    }
    for kind, count in counts.items():
        if count:
            observations.append(_observation(
                tool, kind, f"{count} {kind} result(s) observed", severity="warning" if kind in {"score_increased", "not_applicable", "candidate_invalid"} else "info",
                evidence_refs=evidence, metrics={"count": count},
                recommended_actions=( ["report", "manual_review"] if kind == "score_increased" else ["report"] ),
                requires_replan=kind == "score_unchanged", requires_human_review=kind == "score_increased",
            ))
    if aggregate.get("budget_exhausted") or "budget" in str(aggregate.get("termination_reason") or "").lower():
        observations.append(_observation(tool, "budget_warning", "search budget was exhausted", severity="warning", metrics={"termination_reason": aggregate.get("termination_reason")}, recommended_actions=["stop_and_report"]))
    if aggregate.get("manifest_status") == "damaged":
        observations.append(_observation(tool, "manifest_corrupted", "published artifact manifest is damaged", severity="error", evidence_refs=evidence, recommended_actions=["block_and_report"], requires_human_review=True))
    elif aggregate.get("missing_artifacts"):
        observations.append(_observation(tool, "artifact_missing", "optional or expected experiment artifacts are missing", severity="warning", metrics={"missing_count": len(aggregate.get("missing_artifacts") or [])}, recommended_actions=["report"] ))
    if not observations:
        observations.append(_observation(tool, "pipeline_completed", "experiment artifacts were observed without a classified branch result", evidence_refs=evidence))
    return observations


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _read_records(path: Path, *, issues: List[str]) -> List[Dict[str, Any]]:
    try:
        return load_json_records(str(path), stage="agent_observer")
    except (OSError, StageJsonError) as exc:
        issues.append(f"invalid JSON artifact {path}: {exc}")
        return []


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _collect_statuses(records: Iterable[Mapping[str, Any]], counter: Counter[str], evidence: List[Dict[str, Any]], source: Path, operator_counter: Counter[tuple[str, str]]) -> None:
    for record in records:
        for container in (record, _mapping(record.get("branch_result")), _mapping(record.get("effect_analysis"))):
            status = container.get("branch_status") or container.get("effect_label")
            if isinstance(status, str) and status:
                counter[status] += 1
                operator_id = str(container.get("operator_id", container.get("operator_used")) or "").strip()
                if operator_id:
                    operator_counter[(operator_id, status)] += 1
                if len(evidence) < 40:
                    evidence.append({
                        "path": str(source),
                        "round": record.get("round"),
                        "sample_id": record.get("sample_id", record.get("index")),
                        "branch_id": container.get("branch_id"),
                        "operator_id": container.get("operator_id", container.get("operator_used")),
                        "status": status,
                    })


def _pending_count(records: Iterable[Mapping[str, Any]]) -> int:
    pending = 0
    for record in records:
        for state_name in ("search_state", "vertical_search_state"):
            state = _mapping(record.get(state_name))
            plans = state.get("operator_plan")
            if isinstance(plans, list):
                pending += sum(
                    1 for plan in plans
                    if isinstance(plan, Mapping) and plan.get("status") in {"pending", "running"}
                )
            frontier_status = state.get("frontier_status")
            if frontier_status in {"pending", "running"}:
                pending += 1
    return pending


def _operator_plan_summary(records: Iterable[Mapping[str, Any]]) -> tuple[Dict[str, int], Dict[str, int]]:
    statuses: Counter[str] = Counter()
    attempts: Counter[str] = Counter()
    for record in records:
        for state_name in ("search_state", "vertical_search_state"):
            state = _mapping(record.get(state_name))
            for entry in state.get("operator_plan") or []:
                if not isinstance(entry, Mapping):
                    continue
                status = str(entry.get("status") or "")
                operator = str(entry.get("operator_id") or "")
                if status:
                    statuses[status] += 1
                if operator:
                    attempts[operator] += int(entry.get("generation_attempt_count") or 0)
    return dict(statuses), dict(attempts)


def _memory_summary(memory_dir: Path, issues: List[str]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"directory": str(memory_dir), "banks": {}}
    for name in ("operator_memory_bank.jsonl", "failure_memory_bank.jsonl", "invalid_generation_cases.jsonl"):
        path = memory_dir / name
        if not path.exists():
            result["banks"][name] = {"record_count": 0, "missing": True}
            continue
        records = _read_records(path, issues=issues)
        result["banks"][name] = {"record_count": len(records), "missing": False}
    return result


def _artifact_integrity(experiment_dir: Path) -> Tuple[str, List[str]]:
    damaged: List[str] = []
    for manifest in experiment_dir.rglob("*.manifest.json"):
        output = Path(str(manifest)[: -len(".manifest.json")])
        valid, reason = validate_published_artifact(str(output))
        if not valid:
            damaged.append(f"{output}: {reason}")
    return ("damaged" if damaged else "not_checked"), damaged


def observe_experiment(
    experiment_dir: str | Path,
    *,
    run_dir: str | Path | None = None,
    boundary_target: int = 5,
    task_search_mode: str = "",
) -> Dict[str, Any]:
    """Return a compact summary; missing optional artifacts are not failures."""

    root = Path(experiment_dir).resolve()
    missing: List[str] = []
    issues: List[str] = []
    if not root.is_dir():
        observation = {
            "experiment_dir": str(root), "status": "blocked", "blocked_reason": "experiment directory does not exist",
            "missing_artifacts": [str(root)], "memory_summary": {}, "evidence_refs": [],
        }
        observation["observations"] = [_observation(
            "observe_experiment", "tool_fatal_failure", "experiment directory does not exist",
            severity="error", metrics={"experiment_dir": str(root)}, recommended_actions=["block_and_report"],
            requires_human_review=True,
        )]
        if run_dir:
            _write_json(Path(run_dir) / "agent_observation.json", observation)
        return observation

    manifest_status, damaged = _artifact_integrity(root)
    summary_path = root / "summary.txt"
    if not summary_path.exists():
        missing.append("summary.txt")
    statistics: Dict[str, Any] = {}
    statistics_json = root / "experiment_statistics.json"
    if statistics_json.exists():
        try:
            loaded = json.loads(statistics_json.read_text(encoding="utf-8"))
            statistics = loaded if isinstance(loaded, dict) else {"value": loaded}
        except (OSError, json.JSONDecodeError) as exc:
            issues.append(f"invalid JSON artifact {statistics_json}: {exc}")
    else:
        missing.append("experiment_statistics.json")
    statistics_text_path = root / "experiment_statistics.txt"
    if not statistics_text_path.exists():
        missing.append("experiment_statistics.txt")

    final_path = root / "final" / "final_scored.jsonl"
    final_records: List[Dict[str, Any]] = []
    if final_path.exists():
        final_records = _read_records(final_path, issues=issues)
    else:
        missing.append("final/final_scored.jsonl")

    status_counter: Counter[str] = Counter()
    operator_status_counter: Counter[tuple[str, str]] = Counter()
    evidence: List[Dict[str, Any]] = []
    state_records: List[Dict[str, Any]] = []
    for round_dir in sorted(path for path in root.glob("round_*") if path.is_dir()):
        for parent in (round_dir, round_dir / "search"):
            for filename in ("branch_results.jsonl", "exploration_candidates.jsonl", "effect_analysis.jsonl"):
                artifact = parent / filename
                if artifact.exists():
                    _collect_statuses(_read_records(artifact, issues=issues), status_counter, evidence, artifact, operator_status_counter)
        for filename in ("state_updated.jsonl", "search_state_updated.jsonl"):
            artifact = round_dir / filename
            if artifact.exists():
                state_records.extend(_read_records(artifact, issues=issues))
    _collect_statuses(final_records, status_counter, evidence, final_path, operator_status_counter)
    pending = _pending_count(state_records or final_records)
    memory = _memory_summary(root / "memory", issues)
    operator_plan_status, operator_attempt_count = _operator_plan_summary(state_records or final_records)

    boundary_count = status_counter["boundary_candidate"] + status_counter["exploration_candidate"]
    score_increased = status_counter["score_increased"]
    not_applicable = status_counter["not_applicable"]
    validation_failed = status_counter["validation_failed"] + status_counter["invalid_complexity"]
    branch_error = status_counter["branch_error"]
    primary_issue = next(iter(status_counter.most_common(1)), ("no branch status found", 0))[0]
    termination_reason = statistics.get("termination_reason")
    state_search_mode = ""
    for record in state_records + final_records:
        for state_name in ("search_state", "vertical_search_state"):
            state = _mapping(record.get(state_name))
            if not termination_reason and state.get("termination_reason"):
                termination_reason = state.get("termination_reason")
            if not state_search_mode and state.get("search_mode"):
                state_search_mode = str(state.get("search_mode"))
    search_mode = task_search_mode or str(statistics.get("search_mode") or state_search_mode or "")
    if not search_mode and summary_path.exists():
        for line in summary_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("search mode:"):
                search_mode = line.split(":", 1)[1].strip()
            if not termination_reason and line.lower().startswith("termination reason:"):
                termination_reason = line.split(":", 1)[1].strip()

    status = "blocked" if damaged or issues else "observed"
    observation = {
        "experiment_dir": str(root),
        "status": status,
        "blocked_reason": "; ".join(damaged + issues) if status == "blocked" else None,
        "manifest_status": manifest_status,
        "search_mode": search_mode or None,
        "final_records_count": len(final_records),
        "pending_count": pending,
        "boundary_candidate_count": boundary_count,
        "score_increased_count": score_increased,
        "not_applicable_count": not_applicable,
        "validation_failed_count": validation_failed,
        "branch_error_count": branch_error,
        "target_reached": boundary_count >= boundary_target,
        "termination_reason": termination_reason,
        "main_issue": primary_issue,
        "status_counts": dict(status_counter),
        "operator_status_counts": {operator: {status: count for (seen_operator, status), count in operator_status_counter.items() if seen_operator == operator} for operator in sorted({operator for operator, _ in operator_status_counter})},
        "operator_plan_status": operator_plan_status,
        "operator_attempt_count": operator_attempt_count,
        "missing_artifacts": missing,
        "memory_summary": memory,
        "evidence_refs": evidence,
    }
    observation["observations"] = normalize_tool_result(
        {"tool": "observe_experiment", "ok": observation["status"] != "blocked", "observation": observation},
        experiment_observation=observation,
    )
    if run_dir:
        _write_json(Path(run_dir) / "agent_observation.json", observation)
    return observation
