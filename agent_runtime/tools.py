"""Registered, contract-driven tools for Question Evolution entry points."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from .events import append_event, summarize_text
from .policy import validate_env_overrides
from .task import AgentTask


Runner = Callable[..., subprocess.CompletedProcess[str]]
_EXPERIMENT_DIR_LINE = re.compile(r"^本次实验目录:\s*(.+?)\s*$", re.MULTILINE)
_RETRYABLE_OUTPUT = re.compile(r"timeout|timed out|rate.?limit|too many requests|temporar(?:y|ily)|connection reset|file lock", re.I)
_FATAL_OUTPUT = re.compile(r"schema|manifest|artifact.*hash|checkpoint.*(?:mismatch|identity)|input.*(?:missing|not found)", re.I)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0.0


@dataclass(frozen=True)
class ToolSpec:
    tool_name: str
    version: str
    kind: str
    input_schema: str
    output_schema: str
    side_effects: bool
    idempotency_key_fields: tuple[str, ...]
    timeout_seconds: int
    retry_policy: RetryPolicy
    expected_artifacts: tuple[str, ...]
    observation_types: tuple[str, ...]

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["idempotency_key_fields"] = list(self.idempotency_key_fields)
        value["expected_artifacts"] = list(self.expected_artifacts)
        value["observation_types"] = list(self.observation_types)
        return value


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    tool_call_id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:16]}")
    idempotency_key: str = ""


TOOL_SPECS: Dict[str, ToolSpec] = {
    "check_environment": ToolSpec("check_environment", "1.0", "composite", "agent_task.schema.json", "agent_tool_result.schema.json", False, ("input_file",), 120, RetryPolicy(1), ("environment_checked",), ("environment_ready", "tool_retryable_failure", "tool_fatal_failure")),
    "run_full_loop": ToolSpec("run_full_loop", "1.0", "composite", "agent_task.schema.json", "agent_tool_result.schema.json", True, ("input_file", "search_mode", "execution_scope"), 7200, RetryPolicy(2, 0.25), ("final/final_scored.jsonl",), ("pipeline_started", "pipeline_completed", "tool_retryable_failure", "tool_fatal_failure", "artifact_missing")),
    "resume_full_loop": ToolSpec("resume_full_loop", "1.0", "composite", "agent_task.schema.json", "agent_tool_result.schema.json", True, ("resume_exp_dir", "resume_start_round", "execution_scope"), 7200, RetryPolicy(2, 0.25), ("final/final_scored.jsonl",), ("pipeline_started", "pipeline_completed", "tool_retryable_failure", "tool_fatal_failure", "artifact_missing")),
    "observe_experiment": ToolSpec("observe_experiment", "1.0", "composite", "agent_observation.schema.json", "agent_observation.schema.json", False, ("experiment_dir",), 60, RetryPolicy(1), ("agent_observation.json",), ("score_decreased", "score_unchanged", "score_increased", "not_applicable", "candidate_invalid", "boundary_candidate_found", "budget_warning", "artifact_missing", "manifest_corrupted")),
    "write_agent_report": ToolSpec("write_agent_report", "1.0", "composite", "agent_observation.schema.json", "agent_tool_result.schema.json", True, ("agent_run_id", "plan_revision"), 30, RetryPolicy(1), ("agent_report.md",), ("review_report_ready", "tool_fatal_failure")),
}


class ToolExecutionError(RuntimeError):
    pass


def get_tool_spec(tool_name: str) -> ToolSpec:
    try:
        return TOOL_SPECS[tool_name]
    except KeyError as exc:
        raise ToolExecutionError(f"unregistered tool: {tool_name}") from exc


def list_tool_specs() -> Dict[str, Dict[str, Any]]:
    return {name: spec.as_dict() for name, spec in TOOL_SPECS.items()}


def classify_system_failure(error: Any, *, timed_out: bool = False) -> tuple[str, bool]:
    """Return a stable category and retryability for executor/reporting logic."""

    text = str(error)
    if timed_out or _RETRYABLE_OUTPUT.search(text):
        return "retryable_system_error", True
    if _FATAL_OUTPUT.search(text):
        return "fatal_system_error", False
    return "tool_execution_error", False


class ToolRegistry:
    """Only exposes named, versioned project capabilities to the Agent."""

    def __init__(self, *, project_root: Path, run_dir: Path, runner: Runner = subprocess.run, sleeper: Callable[[float], None] = time.sleep):
        self.project_root = project_root.resolve()
        self.run_dir = run_dir
        self.runner = runner
        self.sleeper = sleeper
        self.events_path = run_dir / "agent_events.jsonl"

    @property
    def specs(self) -> Dict[str, ToolSpec]:
        return dict(TOOL_SPECS)

    def _execute(
        self,
        tool: str,
        command: list[str],
        *,
        env_overrides: Mapping[str, Any],
        tool_call_id: str = "",
        idempotency_key: str = "",
        record_events: bool = True,
    ) -> Dict[str, Any]:
        spec = get_tool_spec(tool)
        allowed_env = validate_env_overrides(env_overrides)
        environment = os.environ.copy()
        environment.update(allowed_env)
        call_id = tool_call_id or f"call_{uuid.uuid4().hex[:16]}"
        attempts = max(1, spec.retry_policy.max_attempts)
        last_result: Dict[str, Any] = {}

        for attempt in range(1, attempts + 1):
            if record_events:
                append_event(self.events_path, "tool_started", {
                    "tool": tool, "tool_version": spec.version, "tool_call_id": call_id,
                    "idempotency_key": idempotency_key, "attempt": attempt,
                    "timeout_seconds": spec.timeout_seconds, "command": command, "env_keys": sorted(allowed_env),
                })
            started = time.monotonic()
            try:
                completed = self.runner(command, cwd=str(self.project_root), env=environment, text=True, capture_output=True, check=False, timeout=spec.timeout_seconds)
                stdout, stderr = completed.stdout or "", completed.stderr or ""
                ok = completed.returncode == 0
                category, retryable = ("", False) if ok else classify_system_failure(stderr or stdout)
                return_code = int(completed.returncode)
            except subprocess.TimeoutExpired as exc:
                stdout, stderr = "", str(exc)
                ok, return_code = False, -1
                category, retryable = classify_system_failure(exc, timed_out=True)
            except OSError as exc:
                stdout, stderr = "", str(exc)
                ok, return_code = False, -1
                category, retryable = classify_system_failure(exc)

            duration = round(time.monotonic() - started, 6)
            result = {
                "tool": tool, "tool_version": spec.version, "tool_call_id": call_id,
                "idempotency_key": idempotency_key, "ok": ok, "return_code": return_code,
                "duration_seconds": duration, "retry_count": attempt - 1,
                "failure_category": category or None, "recoverable": retryable,
                "stdout_summary": summarize_text(stdout), "stderr_summary": summarize_text(stderr),
                "cost": {"known_cost": None, "unit": "not_reported"}, "_stdout": stdout, "_stderr": stderr,
            }
            last_result = result
            if ok:
                if record_events:
                    append_event(self.events_path, "tool_completed", {key: value for key, value in result.items() if not key.startswith("_")})
                return result

            will_retry = retryable and attempt < attempts
            if record_events:
                append_event(self.events_path, "tool_failed", {
                    **{key: value for key, value in result.items() if not key.startswith("_")},
                    "will_retry": will_retry,
                    "retry_backoff_seconds": spec.retry_policy.backoff_seconds if will_retry else 0,
                })
            if will_retry and spec.retry_policy.backoff_seconds:
                self.sleeper(spec.retry_policy.backoff_seconds)
                continue
            return result
        return last_result

    def check_environment(self, task: AgentTask, *, tool_call_id: str = "", idempotency_key: str = "", record_events: bool = True) -> Dict[str, Any]:
        command = [sys.executable, "check_runtime_environment.py", "--input-file", task.input_file, "--json"]
        result = self._execute("check_environment", command, env_overrides={}, tool_call_id=tool_call_id, idempotency_key=idempotency_key, record_events=record_events)
        parsed: Optional[Dict[str, Any]] = None
        if result["_stdout"].strip():
            try:
                parsed = json.loads(result["_stdout"])
            except json.JSONDecodeError:
                parsed = None
        result["report"] = parsed
        result["ready"] = bool(parsed and parsed.get("ready_for_real_stage06_e2e"))
        result.pop("_stdout", None)
        result.pop("_stderr", None)
        return result

    def _bash_path(self) -> str:
        bash = shutil.which("bash")
        if not bash:
            raise ToolExecutionError("bash is required for the registered run_loop.sh entry point")
        return bash

    def _locate_experiment_dir(self, stdout: str, exp_root: str) -> Optional[str]:
        match = _EXPERIMENT_DIR_LINE.search(stdout)
        if match:
            value = Path(match.group(1).strip())
            candidate = value.resolve() if value.is_absolute() else (self.project_root / value).resolve()
            if candidate.is_dir():
                return str(candidate)
        root = Path(exp_root)
        root = root.resolve() if root.is_absolute() else (self.project_root / root).resolve()
        if not root.is_dir():
            return None
        candidates = [path for path in root.glob("*/*") if path.is_dir() and (path / "summary.txt").is_file()]
        return str(max(candidates, key=lambda path: path.stat().st_mtime).resolve()) if candidates else None

    def run_full_loop(self, task: AgentTask, env_overrides: Mapping[str, Any], *, tool_call_id: str = "", idempotency_key: str = "", record_events: bool = True) -> Dict[str, Any]:
        result = self._execute("run_full_loop", [self._bash_path(), "run_loop.sh"], env_overrides=env_overrides, tool_call_id=tool_call_id, idempotency_key=idempotency_key, record_events=record_events)
        result["experiment_dir"] = self._locate_experiment_dir(result.pop("_stdout", ""), str(env_overrides.get("EXP_ROOT", task.exp_root)))
        result.pop("_stderr", None)
        return result

    def resume_full_loop(self, task: AgentTask, env_overrides: Mapping[str, Any], *, tool_call_id: str = "", idempotency_key: str = "", record_events: bool = True) -> Dict[str, Any]:
        if not task.resume_exp_dir or not task.resume_start_round:
            raise ToolExecutionError("resume_full_loop requires resume_exp_dir and resume_start_round")
        result = self._execute("resume_full_loop", [self._bash_path(), "run_loop.sh", "--resume-exp-dir", task.resume_exp_dir], env_overrides=env_overrides, tool_call_id=tool_call_id, idempotency_key=idempotency_key, record_events=record_events)
        result.pop("_stdout", None)
        result.pop("_stderr", None)
        result["experiment_dir"] = str(Path(task.resume_exp_dir).resolve())
        result["resume_start_round"] = task.resume_start_round
        return result
