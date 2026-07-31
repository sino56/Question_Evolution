"""Production step-mode runner for optimized parent-scoped branch search.

This adapter reuses every existing stage CLI and its artifact/checkpoint
contract.  Search control consumes Qwen decision checkpoints immediately;
experimental GPT completion runs on a separate single-worker queue and is
joined before the command publishes its final state.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from branch_artifacts import BranchArtifactStore
from branch_pipeline import ALL_STAGES, BranchPipeline
from pipeline_runtime import (
    FairRequestPool,
    load_json_records,
    publish_records,
    read_manifest,
    validate_published_artifact,
)
from search_coordinator import (
    ASSIGNMENT_MODE_LIVE,
    ASSIGNMENT_MODE_NATURAL,
    _write_jsonl_atomic,
    attach_search_state,
    build_dispatch_records,
    initialize_search_state,
    mark_branch_terminal,
    mark_experimental_evaluation_finished,
    merge_decision_result,
    recover_in_flight_branches,
    register_generated_prompt,
    parent_node_id,
    upgrade_search_state_with_artifacts,
)
from route_integrity import ROUTE_INTEGRITY_VERSION, validate_live_route_integrity


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_true(name: str, default: bool = False) -> bool:
    fallback = "true" if default else "false"
    return os.getenv(name, fallback).strip().lower() in {"1", "true", "yes", "on"}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _run(command: Sequence[str], *, cwd: Path) -> None:
    subprocess.run(list(command), cwd=str(cwd), check=True)


def _records_by_branch(records: Iterable[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    result: Dict[str, Dict[str, Any]] = {}
    for record in records:
        branch_id = _clean(record.get("branch_id") or record.get("candidate_id"))
        if branch_id:
            result[branch_id] = dict(record)
    return result


def _coerce_rate(value: Any) -> Optional[float]:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _route_integrity_manifest(records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Summarize frozen live routes without serializing prompts or evidence."""

    by_parent: Dict[str, Dict[str, str]] = {}
    for record in records:
        route = record.get("operator_route") if isinstance(record, Mapping) else None
        if not isinstance(route, Mapping):
            continue
        if route.get("routing_mode") != "hybrid" or route.get("assignment_mode") != "live":
            continue
        identity = validate_live_route_integrity(route)
        parent_id = parent_node_id(record)
        entry = {
            "route_fingerprint": _clean(route.get("route_fingerprint")),
            "route_revision": _clean(identity.get("route_revision")),
            "routing_schema_version": _clean(identity.get("routing_schema_version")),
        }
        existing = by_parent.get(parent_id)
        if existing is not None and existing != entry:
            raise ValueError(f"conflicting live routes for parent {parent_id}")
        by_parent[parent_id] = entry
    return {
        "route_integrity_version": ROUTE_INTEGRITY_VERSION,
        "live_routes": [
            {"parent_node_id": parent_id, **by_parent[parent_id]}
            for parent_id in sorted(by_parent)
        ],
    }


def _live_routes_by_parent(route_manifest: Mapping[str, Any]) -> Dict[str, Dict[str, str]]:
    if route_manifest.get("route_integrity_version") != ROUTE_INTEGRITY_VERSION:
        raise ValueError("route integrity manifest version mismatch")
    rows = route_manifest.get("live_routes")
    if not isinstance(rows, list):
        raise ValueError("route integrity manifest is missing live_routes")
    result: Dict[str, Dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("route integrity manifest has an invalid route entry")
        parent_id = _clean(row.get("parent_node_id"))
        values = {
            "route_fingerprint": _clean(row.get("route_fingerprint")),
            "route_revision": _clean(row.get("route_revision")),
            "routing_schema_version": _clean(row.get("routing_schema_version")),
        }
        if not parent_id or any(not value for value in values.values()) or parent_id in result:
            raise ValueError("route integrity manifest has an incomplete route entry")
        result[parent_id] = values
    return result


def _validate_branch_artifact_routes(
    artifact_path: Path,
    route_manifest: Mapping[str, Any],
) -> None:
    """Ensure every existing branch artifact belongs to this frozen route set."""

    expected_by_parent = _live_routes_by_parent(route_manifest)
    if not artifact_path.exists():
        return
    for line_number, line in enumerate(artifact_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            envelope = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid branch artifact {artifact_path}:{line_number}") from exc
        record = envelope.get("record") if isinstance(envelope, Mapping) else None
        if not isinstance(record, Mapping):
            raise ValueError(f"branch artifact {artifact_path}:{line_number} is missing its record")
        parent_id = _clean(record.get("parent_node_id"))
        route = record.get("operator_route")
        if not isinstance(route, Mapping) or route.get("assignment_mode") != "live":
            continue
        expected = expected_by_parent.get(parent_id)
        if expected is None:
            raise ValueError(f"branch artifact {artifact_path}:{line_number} has an unknown live parent")
        identity = validate_live_route_integrity(route)
        actual = {
            "route_fingerprint": _clean(record.get("route_fingerprint")),
            "route_revision": _clean(record.get("route_revision")),
            "routing_schema_version": _clean(record.get("routing_schema_version")),
        }
        if actual != expected or actual["route_fingerprint"] != _clean(route.get("route_fingerprint")):
            raise ValueError(f"branch artifact {artifact_path}:{line_number} route identity mismatch")
        if actual["route_revision"] != _clean(identity.get("route_revision")):
            raise ValueError(f"branch artifact {artifact_path}:{line_number} route revision mismatch")


def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
    return ordered[index]


async def _async_identity(record: Mapping[str, Any]) -> Dict[str, Any]:
    return deepcopy(dict(record))


def _artifact_operator_id(record: Mapping[str, Any]) -> str:
    operator_id = _clean(
        record.get("operator_id")
        or record.get("candidate_operator")
    )
    if operator_id:
        return operator_id
    route = record.get("operator_route")
    route = route if isinstance(route, Mapping) else {}
    return _clean(route.get("primary_operator"))


def _complete_branch_artifact(record: Mapping[str, Any]) -> Dict[str, Any]:
    result = deepcopy(dict(record))
    parent_rate = _coerce_rate(result.get("parent_score_rate"))
    child_rate = _coerce_rate(result.get("score_rate"))
    if parent_rate is None or child_rate is None:
        branch_status = "branch_error"
    elif child_rate < parent_rate:
        branch_status = "boundary_candidate"
    elif child_rate > parent_rate:
        branch_status = "score_increased"
    else:
        branch_status = "no_score_change"
    result.update(
        {
            "branch_id": _clean(result.get("branch_id") or result.get("candidate_id")),
            "parent_node_id": _clean(result.get("parent_node_id")),
            "operator_id": _artifact_operator_id(result),
            "branch_status": branch_status,
            "parent_score_rate": parent_rate,
            "child_score_rate": child_rate,
            "delta_score_rate": (
                child_rate - parent_rate
                if child_rate is not None and parent_rate is not None
                else None
            ),
            "review_status": (
                "pending" if branch_status == "boundary_candidate" else None
            ),
            "decision_evaluation_status": _clean(
                result.get("decision_evaluation_status")
            )
            or "completed",
            "experimental_evaluation_status": _clean(
                result.get("experimental_evaluation_status")
            )
            or "completed",
        }
    )
    return result


def _terminal_branch_artifact(
    record: Mapping[str, Any],
    *,
    branch_status: str,
    reason: str,
) -> Dict[str, Any]:
    result = deepcopy(dict(record))
    result.update(
        {
            "branch_id": _clean(result.get("branch_id") or result.get("candidate_id")),
            "parent_node_id": _clean(result.get("parent_node_id")),
            "operator_id": _artifact_operator_id(result),
            "branch_status": branch_status,
            "decision_evaluation_status": "failed",
            "experimental_evaluation_status": "failed",
            "parent_score_rate": _coerce_rate(result.get("parent_score_rate")),
            "child_score_rate": None,
            "delta_score_rate": None,
            "review_status": None,
            "terminal_reason": reason,
        }
    )
    return result


def _python_stage(
    project_dir: Path,
    script: str,
    *args: str,
) -> List[str]:
    return [sys.executable, str(project_dir / script), *map(str, args)]


class StreamBranchTerminalError(RuntimeError):
    def __init__(
        self,
        branch_status: str,
        record: Mapping[str, Any],
        reason: str,
    ):
        super().__init__(reason)
        self.branch_status = branch_status
        self.record = deepcopy(dict(record))
        self.reason = reason


class MultiOperatorSearchRunner:
    def __init__(
        self,
        *,
        project_dir: Path,
        work_dir: Path,
        memory_dir: Path,
        branch_window: int,
        boundary_target: int,
        operator_sort_mode: str,
        operator_statistics: Optional[Mapping[str, Any]],
        exploration_ratio: float,
        assignment_mode: str = ASSIGNMENT_MODE_NATURAL,
        max_iterations: int,
        rule_only_difficulty: bool,
        defer_gpt_experimental_evaluation: bool,
        artifact_retention: str = "compact",
    ):
        self.project_dir = project_dir
        self.work_dir = work_dir
        self.memory_dir = memory_dir
        self.branch_window = branch_window
        self.boundary_target = boundary_target
        self.operator_sort_mode = operator_sort_mode
        self.operator_statistics = operator_statistics
        self.exploration_ratio = exploration_ratio
        if assignment_mode not in {ASSIGNMENT_MODE_NATURAL, ASSIGNMENT_MODE_LIVE}:
            raise ValueError("assignment_mode must be natural or live")
        if assignment_mode == ASSIGNMENT_MODE_LIVE and operator_sort_mode != "route":
            raise ValueError("live assignment preserves Router rank and requires operator_sort_mode=route")
        self.assignment_mode = assignment_mode
        self.max_iterations = max_iterations
        self.rule_only_difficulty = rule_only_difficulty
        self.defer_gpt_experimental_evaluation = bool(
            defer_gpt_experimental_evaluation
        )
        if artifact_retention not in {"compact", "full"}:
            raise ValueError("artifact_retention must be compact or full")
        self.artifact_retention = artifact_retention
        self.performance_events = self.work_dir / "performance_events.jsonl"
        self.artifacts = BranchArtifactStore(self.work_dir / "branch_results.jsonl")
        self._artifact_lock = threading.Lock()
        self._artifact_cleanup_errors: List[Dict[str, str]] = []

    def _resume_state_paths(self) -> List[Path]:
        """Return newest durable scheduler snapshots first.

        Only these atomically-written state files are used for recovery.  A
        final published output is handled by ``main`` before a runner is
        created, so an unfinished directory never silently restarts from the
        newly routed parent records.
        """

        candidates = [self.work_dir / "stream_search_state.jsonl"]
        for wave_dir in self.work_dir.glob("wave_*"):
            candidates.extend(
                [
                    wave_dir / "search_state_updated.jsonl",
                    wave_dir / "search_state_claimed.jsonl",
                ]
            )
        return sorted(
            (path for path in candidates if path.is_file()),
            key=lambda path: path.stat().st_mtime_ns,
            reverse=True,
        )

    def _load_resumable_records(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> Sequence[Mapping[str, Any]]:
        expected_by_parent = {parent_node_id(record): record for record in records}
        if len(expected_by_parent) != len(records):
            raise ValueError("input contains duplicate parent_node_id values")
        expected_manifest = _route_integrity_manifest(records)

        for state_path in self._resume_state_paths():
            persisted = load_json_records(str(state_path), stage="search_resume_state")
            by_parent: Dict[str, Mapping[str, Any]] = {}
            for row in persisted:
                parent_id = _clean(row.get("parent_node_id"))
                state = row.get("search_state")
                if not parent_id or not isinstance(state, Mapping) or parent_id in by_parent:
                    raise ValueError(f"invalid resumable search state: {state_path}")
                by_parent[parent_id] = row
            if set(by_parent) != set(expected_by_parent):
                raise ValueError(
                    "resumable search state parent set differs from routed input; "
                    "start a new experiment instead of mixing artifacts"
                )
            if _route_integrity_manifest(list(by_parent.values())) != expected_manifest:
                raise ValueError(
                    "resumable search state route identity differs from routed input"
                )

            resumed: List[Dict[str, Any]] = []
            for parent_id, current in expected_by_parent.items():
                result = deepcopy(dict(current))
                result["search_state"] = deepcopy(dict(by_parent[parent_id]["search_state"]))
                resumed.append(result)
            return resumed
        return records

    def _initialize_state_records(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        records = self._load_resumable_records(records)
        state_records: List[Dict[str, Any]] = []
        for record in records:
            existing_state = record.get("search_state")
            if not isinstance(existing_state, Mapping):
                existing_state = record.get("multi_operator_search_state")
            if isinstance(existing_state, Mapping):
                state, legacy_artifacts = upgrade_search_state_with_artifacts(
                    existing_state,
                    record=record,
                    branch_window=self.branch_window,
                    boundary_target=self.boundary_target,
                    assignment_mode=self.assignment_mode,
                )
                with self._artifact_lock:
                    for artifact in legacy_artifacts:
                        self.artifacts.append(artifact, "legacy_branch")
                state = recover_in_flight_branches(state, {})
            else:
                state = initialize_search_state(
                    record,
                    branch_window=self.branch_window,
                    boundary_target=self.boundary_target,
                    operator_sort_mode=self.operator_sort_mode,
                    operator_statistics=self.operator_statistics,
                    exploration_ratio=self.exploration_ratio,
                    assignment_mode=self.assignment_mode,
                )
            state_records.append(attach_search_state(record, state))
        _validate_branch_artifact_routes(
            self.work_dir / "branch_results.jsonl",
            _route_integrity_manifest(state_records),
        )
        return state_records

    def _write(self, path: Path, records: Sequence[Mapping[str, Any]]) -> None:
        _write_jsonl_atomic(records, str(path))

    def _generate_batch(
        self,
        wave_dir: Path,
        name: str,
        records: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not records:
            return []
        input_path = wave_dir / f"{name}_generation_input.jsonl"
        output_path = wave_dir / f"{name}_candidates.jsonl"
        self._write(input_path, records)
        _run(
            _python_stage(
                self.project_dir,
                "question_evolution.py",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--num-candidates",
                "1",
                "--validation-retries",
                str(_env_int("VALIDATION_RETRIES", 1)),
                "--concurrency",
                str(max(1, _env_int("EVO_CONCURRENCY", 20))),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        return load_json_records(str(output_path), stage="search_generation")

    def _scoring_command(
        self,
        input_path: Path,
        output_path: Path,
        *,
        evaluation_mode: str,
        per_branch_stream: bool = False,
    ) -> List[str]:
        qwen_limit = (
            1
            if per_branch_stream
            else max(1, _env_int("QWEN_SCORING_MAX_CONCURRENT", 20))
        )
        gpt_limit = (
            1
            if per_branch_stream
            else max(1, _env_int("GPT_SCORING_MAX_CONCURRENT", 20))
        )
        return _python_stage(
            self.project_dir,
            "scoring.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--evaluation-mode",
            evaluation_mode,
            "--answer-mode",
            "llm",
            "--answer-trials",
            str(max(1, _env_int("SCORING_ANSWER_TRIALS", 3))),
            "--gpt-answer-trials",
            str(max(0, _env_int("GPT_ANSWER_TRIALS", 3))),
            "--qwen-judge-repeats",
            str(max(1, _env_int("QWEN_JUDGE_REPEATS", 2))),
            "--gpt-judge-repeats",
            str(max(0, _env_int("GPT_JUDGE_REPEATS", 2))),
            "--qwen-max-concurrent",
            str(qwen_limit),
            "--gpt-max-concurrent",
            str(gpt_limit),
            "--concurrency",
            str(max(1, _env_int("SCORING_CONCURRENCY", 20))),
            "--performance-events",
            str(self.performance_events),
        )

    def _run_downstream(
        self,
        wave_dir: Path,
        accepted: Sequence[Mapping[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        candidates = wave_dir / "accepted_candidates.jsonl"
        validated = wave_dir / "validated_candidates.jsonl"
        factual = wave_dir / "light_factual_checked_candidates.jsonl"
        difficulty = wave_dir / "difficulty_validated_candidates.jsonl"
        evolved = wave_dir / "evolved_branches.jsonl"
        answers = wave_dir / "with_answers.jsonl"
        rubric = wave_dir / "rubric.jsonl"
        decision = wave_dir / "decision_scored.jsonl"
        effect = wave_dir / "effect_analysis.jsonl"
        post = wave_dir / "state_updated.jsonl"
        parents = wave_dir / "parents.jsonl"
        self._write(candidates, accepted)

        _run(
            _python_stage(
                self.project_dir,
                "validate_evolved_question.py",
                "--input",
                str(candidates),
                "--output",
                str(validated),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        _run(
            _python_stage(
                self.project_dir,
                "light_factual_check.py",
                "--input",
                str(validated),
                "--output",
                str(factual),
                "--report-output",
                str(wave_dir / "light_factual_report.json"),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        difficulty_command = _python_stage(
            self.project_dir,
            "validate_difficulty_gain.py",
            "--input",
            str(factual),
            "--output",
            str(difficulty),
            "--report-output",
            str(wave_dir / "difficulty_gain_report.json"),
            "--concurrency",
            str(max(1, _env_int("DIFFICULTY_GAIN_CONCURRENCY", 5))),
            "--min-gain-score",
            str(_env_float("MIN_DIFFICULTY_GAIN_SCORE", 0.75)),
            "--borderline-gain-score",
            str(_env_float("BORDERLINE_DIFFICULTY_GAIN_SCORE", 0.65)),
            "--min-competitive-judgment-score",
            str(_env_float("MIN_COMPETITIVE_JUDGMENT_SCORE", 0.60)),
            "--performance-events",
            str(self.performance_events),
        )
        if self.rule_only_difficulty:
            difficulty_command.append("--rule-only")
        if _env_true("DIFFICULTY_GAIN_ALLOW_BORDERLINE"):
            difficulty_command.append("--allow-borderline")
        if _env_true("DIFFICULTY_GAIN_ENABLE_WEAK_PROBE"):
            difficulty_command.extend(
                [
                    "--enable-weak-probe",
                    "--weak-probe-mode",
                    os.getenv("WEAK_PROBE_MODE", "light"),
                ]
            )
        _run(difficulty_command, cwd=self.project_dir)
        _run(
            _python_stage(
                self.project_dir,
                "candidate_selection.py",
                "--input",
                str(difficulty),
                "--output",
                str(evolved),
                "--branch-mode",
                "--invalid-output",
                str(wave_dir / "invalid_generation_cases.jsonl"),
                "--report-output",
                str(wave_dir / "candidate_selection_report.json"),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        evolved_records = load_json_records(str(evolved), stage="search_selection")
        eligible = [record for record in evolved_records if record.get("question_evolved") is True]
        rejected = [record for record in evolved_records if record.get("question_evolved") is not True]
        if not eligible:
            return [], rejected
        self._write(evolved, eligible)

        _run(
            _python_stage(
                self.project_dir,
                "collect_answers.py",
                "--input",
                str(evolved),
                "--output",
                str(answers),
                "--samples",
                "1",
                "--concurrency",
                str(max(1, _env_int("ANSWER_CONCURRENCY", 20))),
                "--request-concurrency",
                str(max(1, _env_int("ANSWER_REQUEST_CONCURRENCY", 20))),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        _run(
            _python_stage(
                self.project_dir,
                "gen_rubric.py",
                "--input",
                str(answers),
                "--output",
                str(rubric),
                "--concurrency",
                str(max(1, _env_int("RUBRIC_CONCURRENCY", 20))),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        _run(
            self._scoring_command(
                rubric,
                decision,
                evaluation_mode=(
                    "decision"
                    if self.defer_gpt_experimental_evaluation
                    else "complete"
                ),
            ),
            cwd=self.project_dir,
        )
        decision_records = load_json_records(str(decision), stage="search_decision")
        parent_records: Dict[str, Dict[str, Any]] = {}
        for record in accepted:
            parent_id = _clean(record.get("parent_node_id"))
            if parent_id and parent_id not in parent_records:
                parent = deepcopy(dict(record))
                snapshot = parent.get("meta_info", {}).get("parent_snapshot")
                if isinstance(snapshot, Mapping):
                    parent["prompt"] = snapshot.get("prompt", parent.get("prompt"))
                    parent["rubric"] = snapshot.get("rubric")
                    parent["score_prompt"] = snapshot.get("score_prompt")
                    parent["scoring_result"] = snapshot.get("scoring_result")
                    parent["score_rate"] = snapshot.get("score_rate")
                parent_records[parent_id] = parent
        self._write(parents, list(parent_records.values()))
        _run(
            _python_stage(
                self.project_dir,
                "analyze_evolution_effect.py",
                "--before",
                str(parents),
                "--input",
                str(decision),
                "--output",
                str(effect),
                "--matrix-output",
                str(wave_dir / "effect_matrix.jsonl"),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        _run(
            _python_stage(
                self.project_dir,
                "update_sample_state.py",
                "--input",
                str(effect),
                "--output",
                str(post),
                "--memory-dir",
                str(self.memory_dir),
                "--preselection-invalid-input",
                str(wave_dir / "invalid_generation_cases.jsonl"),
                "--report-output",
                str(wave_dir / "state_update_report.json"),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        return decision_records, rejected

    def _stream_branch_dir(self, record: Mapping[str, Any]) -> Path:
        branch_id = _clean(record.get("branch_id") or record.get("candidate_id"))
        return self.work_dir / "stream_branches" / hashlib_sha(branch_id)

    def _remove_scoped_tree(self, path: Path, root: Path) -> None:
        """Remove one verified child tree without accepting broad targets."""

        resolved_root = root.resolve()
        resolved_path = path.resolve()
        if resolved_path == resolved_root or resolved_path.parent != resolved_root:
            raise ValueError(f"refuse to remove unscoped search artifact path: {path}")
        if resolved_path.exists():
            try:
                shutil.rmtree(resolved_path)
            except OSError as exc:
                # Storage cleanup must never turn an already durable branch
                # result into a business-stage failure. Leave the directory
                # for manual inspection and publish a small diagnostic.
                self._artifact_cleanup_errors.append(
                    {"path": str(resolved_path), "error": str(exc)}
                )
                warning_path = self.work_dir / "artifact_cleanup_warnings.json"
                temporary = warning_path.with_suffix(warning_path.suffix + ".tmp")
                temporary.write_text(
                    json.dumps(
                        self._artifact_cleanup_errors,
                        ensure_ascii=False,
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, warning_path)

    def _cleanup_stream_branch(self, record: Mapping[str, Any]) -> None:
        if self.artifact_retention != "compact":
            return
        root = self.work_dir / "stream_branches"
        self._remove_scoped_tree(self._stream_branch_dir(record), root)

    def cleanup_published_intermediates(self, pipeline_mode: str) -> None:
        """Drop replay-redundant stage files only after final publication.

        The append-only branch result, lightweight search state, summary,
        performance events, and terminal stream checkpoint remain available.
        """

        if self.artifact_retention != "compact":
            return
        if pipeline_mode == "step":
            for wave_dir in self.work_dir.glob("wave_[0-9][0-9][0-9][0-9]"):
                if wave_dir.is_dir():
                    self._remove_scoped_tree(wave_dir, self.work_dir)
        elif pipeline_mode == "stream":
            stream_root = self.work_dir / "stream_branches"
            if stream_root.exists():
                for branch_dir in list(stream_root.iterdir()):
                    if branch_dir.is_dir():
                        self._remove_scoped_tree(branch_dir, stream_root)
                try:
                    stream_root.rmdir()
                except OSError:
                    # Preserve unexpected non-directory diagnostics rather than
                    # deleting files outside the known branch layout.
                    pass
        else:
            raise ValueError(f"unsupported pipeline mode: {pipeline_mode}")

    def _stream_validation(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        branch_dir = self._stream_branch_dir(record)
        branch_dir.mkdir(parents=True, exist_ok=True)
        candidates = branch_dir / "candidate.jsonl"
        validated = branch_dir / "validated.jsonl"
        factual = branch_dir / "factual.jsonl"
        difficulty = branch_dir / "difficulty.jsonl"
        selected = branch_dir / "selected.jsonl"
        self._write(candidates, [record])
        _run(
            _python_stage(
                self.project_dir,
                "validate_evolved_question.py",
                "--input",
                str(candidates),
                "--output",
                str(validated),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        _run(
            _python_stage(
                self.project_dir,
                "light_factual_check.py",
                "--input",
                str(validated),
                "--output",
                str(factual),
                "--report-output",
                str(branch_dir / "light_factual_report.json"),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        difficulty_command = _python_stage(
            self.project_dir,
            "validate_difficulty_gain.py",
            "--input",
            str(factual),
            "--output",
            str(difficulty),
            "--report-output",
            str(branch_dir / "difficulty_report.json"),
            "--concurrency",
            str(max(1, _env_int("DIFFICULTY_GAIN_CONCURRENCY", 5))),
            "--min-gain-score",
            str(_env_float("MIN_DIFFICULTY_GAIN_SCORE", 0.75)),
            "--borderline-gain-score",
            str(_env_float("BORDERLINE_DIFFICULTY_GAIN_SCORE", 0.65)),
            "--min-competitive-judgment-score",
            str(_env_float("MIN_COMPETITIVE_JUDGMENT_SCORE", 0.60)),
            "--performance-events",
            str(self.performance_events),
        )
        if self.rule_only_difficulty:
            difficulty_command.append("--rule-only")
        if _env_true("DIFFICULTY_GAIN_ALLOW_BORDERLINE"):
            difficulty_command.append("--allow-borderline")
        if _env_true("DIFFICULTY_GAIN_ENABLE_WEAK_PROBE"):
            difficulty_command.extend(
                [
                    "--enable-weak-probe",
                    "--weak-probe-mode",
                    os.getenv("WEAK_PROBE_MODE", "light"),
                ]
            )
        _run(difficulty_command, cwd=self.project_dir)
        _run(
            _python_stage(
                self.project_dir,
                "candidate_selection.py",
                "--input",
                str(difficulty),
                "--output",
                str(selected),
                "--branch-mode",
                "--invalid-output",
                str(branch_dir / "invalid_generation_cases.jsonl"),
                "--report-output",
                str(branch_dir / "candidate_selection_report.json"),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        rows = load_json_records(str(selected), stage="stream_validation")
        if not rows or rows[0].get("question_evolved") is not True:
            rejected = rows[0] if rows else dict(record)
            raise StreamBranchTerminalError(
                "validation_failed",
                rejected,
                "candidate selection rejected the branch",
            )
        return rows[0]

    def _stream_reference_answer(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        branch_dir = self._stream_branch_dir(record)
        selected = branch_dir / "selected_for_answer.jsonl"
        answers = branch_dir / "answers.jsonl"
        self._write(selected, [record])
        _run(
            _python_stage(
                self.project_dir,
                "collect_answers.py",
                "--input",
                str(selected),
                "--output",
                str(answers),
                "--samples",
                "1",
                "--concurrency",
                str(max(1, _env_int("ANSWER_CONCURRENCY", 20))),
                "--request-concurrency",
                str(max(1, _env_int("ANSWER_REQUEST_CONCURRENCY", 20))),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        return load_json_records(str(answers), stage="stream_reference_answer")[0]

    def _stream_rubric(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        branch_dir = self._stream_branch_dir(record)
        answers = branch_dir / "answers_for_rubric.jsonl"
        rubric = branch_dir / "rubric.jsonl"
        self._write(answers, [record])
        _run(
            _python_stage(
                self.project_dir,
                "gen_rubric.py",
                "--input",
                str(answers),
                "--output",
                str(rubric),
                "--concurrency",
                str(max(1, _env_int("RUBRIC_CONCURRENCY", 20))),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        return load_json_records(str(rubric), stage="stream_rubric")[0]

    def _stream_decision(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        branch_dir = self._stream_branch_dir(record)
        rubric = branch_dir / "rubric_for_scoring.jsonl"
        decision = branch_dir / "decision.jsonl"
        parent = branch_dir / "parent.jsonl"
        effect = branch_dir / "effect.jsonl"
        post = branch_dir / "post_decision.jsonl"
        self._write(rubric, [record])
        _run(
            self._scoring_command(
                rubric,
                decision,
                evaluation_mode=(
                    "decision"
                    if self.defer_gpt_experimental_evaluation
                    else "complete"
                ),
                per_branch_stream=True,
            ),
            cwd=self.project_dir,
        )
        decision_record = load_json_records(
            str(decision),
            stage="stream_decision",
        )[0]
        parent_record = deepcopy(dict(record))
        meta_info = parent_record.get("meta_info")
        meta_info = meta_info if isinstance(meta_info, Mapping) else {}
        snapshot = meta_info.get("parent_snapshot")
        if isinstance(snapshot, Mapping):
            for field in (
                "prompt",
                "rubric",
                "score_prompt",
                "scoring_result",
                "score_rate",
            ):
                if field in snapshot:
                    parent_record[field] = deepcopy(snapshot[field])
        self._write(parent, [parent_record])
        _run(
            _python_stage(
                self.project_dir,
                "analyze_evolution_effect.py",
                "--before",
                str(parent),
                "--input",
                str(decision),
                "--output",
                str(effect),
                "--matrix-output",
                str(branch_dir / "effect_matrix.jsonl"),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        _run(
            _python_stage(
                self.project_dir,
                "update_sample_state.py",
                "--input",
                str(effect),
                "--output",
                str(post),
                "--memory-dir",
                str(self.memory_dir),
                "--preselection-invalid-input",
                str(branch_dir / "invalid_generation_cases.jsonl"),
                "--report-output",
                str(branch_dir / "state_update_report.json"),
                "--performance-events",
                str(self.performance_events),
            ),
            cwd=self.project_dir,
        )
        post_record = load_json_records(str(post), stage="stream_post_decision")[0]
        for field in (
            "effect_analysis",
            "evolution_state",
            "failure_memory_candidate",
        ):
            if field in post_record:
                decision_record[field] = deepcopy(post_record[field])
        return decision_record

    def _stream_experimental(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        if not self.defer_gpt_experimental_evaluation:
            return dict(record)
        branch_dir = self._stream_branch_dir(record)
        decision = branch_dir / "experimental_input.jsonl"
        complete = branch_dir / "complete.jsonl"
        self._write(decision, [record])
        _run(
            self._scoring_command(
                decision,
                complete,
                evaluation_mode="experimental",
                per_branch_stream=True,
            ),
            cwd=self.project_dir,
        )
        completed = load_json_records(str(complete), stage="stream_experimental")[0]
        for field in (
            "effect_analysis",
            "evolution_state",
            "failure_memory_candidate",
        ):
            if field in record:
                completed[field] = deepcopy(record[field])
        return completed

    def _publish_complete_records(
        self,
        wave_dir: Path,
        complete: Sequence[Mapping[str, Any]],
    ) -> List[Tuple[str, str]]:
        post_path = wave_dir / "state_updated.jsonl"
        post = (
            _records_by_branch(load_json_records(str(post_path), stage="search_post"))
            if post_path.exists()
            else {}
        )
        completed_branches: List[Tuple[str, str]] = []
        with self._artifact_lock:
            for record in complete:
                branch_id = _clean(record.get("branch_id") or record.get("candidate_id"))
                final = dict(record)
                if branch_id in post:
                    for field in (
                        "effect_analysis",
                        "evolution_state",
                        "failure_memory_candidate",
                    ):
                        if field in post[branch_id]:
                            final[field] = deepcopy(post[branch_id][field])
                final = _complete_branch_artifact(final)
                self.artifacts.append(final, "complete_branch")
                experimental_status = _clean(
                    final.get("experimental_evaluation_status")
                )
                completed_branches.append(
                    (
                        branch_id,
                        "failed" if experimental_status == "failed" else "completed",
                    )
                )
        return completed_branches

    def _complete_experimental(
        self,
        wave_dir: Path,
        decision_records: Sequence[Mapping[str, Any]],
    ) -> List[Tuple[str, str]]:
        decision_path = wave_dir / "experimental_input.jsonl"
        complete_path = wave_dir / "scored.jsonl"
        self._write(decision_path, decision_records)
        _run(
            self._scoring_command(
                decision_path,
                complete_path,
                evaluation_mode="experimental",
            ),
            cwd=self.project_dir,
        )
        complete = load_json_records(str(complete_path), stage="search_experimental")
        return self._publish_complete_records(wave_dir, complete)

    def _append_terminal_artifact(
        self,
        record: Mapping[str, Any],
        *,
        branch_status: str,
        reason: str,
    ) -> None:
        with self._artifact_lock:
            self.artifacts.append(
                _terminal_branch_artifact(
                    record,
                    branch_status=branch_status,
                    reason=reason,
                ),
                "terminal_branch",
            )

    @staticmethod
    def _with_branch_duration(
        record: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> Dict[str, Any]:
        result = deepcopy(dict(record))
        branch_id = _clean(result.get("branch_id") or result.get("candidate_id"))
        entry = next(
            (
                row
                for row in state.get("operator_plan") or []
                if row.get("branch_id") == branch_id
            ),
            None,
        )
        if isinstance(entry, Mapping):
            claimed_at = _coerce_rate(entry.get("claimed_at"))
            completed_at = _coerce_rate(entry.get("completed_at"))
            if (
                claimed_at is not None
                and completed_at is not None
                and completed_at >= claimed_at
            ):
                result["branch_duration_seconds"] = completed_at - claimed_at
        return result

    def _request_pool_summary(self) -> Dict[str, Any]:
        if not self.performance_events.exists():
            return {
                "request_pool_utilization": None,
                "request_pool_peak_utilization": None,
                "request_pool_peaks": {},
            }
        limits = {
            "reference_answer": max(1, _env_int("ANSWER_REQUEST_CONCURRENCY", 20)),
            "qwen": max(1, _env_int("QWEN_SCORING_MAX_CONCURRENT", 20)),
            "gpt": max(1, _env_int("GPT_SCORING_MAX_CONCURRENT", 20)),
        }
        peaks: Dict[str, int] = {}
        for line in self.performance_events.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            event_peaks = event.get("request_pool_peaks")
            if not isinstance(event_peaks, Mapping):
                continue
            for pool_name, raw_peak in event_peaks.items():
                try:
                    peak = max(0, int(raw_peak))
                except (TypeError, ValueError):
                    continue
                peaks[str(pool_name)] = max(peaks.get(str(pool_name), 0), peak)
        ratios = [
            peak / limits[name]
            for name, peak in peaks.items()
            if name in limits and limits[name] > 0
        ]
        return {
            # Step mode only exposes per-stage peaks. Keep the aggregate
            # utilization conservative and publish the peak separately.
            "request_pool_utilization": (
                statistics.fmean(ratios) if ratios else None
            ),
            "request_pool_peak_utilization": max(ratios) if ratios else None,
            "request_pool_peaks": peaks,
        }

    def run(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        output_path: Path,
    ) -> List[Dict[str, Any]]:
        started_at = time.monotonic()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        state_records = self._initialize_state_records(records)

        experimental_pool = ThreadPoolExecutor(max_workers=1)
        experimental_jobs: List[Future[List[Tuple[str, str]]]] = []
        search_completed_at: Optional[float] = None
        try:
            existing_wave_numbers = [
                int(path.name.removeprefix("wave_"))
                for path in self.work_dir.glob("wave_[0-9][0-9][0-9][0-9]")
                if path.is_dir() and path.name.removeprefix("wave_").isdigit()
            ]
            first_iteration = max(existing_wave_numbers, default=0) + 1
            for iteration in range(
                first_iteration,
                first_iteration + self.max_iterations,
            ):
                wave_dir = self.work_dir / f"wave_{iteration:04d}"
                wave_dir.mkdir(parents=True, exist_ok=True)
                next_states: List[Dict[str, Any]] = []
                dispatch_records: List[Dict[str, Any]] = []
                for record in state_records:
                    state, dispatched = build_dispatch_records(
                        record,
                        record["search_state"],
                    )
                    next_states.append(attach_search_state(record, state))
                    dispatch_records.extend(dispatched)
                state_records = next_states
                self._write(wave_dir / "search_state_claimed.jsonl", state_records)
                if not dispatch_records:
                    break

                accepted: List[Dict[str, Any]] = []
                states_by_parent = {
                    record["search_state"]["parent_node_id"]: record
                    for record in state_records
                }
                for sequence in sorted(
                    {
                        int(record["search_dispatch"]["generation_sequence"])
                        for record in dispatch_records
                    }
                ):
                    sequence_inputs = [
                        record
                        for record in dispatch_records
                        if int(record["search_dispatch"]["generation_sequence"]) == sequence
                    ]
                    generated = self._generate_batch(
                        wave_dir,
                        f"seq_{sequence}",
                        sequence_inputs,
                    )
                    input_by_branch = _records_by_branch(sequence_inputs)
                    for candidate in generated:
                        branch_id = _clean(
                            candidate.get("branch_id") or candidate.get("candidate_id")
                        )
                        parent_id = _clean(candidate.get("parent_node_id"))
                        state_record = states_by_parent[parent_id]
                        state = state_record["search_state"]
                        generation = candidate.get("candidate_generation")
                        generation = generation if isinstance(generation, Mapping) else {}
                        if generation.get("generation_status") == "not_applicable":
                            reason = _clean(generation.get("not_applicable_reason")) or (
                                "operator reported not_applicable without a reason"
                            )
                            state = mark_branch_terminal(
                                state,
                                branch_id=branch_id,
                                branch_status="not_applicable",
                                reason=reason,
                            )
                            self._append_terminal_artifact(
                                self._with_branch_duration(candidate, state),
                                branch_status="not_applicable",
                                reason=reason,
                            )
                        elif candidate.get("question_evolved") is not True:
                            reason = _clean(candidate.get("question_evolution_error"))
                            state = mark_branch_terminal(
                                state,
                                branch_id=branch_id,
                                branch_status="branch_error",
                                reason=reason,
                            )
                            self._append_terminal_artifact(
                                self._with_branch_duration(candidate, state),
                                branch_status="branch_error",
                                reason=reason,
                            )
                        else:
                            state, action = register_generated_prompt(
                                state,
                                branch_id=branch_id,
                                prompt=_clean(candidate.get("prompt")),
                            )
                            if action == "accepted":
                                accepted.append(candidate)
                            elif action == "retry_duplicate":
                                retry_input = deepcopy(input_by_branch[branch_id])
                                retry_input["search_generation_feedback"] = (
                                    "上一次生成结果与父节点或已有兄弟分支精确重复：\n"
                                    f"{candidate.get('prompt')}\n"
                                    "请继续使用当前算子重新生成，但不得复用上述题目。"
                                )
                                retry_rows = self._generate_batch(
                                    wave_dir,
                                    f"seq_{sequence}_{BranchArtifactStore.__name__}_{hashlib_sha(branch_id)}",
                                    [retry_input],
                                )
                                retry_candidate = retry_rows[0]
                                retry_candidate["duplicate_generation_trajectories"] = [
                                    {
                                        "generation_attempt": 1,
                                        "prompt": candidate.get("prompt"),
                                        "candidate_generation": deepcopy(
                                            candidate.get("candidate_generation")
                                        ),
                                    },
                                    {
                                        "generation_attempt": 2,
                                        "prompt": retry_candidate.get("prompt"),
                                        "candidate_generation": deepcopy(
                                            retry_candidate.get("candidate_generation")
                                        ),
                                    },
                                ]
                                state, retry_action = register_generated_prompt(
                                    state,
                                    branch_id=branch_id,
                                    prompt=_clean(retry_candidate.get("prompt")),
                                )
                                if retry_action == "accepted":
                                    accepted.append(retry_candidate)
                                elif retry_action == "duplicate_exhausted":
                                    self._append_terminal_artifact(
                                        self._with_branch_duration(
                                            retry_candidate,
                                            state,
                                        ),
                                        branch_status="duplicate_exhausted",
                                        reason="exact duplicate after one regeneration retry",
                                    )
                        state_record["search_state"] = state

                if accepted:
                    decisions, rejected = self._run_downstream(wave_dir, accepted)
                    for rejected_record in rejected:
                        branch_id = _clean(
                            rejected_record.get("branch_id")
                            or rejected_record.get("candidate_id")
                        )
                        parent_id = _clean(rejected_record.get("parent_node_id"))
                        state_record = states_by_parent[parent_id]
                        state_record["search_state"] = mark_branch_terminal(
                            state_record["search_state"],
                            branch_id=branch_id,
                            branch_status="validation_failed",
                            reason="candidate selection rejected the branch",
                        )
                        self._append_terminal_artifact(
                            self._with_branch_duration(
                                rejected_record,
                                state_record["search_state"],
                            ),
                            branch_status="validation_failed",
                            reason="candidate selection rejected the branch",
                        )
                    for decision in decisions:
                        parent_id = _clean(decision.get("parent_node_id"))
                        state_record = states_by_parent[parent_id]
                        state_record["search_state"] = merge_decision_result(
                            state_record["search_state"],
                            decision,
                        )
                        branch_id = _clean(
                            decision.get("branch_id") or decision.get("candidate_id")
                        )
                        summary = state_record["search_state"]["branch_summaries"][
                            branch_id
                        ]
                        entry = next(
                            row
                            for row in state_record["search_state"]["operator_plan"]
                            if row.get("branch_id") == branch_id
                        )
                        decision["operator_id"] = summary["operator_id"]
                        decision["branch_status"] = summary["branch_status"]
                        claimed_at = _coerce_rate(entry.get("claimed_at"))
                        completed_at = _coerce_rate(entry.get("completed_at"))
                        if (
                            claimed_at is not None
                            and completed_at is not None
                            and completed_at >= claimed_at
                        ):
                            decision["branch_duration_seconds"] = (
                                completed_at - claimed_at
                            )
                    if self.defer_gpt_experimental_evaluation:
                        experimental_jobs.append(
                            experimental_pool.submit(
                                self._complete_experimental,
                                wave_dir,
                                decisions,
                            )
                        )
                    else:
                        self._publish_complete_records(wave_dir, decisions)
                state_records = list(states_by_parent.values())
                self._write(wave_dir / "search_state_updated.jsonl", state_records)
            else:
                raise RuntimeError(
                    f"search exceeded max scheduler iterations: {self.max_iterations}"
                )
            search_completed_at = time.monotonic()
            completed_experimental_branches: List[Tuple[str, str]] = []
            for job in experimental_jobs:
                completed_experimental_branches.extend(job.result())
            if completed_experimental_branches:
                completed_status = dict(completed_experimental_branches)
                for record in state_records:
                    state = record["search_state"]
                    for branch_id in completed_status.keys() & (
                        state.get("branch_summaries") or {}
                    ).keys():
                        state = mark_experimental_evaluation_finished(
                            state,
                            branch_id,
                            status=completed_status[branch_id],
                        )
                    record["search_state"] = state
        finally:
            experimental_pool.shutdown(wait=True)
        full_completed_at = time.monotonic()
        elapsed_search = max(1e-9, (search_completed_at or full_completed_at) - started_at)
        elapsed_full = max(1e-9, full_completed_at - started_at)
        decision_count = sum(
            int(record["search_state"].get("decision_completed_count") or 0)
            for record in state_records
        )
        boundary_count = sum(
            int(record["search_state"].get("boundary_candidate_count") or 0)
            for record in state_records
        )
        error_branch_ids = {
            _clean(entry.get("branch_id"))
            for record in state_records
            for entry in record["search_state"].get("operator_plan") or []
            if entry.get("status") == "branch_error"
        }
        error_branch_ids.update(
            branch_id
            for record in state_records
            for branch_id, summary in (
                record["search_state"].get("branch_summaries") or {}
            ).items()
            if isinstance(summary, Mapping)
            and summary.get("experimental_evaluation_status") == "failed"
        )
        error_count = len(error_branch_ids)
        terminal_branch_count = sum(
            1
            for record in state_records
            for entry in record["search_state"].get("operator_plan") or []
            if entry.get("status")
            in {
                "completed",
                "duplicate_exhausted",
                "not_applicable",
                "validation_failed",
                "branch_error",
            }
        )
        branch_latencies: List[float] = []
        sample_latencies: List[float] = []
        duplicate_retry_count = 0
        attempt_count = 0
        for record in state_records:
            entries = record["search_state"].get("operator_plan") or []
            claimed_times: List[float] = []
            completed_times: List[float] = []
            for entry in entries:
                attempt_count += int(entry.get("generation_attempt_count") or 0)
                duplicate_retry_count += int(entry.get("duplicate_retry_count") or 0)
                claimed_at = _coerce_rate(entry.get("claimed_at"))
                completed_at = _coerce_rate(entry.get("completed_at"))
                if claimed_at is not None:
                    claimed_times.append(claimed_at)
                if completed_at is not None:
                    completed_times.append(completed_at)
                if (
                    claimed_at is not None
                    and completed_at is not None
                    and completed_at >= claimed_at
                ):
                    branch_latencies.append(completed_at - claimed_at)
            if claimed_times and completed_times:
                sample_latencies.append(max(completed_times) - min(claimed_times))
        pool_summary = self._request_pool_summary()
        summary = {
            "search_completed_seconds": elapsed_search,
            "full_experiment_completed_seconds": elapsed_full,
            "decision_to_full_experiment_delay_seconds": max(
                0.0,
                elapsed_full - elapsed_search,
            ),
            "branches_completed": decision_count,
            "decision_evaluations_completed": decision_count,
            "boundary_candidates": boundary_count,
            "branches_completed_per_wall_clock_hour": (
                decision_count * 3600.0 / elapsed_search
            ),
            "decision_evaluations_completed_per_wall_clock_hour": decision_count * 3600.0 / elapsed_search,
            "boundary_candidates_per_wall_clock_hour": boundary_count * 3600.0 / elapsed_search,
            "model_error_rate": (
                error_count / terminal_branch_count
                if terminal_branch_count
                else 0.0
            ),
            "p50_branch_latency": _percentile(branch_latencies, 0.50),
            "p95_branch_latency": _percentile(branch_latencies, 0.95),
            "p50_sample_termination_latency": _percentile(sample_latencies, 0.50),
            "p95_sample_termination_latency": _percentile(sample_latencies, 0.95),
            **pool_summary,
            "retry_rate": (
                duplicate_retry_count / attempt_count if attempt_count else 0.0
            ),
            "artifact_cleanup_error_count": len(self._artifact_cleanup_errors),
        }
        (self.work_dir / "search_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return state_records

    async def run_stream_async(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Run the dynamically refilled long-lived per-branch pipeline."""

        started_at = time.monotonic()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        state_records = self._initialize_state_records(records)
        states_by_parent = {
            record["search_state"]["parent_node_id"]: record
            for record in state_records
        }
        state_lock = asyncio.Lock()
        gpt_pool = FairRequestPool(
            max(1, _env_int("GPT_SCORING_MAX_CONCURRENT", 20)),
            "stream_gpt",
        )
        qwen_pool = FairRequestPool(
            max(1, _env_int("QWEN_SCORING_MAX_CONCURRENT", 20)),
            "stream_qwen",
        )

        async def run_in_pool(
            pool: FairRequestPool,
            record: Mapping[str, Any],
            function: Any,
            *args: Any,
        ) -> Any:
            sample_key = _clean(record.get("parent_node_id")) or _clean(
                record.get("sample_id")
            )
            async with pool.request(sample_key):
                return await asyncio.to_thread(function, *args)

        async def generation_handler(record: Dict[str, Any]) -> Dict[str, Any]:
            branch_dir = self._stream_branch_dir(record)
            candidate_rows = await run_in_pool(
                gpt_pool,
                record,
                self._generate_batch,
                branch_dir,
                "generation",
                [record],
            )
            candidate = candidate_rows[0]
            branch_id = _clean(
                candidate.get("branch_id") or candidate.get("candidate_id")
            )
            parent_id = _clean(candidate.get("parent_node_id"))
            generation = candidate.get("candidate_generation")
            generation = generation if isinstance(generation, Mapping) else {}
            if generation.get("generation_status") == "not_applicable":
                raise StreamBranchTerminalError(
                    "not_applicable",
                    candidate,
                    _clean(generation.get("not_applicable_reason"))
                    or "operator reported not_applicable without a reason",
                )
            if candidate.get("question_evolved") is not True:
                raise StreamBranchTerminalError(
                    "branch_error",
                    candidate,
                    _clean(candidate.get("question_evolution_error"))
                    or "question evolution did not produce a candidate",
                )
            async with state_lock:
                state_record = states_by_parent[parent_id]
                state, action = register_generated_prompt(
                    state_record["search_state"],
                    branch_id=branch_id,
                    prompt=_clean(candidate.get("prompt")),
                )
                state_record["search_state"] = state
            if action == "accepted":
                return candidate
            if action == "duplicate_exhausted":
                raise StreamBranchTerminalError(
                    "duplicate_exhausted",
                    candidate,
                    "exact duplicate after one regeneration retry",
                )
            retry_input = deepcopy(dict(record))
            retry_input["search_generation_feedback"] = (
                "上一次生成结果与父节点或已有兄弟分支精确重复：\n"
                f"{candidate.get('prompt')}\n"
                "请继续使用当前算子重新生成，但不得复用上述题目。"
            )
            retry_rows = await run_in_pool(
                gpt_pool,
                record,
                self._generate_batch,
                branch_dir,
                "duplicate_retry",
                [retry_input],
            )
            retry_candidate = retry_rows[0]
            retry_candidate["duplicate_generation_trajectories"] = [
                {
                    "generation_attempt": 1,
                    "prompt": candidate.get("prompt"),
                    "candidate_generation": deepcopy(
                        candidate.get("candidate_generation")
                    ),
                },
                {
                    "generation_attempt": 2,
                    "prompt": retry_candidate.get("prompt"),
                    "candidate_generation": deepcopy(
                        retry_candidate.get("candidate_generation")
                    ),
                },
            ]
            async with state_lock:
                state_record = states_by_parent[parent_id]
                state, retry_action = register_generated_prompt(
                    state_record["search_state"],
                    branch_id=branch_id,
                    prompt=_clean(retry_candidate.get("prompt")),
                )
                state_record["search_state"] = state
            if retry_action != "accepted":
                raise StreamBranchTerminalError(
                    "duplicate_exhausted",
                    retry_candidate,
                    "exact duplicate after one regeneration retry",
                )
            return retry_candidate

        async def decision_handler(record: Dict[str, Any]) -> Dict[str, Any]:
            sample_key = _clean(record.get("parent_node_id")) or _clean(
                record.get("sample_id")
            )
            if self.defer_gpt_experimental_evaluation:
                async with qwen_pool.request(sample_key):
                    return await asyncio.to_thread(self._stream_decision, record)
            async with gpt_pool.request(sample_key):
                async with qwen_pool.request(sample_key):
                    return await asyncio.to_thread(self._stream_decision, record)

        async def on_decision(record: Dict[str, Any]) -> None:
            parent_id = _clean(record.get("parent_node_id"))
            branch_id = _clean(record.get("branch_id") or record.get("candidate_id"))
            async with state_lock:
                state_record = states_by_parent[parent_id]
                state_record["search_state"] = merge_decision_result(
                    state_record["search_state"],
                    record,
                )
                summary = state_record["search_state"]["branch_summaries"][branch_id]
                record["operator_id"] = summary["operator_id"]
                record["branch_status"] = summary["branch_status"]

        async def on_complete(record: Dict[str, Any]) -> None:
            parent_id = _clean(record.get("parent_node_id"))
            branch_id = _clean(record.get("branch_id") or record.get("candidate_id"))
            experimental_status = _clean(
                record.get("experimental_evaluation_status")
            ) or "completed"
            async with state_lock:
                state_record = states_by_parent[parent_id]
                state_record["search_state"] = (
                    mark_experimental_evaluation_finished(
                        state_record["search_state"],
                        branch_id,
                        status=(
                            "failed"
                            if experimental_status == "failed"
                            else "completed"
                        ),
                    )
                )
                entry = next(
                    row
                    for row in state_record["search_state"]["operator_plan"]
                    if row.get("branch_id") == branch_id
                )
                claimed_at = _coerce_rate(entry.get("claimed_at"))
                completed_at = _coerce_rate(entry.get("completed_at"))
                if (
                    claimed_at is not None
                    and completed_at is not None
                    and completed_at >= claimed_at
                ):
                    record["branch_duration_seconds"] = completed_at - claimed_at
            with self._artifact_lock:
                self.artifacts.append(
                    _complete_branch_artifact(record),
                    "complete_branch",
                )
            # The complete branch artifact is durable, while the pipeline's
            # post/experimental checkpoints can still recreate final.json if
            # the process stops immediately after this callback.
            self._cleanup_stream_branch(record)

        async def on_error(record: Dict[str, Any]) -> None:
            parent_id = _clean(record.get("parent_node_id"))
            branch_id = _clean(record.get("branch_id") or record.get("candidate_id"))
            branch_status = _clean(record.get("branch_status")) or "branch_error"
            if branch_status not in {
                "duplicate_exhausted",
                "validation_failed",
                "branch_error",
                "not_applicable",
            }:
                branch_status = "branch_error"
            reason = _clean(
                (record.get("branch_error") or {}).get("error")
                if isinstance(record.get("branch_error"), Mapping)
                else ""
            )
            async with state_lock:
                state_record = states_by_parent[parent_id]
                state_record["search_state"] = mark_branch_terminal(
                    state_record["search_state"],
                    branch_id=branch_id,
                    branch_status=branch_status,
                    reason=reason,
                )
                entry = next(
                    row
                    for row in state_record["search_state"]["operator_plan"]
                    if row.get("branch_id") == branch_id
                )
                claimed_at = _coerce_rate(entry.get("claimed_at"))
                completed_at = _coerce_rate(entry.get("completed_at"))
                if (
                    claimed_at is not None
                    and completed_at is not None
                    and completed_at >= claimed_at
                ):
                    record["branch_duration_seconds"] = completed_at - claimed_at
            self._append_terminal_artifact(
                record,
                branch_status=branch_status,
                reason=reason,
            )
            # branch_error.json and the terminal branch artifact now contain
            # all recovery and audit data needed for this failed branch.
            self._cleanup_stream_branch(record)

        handlers = {
            "generation": generation_handler,
            "validation": lambda row: run_in_pool(
                gpt_pool,
                row,
                self._stream_validation,
                row,
            ),
            "reference_answer": lambda row: run_in_pool(
                gpt_pool,
                row,
                self._stream_reference_answer,
                row,
            ),
            "rubric": lambda row: run_in_pool(
                gpt_pool,
                row,
                self._stream_rubric,
                row,
            ),
            "decision": decision_handler,
            "post_decision": lambda row: _async_identity(row),
            "experimental": lambda row: run_in_pool(
                gpt_pool,
                row,
                self._stream_experimental,
                row,
            ),
        }
        worker_cap = max(1, self.branch_window)
        pipeline = BranchPipeline(
            handlers=handlers,
            checkpoint_dir=self.work_dir / "stream_checkpoints",
            on_decision=on_decision,
            on_complete=on_complete,
            on_error=on_error,
            worker_counts={stage: worker_cap for stage in ALL_STAGES},
            queue_size=max(2, worker_cap * 2),
            performance_events=str(self.performance_events),
            compact_checkpoints=(self.artifact_retention == "compact"),
        )
        await pipeline.start()
        search_completed_at: Optional[float] = None
        for _iteration in range(1, self.max_iterations + 1):
            dispatch_records: List[Dict[str, Any]] = []
            async with state_lock:
                for parent_id, state_record in list(states_by_parent.items()):
                    state, dispatched = build_dispatch_records(
                        state_record,
                        state_record["search_state"],
                    )
                    state_record["search_state"] = state
                    states_by_parent[parent_id] = state_record
                    dispatch_records.extend(dispatched)
            if dispatch_records:
                await pipeline.submit(dispatch_records)
                self._write(
                    self.work_dir / "stream_search_state.jsonl",
                    list(states_by_parent.values()),
                )

            async with state_lock:
                in_flight_count = sum(
                    len(record["search_state"].get("in_flight_branch_ids") or [])
                    for record in states_by_parent.values()
                )
            if in_flight_count == 0:
                search_completed_at = time.monotonic()
                break
            await pipeline.wait_for_outcome()
            self._write(
                self.work_dir / "stream_search_state.jsonl",
                list(states_by_parent.values()),
            )
        else:
            raise RuntimeError(
                f"stream search exceeded max scheduler iterations: {self.max_iterations}"
            )

        pipeline_summary = await pipeline.finish()
        full_completed_at = time.monotonic()
        elapsed_search = max(
            1e-9,
            (search_completed_at or full_completed_at) - started_at,
        )
        elapsed_full = max(1e-9, full_completed_at - started_at)
        summary = {
            **pipeline_summary,
            "search_completed_seconds": elapsed_search,
            "full_experiment_completed_seconds": elapsed_full,
            "decision_to_full_experiment_delay_seconds": max(
                0.0,
                elapsed_full - elapsed_search,
            ),
            **self._request_pool_summary(),
            "request_pool_utilization": statistics.fmean(
                [
                    gpt_pool.average_utilization,
                    qwen_pool.average_utilization,
                ]
            ),
            "request_pool_peak_utilization": max(
                gpt_pool.peak_active / gpt_pool.limit,
                qwen_pool.peak_active / qwen_pool.limit,
            ),
            "request_pool_peaks": {
                "stream_gpt": gpt_pool.peak_active,
                "stream_qwen": qwen_pool.peak_active,
            },
            "artifact_cleanup_error_count": len(self._artifact_cleanup_errors),
        }
        (self.work_dir / "search_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return list(states_by_parent.values())

    def run_stream(
        self,
        records: Sequence[Mapping[str, Any]],
    ) -> List[Dict[str, Any]]:
        return asyncio.run(self.run_stream_async(records))


def hashlib_sha(value: str) -> str:
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run optimized multi-operator branch search.")
    parser.add_argument("--input", required=True, help="Routed parent JSONL input.")
    parser.add_argument("--output", required=True, help="Final lightweight search-state JSONL.")
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--memory-dir", default="memory")
    parser.add_argument("--branch-window", type=int, default=1)
    parser.add_argument("--boundary-target", type=int, default=5)
    parser.add_argument(
        "--pipeline-mode",
        choices=["step", "stream"],
        default="step",
    )
    parser.add_argument("--operator-sort-mode", choices=["route", "yield_per_time"], default="route")
    parser.add_argument("--operator-statistics", default=None)
    parser.add_argument("--exploration-ratio", type=float, default=0.1)
    parser.add_argument(
        "--assignment-mode",
        choices=[ASSIGNMENT_MODE_NATURAL, ASSIGNMENT_MODE_LIVE],
        default=os.getenv("ASSIGNMENT_MODE", ASSIGNMENT_MODE_NATURAL),
    )
    parser.add_argument("--max-iterations", type=int, default=1000)
    parser.add_argument("--rule-only-difficulty", action="store_true")
    parser.add_argument(
        "--defer-gpt-experimental-evaluation",
        action="store_true",
        help="Use Qwen-only decision checkpoints and complete GPT evaluation asynchronously.",
    )
    parser.add_argument(
        "--artifact-retention",
        choices=["compact", "full"],
        default=os.getenv("SEARCH_ARTIFACT_RETENTION", "compact"),
        help=(
            "compact removes replay-redundant stage files after durable "
            "publication; full keeps every intermediate for debugging"
        ),
    )
    return parser.parse_args()


def _search_manifest_config(args: argparse.Namespace) -> Dict[str, Any]:
    return {
        "branch_window": args.branch_window,
        "boundary_target": args.boundary_target,
        "pipeline_mode": args.pipeline_mode,
        "operator_sort_mode": args.operator_sort_mode,
        "exploration_ratio": args.exploration_ratio,
        "assignment_mode": args.assignment_mode,
        "max_iterations": args.max_iterations,
        "rule_only_difficulty": args.rule_only_difficulty,
        "defer_gpt_experimental_evaluation": args.defer_gpt_experimental_evaluation,
        "artifact_retention": args.artifact_retention,
    }


def main() -> None:
    args = parse_args()
    if args.branch_window < 1:
        raise ValueError("--branch-window must be >= 1")
    if args.boundary_target < 1:
        raise ValueError("--boundary-target must be >= 1")
    project_dir = Path(__file__).resolve().parent
    operator_statistics = None
    if args.operator_statistics:
        operator_statistics = json.loads(
            Path(args.operator_statistics).read_text(encoding="utf-8")
        )
    records = load_json_records(args.input, stage="multi_operator_search")
    route_manifest = _route_integrity_manifest(records)
    manifest_config = _search_manifest_config(args)
    output_path = Path(args.output)
    branch_artifact_path = Path(args.work_dir) / "branch_results.jsonl"
    if output_path.exists():
        valid, reason = validate_published_artifact(
            args.output,
            stage="multi_operator_search",
            input_path=args.input,
            config=manifest_config,
        )
        if not valid:
            raise ValueError(
                "existing search artifact is incompatible or incomplete "
                f"({reason}); start a new experiment instead of mixing route revisions"
            )
        manifest = read_manifest(args.output) or {}
        if manifest.get("route_integrity") != route_manifest:
            raise ValueError("existing search manifest route identity mismatch")
        _validate_branch_artifact_routes(branch_artifact_path, route_manifest)
        return
    runner = MultiOperatorSearchRunner(
        project_dir=project_dir,
        work_dir=Path(args.work_dir),
        memory_dir=Path(args.memory_dir),
        branch_window=args.branch_window,
        boundary_target=args.boundary_target,
        operator_sort_mode=args.operator_sort_mode,
        operator_statistics=operator_statistics,
        exploration_ratio=args.exploration_ratio,
        assignment_mode=args.assignment_mode,
        max_iterations=args.max_iterations,
        rule_only_difficulty=args.rule_only_difficulty,
        defer_gpt_experimental_evaluation=args.defer_gpt_experimental_evaluation,
        artifact_retention=args.artifact_retention,
    )
    final_records = (
        runner.run_stream(records)
        if args.pipeline_mode == "stream"
        else runner.run(records, output_path=Path(args.output))
    )
    search_summary_path = Path(args.work_dir) / "search_summary.json"
    branch_count = (
        sum(
            1
            for line in branch_artifact_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
        if branch_artifact_path.exists()
        else 0
    )
    publish_records(
        final_records,
        args.output,
        stage="multi_operator_search",
        input_path=args.input,
        config=manifest_config,
        code_paths=[__file__, str(project_dir / "search_coordinator.py")],
        performance_path=str(Path(args.work_dir) / "performance_events.jsonl"),
        sidecars=[
            *(
                [(str(branch_artifact_path), "branch_results", branch_count)]
                if branch_artifact_path.exists()
                else []
            ),
            (str(search_summary_path), "search_performance_summary", 1),
        ],
        extra_manifest={"route_integrity": route_manifest},
    )
    # Cleanup is deliberately last: step-mode wave files remain available if
    # final publication fails, while successful runs retain only canonical
    # branch results and recovery-safe terminal checkpoints.
    runner.cleanup_published_intermediates(args.pipeline_mode)


if __name__ == "__main__":
    main()
