"""Read-only summaries of experiment artifacts and local M1 memory."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Tuple

from pipeline_runtime import StageJsonError, load_json_records, validate_published_artifact


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


def _collect_statuses(records: Iterable[Mapping[str, Any]], counter: Counter[str], evidence: List[Dict[str, Any]], source: Path) -> None:
    for record in records:
        for container in (record, _mapping(record.get("branch_result")), _mapping(record.get("effect_analysis"))):
            status = container.get("branch_status") or container.get("effect_label")
            if isinstance(status, str) and status:
                counter[status] += 1
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
    evidence: List[Dict[str, Any]] = []
    state_records: List[Dict[str, Any]] = []
    for round_dir in sorted(path for path in root.glob("round_*") if path.is_dir()):
        for parent in (round_dir, round_dir / "search"):
            for filename in ("branch_results.jsonl", "exploration_candidates.jsonl", "effect_analysis.jsonl"):
                artifact = parent / filename
                if artifact.exists():
                    _collect_statuses(_read_records(artifact, issues=issues), status_counter, evidence, artifact)
        for filename in ("state_updated.jsonl", "search_state_updated.jsonl"):
            artifact = round_dir / filename
            if artifact.exists():
                state_records.extend(_read_records(artifact, issues=issues))
    _collect_statuses(final_records, status_counter, evidence, final_path)
    pending = _pending_count(state_records or final_records)
    memory = _memory_summary(root / "memory", issues)

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
        "missing_artifacts": missing,
        "memory_summary": memory,
        "evidence_refs": evidence,
    }
    if run_dir:
        _write_json(Path(run_dir) / "agent_observation.json", observation)
    return observation
