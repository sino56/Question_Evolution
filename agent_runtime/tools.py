"""Registered, contract-driven tools for Question Evolution entry points."""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from .env_contract import INNER_LOOP_MARKER, resolved_environment
from .events import append_event, summarize_text
from .policy import validate_env_overrides
from .task import AgentTask


Runner = Callable[..., subprocess.CompletedProcess[str]]
_EXPERIMENT_DIR_LINE = re.compile(r"^本次实验目录:\s*(.+?)\s*$", re.MULTILINE)
_EXPERIMENT_DIR_FILE = "experiment_dir.txt"
_EXPERIMENT_STATISTICS_FILE = "experiment_statistics.json"
# Measurement keys published by the pipeline's experiment statistics artifact.
_COST_MEASUREMENT_KEYS = (
    "total_cost",
    "cost",
    "request_count",
    "evaluation_count",
    "model_calls",
    "duration_seconds",
    "elapsed_seconds",
)
# Preferred structured failure contract.  The pipeline may print
# ``ERROR_CATEGORY=fatal_system_error`` instead of relying on prose.
_STRUCTURED_ERROR_CATEGORY = re.compile(
    r"ERROR_CATEGORY\s*[=:]\s*(retryable_system_error|fatal_system_error|tool_execution_error)\b"
)
_RETRYABLE_OUTPUT = re.compile(r"timeout|timed out|rate.?limit|too many requests|temporar(?:y|ily)|connection reset|file lock", re.I)
# Fatal patterns are deliberately specific.  A bare word such as "schema" in
# model output or a business message must not be classified as unrecoverable.
_FATAL_OUTPUT = re.compile(
    r"(?:"
    r"schema[\s_-]*(?:mismatch|invalid|validation|violation|error)"
    r"|manifest[\s_-]*(?:missing|corrupt|invalid|mismatch)"
    r"|artifact[^\n]{0,40}hash"
    r"|checkpoint[^\n]{0,20}(?:mismatch|identity)"
    r"|input\s+file[^\n]{0,24}(?:missing|not found)"
    r"|no such file"
    r")",
    re.I,
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_seconds: float = 0.0
    multiplier: float = 2.0
    max_backoff_seconds: float = 60.0

    def backoff_for(self, attempt: int) -> float:
        """Exponential backoff with a hard cap; attempt is 1-based."""

        if self.backoff_seconds <= 0:
            return 0.0
        exponent = max(0, int(attempt) - 1)
        return round(min(self.backoff_seconds * (self.multiplier ** exponent), self.max_backoff_seconds), 6)


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
    cost_policy: str = "not_reported"

    def as_dict(self) -> Dict[str, Any]:
        value = asdict(self)
        value["idempotency_key_fields"] = list(self.idempotency_key_fields)
        value["expected_artifacts"] = list(self.expected_artifacts)
        value["observation_types"] = list(self.observation_types)
        return value


def cost_estimate(spec: ToolSpec) -> Dict[str, Any]:
    """Return the declared cost dimension for a tool result.

    The previous implementation hard-coded ``{known_cost: None,
    unit: not_reported}`` for every tool, which made the design's cost
    accounting impossible.  Local composite tools now declare a real zero
    cost; model-billed tools declare that their cost is not reported by the
    pipeline yet.
    """

    if spec.cost_policy == "local":
        return {"known_cost": 0.0, "unit": "local", "cost_policy": spec.cost_policy}
    return {"known_cost": None, "unit": "not_reported", "cost_policy": spec.cost_policy}


@dataclass(frozen=True)
class ToolCall:
    tool_name: str
    inputs: Mapping[str, Any] = field(default_factory=dict)
    tool_call_id: str = field(default_factory=lambda: f"call_{uuid.uuid4().hex[:16]}")
    idempotency_key: str = ""


TOOL_SPECS: Dict[str, ToolSpec] = {
    "check_environment": ToolSpec("check_environment", "1.0", "composite", "agent_task.schema.json", "agent_tool_result.schema.json", False, ("input_file",), 120, RetryPolicy(1), ("environment_checked",), ("environment_ready", "tool_retryable_failure", "tool_fatal_failure"), "local"),
    "run_full_loop": ToolSpec("run_full_loop", "1.0", "composite", "agent_task.schema.json", "agent_tool_result.schema.json", True, ("input_file", "search_mode", "execution_scope", "exp_root", "max_search_steps", "boundary_target", "memory_snapshot_id"), 7200, RetryPolicy(2, 0.25), ("final/final_scored.jsonl",), ("pipeline_started", "pipeline_completed", "tool_retryable_failure", "tool_fatal_failure", "artifact_missing"), "model_billed"),
    "resume_full_loop": ToolSpec("resume_full_loop", "1.0", "composite", "agent_task.schema.json", "agent_tool_result.schema.json", True, ("resume_exp_dir", "resume_start_round", "execution_scope"), 7200, RetryPolicy(2, 0.25), ("final/final_scored.jsonl",), ("pipeline_started", "pipeline_completed", "tool_retryable_failure", "tool_fatal_failure", "artifact_missing"), "model_billed"),
    "observe_experiment": ToolSpec("observe_experiment", "1.0", "composite", "agent_observation.schema.json", "agent_observation.schema.json", False, ("experiment_dir",), 60, RetryPolicy(1), ("agent_observation.json",), ("score_decreased", "score_unchanged", "score_increased", "not_applicable", "candidate_invalid", "boundary_candidate_found", "budget_warning", "artifact_missing", "manifest_corrupted", "effective_boundary_found", "judge_instability_detected", "memory_written"), "local"),
    "write_agent_report": ToolSpec("write_agent_report", "1.0", "composite", "agent_observation.schema.json", "agent_tool_result.schema.json", True, ("agent_run_id", "plan_revision"), 30, RetryPolicy(1), ("agent_report.md",), ("review_report_ready", "tool_fatal_failure"), "local"),
}


def _scan_orphan_descendants(pid: int) -> str:
    """Best-effort orphan scan after a timeout kill.

    Returns ``"clean"`` / ``"residual"`` / ``"not_checked"``.  The three-state
    result mirrors the manifest contract: an unsupported platform must report
    ``not_checked`` rather than claim a clean sweep it never performed.
    """

    if os.name == "nt":
        return "not_checked"
    try:
        completed = subprocess.run(
            ["pgrep", "-P", str(pid)], capture_output=True, text=True, check=False, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return "not_checked"
    if completed.returncode != 0:
        return "clean"
    return "residual" if completed.stdout.strip() else "clean"


def terminate_process_tree(process: "subprocess.Popen[str]", *, grace_seconds: float = 10.0) -> Dict[str, Any]:
    """Terminate a subprocess *and its children*, then scan for orphans.

    ``subprocess.run(timeout=...)`` only kills the direct child, so a timed-out
    ``bash run_loop.sh`` previously left orphan python processes that kept
    calling the model API.  The runner below starts each command in its own
    process group/session and terminates the whole tree on timeout, and this
    helper additionally reports whether anything survived the sweep.
    """

    if process.poll() is not None:
        return {"terminated": True, "orphan_scan": "clean"}
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True, check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                process.kill()
    except OSError:
        try:
            process.kill()
        except OSError:
            pass
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except OSError:
            pass
    terminated = process.poll() is not None
    return {"terminated": terminated, "orphan_scan": _scan_orphan_descendants(process.pid) if terminated else "residual"}


def run_in_process_group(
    command: Any,
    *,
    cwd: Any = None,
    env: Any = None,
    text: bool = True,
    capture_output: bool = False,
    check: bool = False,
    timeout: Optional[float] = None,
) -> "subprocess.CompletedProcess[str]":
    """``subprocess.run``-compatible runner with process-group timeouts."""

    popen_kwargs: Dict[str, Any] = {
        "cwd": cwd,
        "env": env,
        "text": text,
        "stdout": subprocess.PIPE if capture_output else None,
        "stderr": subprocess.PIPE if capture_output else None,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **popen_kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        cleanup = terminate_process_tree(process)
        stdout, stderr = process.communicate()
        # Surface the sweep result on the failure text so the tool result
        # carries auditable evidence of what the timeout actually cleaned up.
        suffix = f"\nprocess_tree_cleanup={json.dumps(cleanup, sort_keys=True)}"
        raise subprocess.TimeoutExpired(
            command, timeout,
            output=stdout or exc.output,
            stderr=(stderr or exc.stderr or "") + suffix,
        ) from exc
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


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
    """Return a stable category and retryability for executor/reporting logic.

    A structured ``ERROR_CATEGORY=...`` contract wins outright; the prose
    regexes below are only a compatibility fallback for entries that do not
    emit one (design §16.2 / report T-6).
    """

    text = str(error)
    if timed_out:
        return "retryable_system_error", True
    structured = _STRUCTURED_ERROR_CATEGORY.search(text)
    if structured:
        category = structured.group(1)
        return category, category == "retryable_system_error"
    if _RETRYABLE_OUTPUT.search(text):
        return "retryable_system_error", True
    if _FATAL_OUTPUT.search(text):
        return "fatal_system_error", False
    return "tool_execution_error", False


class ToolRegistry:
    """Only exposes named, versioned project capabilities to the Agent."""

    def __init__(self, *, project_root: Path, run_dir: Path, runner: Runner = run_in_process_group, sleeper: Callable[[float], None] = time.sleep):
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
        allow_retry: bool = True,
    ) -> Dict[str, Any]:
        spec = get_tool_spec(tool)
        allowed_env = validate_env_overrides(env_overrides)
        environment = os.environ.copy()
        environment.update(allowed_env)
        # Mark this process tree as *inside* the Agent control plane so
        # run_loop.sh refuses to start a nested Harness (report X-1/X-3).
        environment[INNER_LOOP_MARKER] = "1"
        call_id = tool_call_id or f"call_{uuid.uuid4().hex[:16]}"
        # A side-effecting tool whose Session already produced a result must not
        # restart the whole pipeline: the executor withholds the retry budget
        # (report R-6) and the withheld decision is recorded for audit.
        attempts = max(1, spec.retry_policy.max_attempts) if allow_retry else 1
        if not allow_retry and record_events:
            append_event(self.events_path, "tool_retry_withheld", {
                "tool": tool, "tool_version": spec.version, "tool_call_id": call_id,
                "idempotency_key": idempotency_key,
                "reason": "side_effecting_tool_already_has_a_successful_session_record",
            })

        for attempt in range(1, attempts + 1):
            if record_events:
                append_event(self.events_path, "tool_started", {
                    "tool": tool, "tool_version": spec.version, "tool_call_id": call_id,
                    "idempotency_key": idempotency_key, "attempt": attempt,
                    "timeout_seconds": spec.timeout_seconds, "command": command, "env_keys": sorted(allowed_env),
                    "resolved_env": resolved_environment(environment),
                })
            started = time.monotonic()
            try:
                completed = self.runner(command, cwd=str(self.project_root), env=environment, text=True, capture_output=True, check=False, timeout=spec.timeout_seconds)
                stdout, stderr = completed.stdout or "", completed.stderr or ""
                ok = completed.returncode == 0
                category, retryable = ("", False) if ok else classify_system_failure(stderr or stdout)
                return_code = int(completed.returncode)
            except subprocess.TimeoutExpired as exc:
                # Keep the captured streams: the process-group runner attaches
                # its cleanup report to ``exc.stderr``, and dropping it would
                # make the timeout sweep unauditable (T-3).
                stdout = str(exc.output or "")
                stderr = str(exc.stderr or str(exc))
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
                "cost": cost_estimate(spec), "_stdout": stdout, "_stderr": stderr,
            }
            if ok:
                if record_events:
                    append_event(self.events_path, "tool_completed", {key: value for key, value in result.items() if not key.startswith("_")})
                return result

            will_retry = retryable and attempt < attempts
            backoff = spec.retry_policy.backoff_for(attempt) if will_retry else 0.0
            if record_events:
                append_event(self.events_path, "tool_failed", {
                    **{key: value for key, value in result.items() if not key.startswith("_")},
                    "will_retry": will_retry,
                    "retry_backoff_seconds": backoff,
                })
            if will_retry:
                # ``max_attempts`` is honoured regardless of backoff: a policy
                # with no delay must still retry, not silently stop at one try.
                if backoff > 0:
                    self.sleeper(backoff)
                continue
            return result
        # ``attempts >= 1`` and every iteration returns, so the loop can never
        # fall through; the previous unreachable ``return last_result`` is gone.
        raise ToolExecutionError(f"{tool} produced no result")

    def check_environment(self, task: AgentTask, *, tool_call_id: str = "", idempotency_key: str = "", record_events: bool = True, allow_retry: bool = True) -> Dict[str, Any]:
        command = [sys.executable, "check_runtime_environment.py", "--input-file", task.input_file, "--json"]
        result = self._execute("check_environment", command, env_overrides={}, tool_call_id=tool_call_id, idempotency_key=idempotency_key, record_events=record_events, allow_retry=allow_retry)
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

    def _resolve_exp_root(self, exp_root: str) -> Optional[Path]:
        if not exp_root:
            return None
        root = Path(exp_root)
        root = root.resolve() if root.is_absolute() else (self.project_root / root).resolve()
        return root if root.is_dir() else None

    def _experiment_dirs(self, exp_root: str) -> set[str]:
        root = self._resolve_exp_root(exp_root)
        if root is None:
            return set()
        return {str(path.resolve()) for path in root.glob("*/*") if path.is_dir()}

    def _locate_experiment_dir(self, stdout: str, exp_root: str, *, before: set[str]) -> Optional[str]:
        """Resolve the experiment directory from an explicit contract only.

        Precedence: (1) the declared ``本次实验目录:`` line, (2) exactly one
        newly created directory under ``EXP_ROOT``.  The previous "newest
        mtime" fallback could silently hand a *previous* experiment to the
        observer; guessing is now forbidden and an unresolvable directory stays
        ``None`` so the artifact gate fails loudly instead.
        """

        match = _EXPERIMENT_DIR_LINE.search(stdout)
        if match:
            value = Path(match.group(1).strip())
            candidate = value.resolve() if value.is_absolute() else (self.project_root / value).resolve()
            if candidate.is_dir():
                return str(candidate)
        current = self._experiment_dirs(exp_root)
        created = sorted(current - before)
        if len(created) == 1:
            return created[0]
        return None

    def _write_experiment_dir(self, value: Optional[str]) -> None:
        """Record the resolved experiment directory as an explicit artifact."""

        self.run_dir.mkdir(parents=True, exist_ok=True)
        (self.run_dir / _EXPERIMENT_DIR_FILE).write_text((value or "") + "\n", encoding="utf-8")

    def _backfill_cost(self, result: Dict[str, Any]) -> None:
        """Backfill ``cost`` from the pipeline's own statistics artifact (T-7).

        The declared cost used to be a constant ``not_reported`` for every
        tool, which made the design's per-boundary cost accounting impossible.
        When the experiment published ``experiment_statistics.json``, its real
        measurements replace the placeholder.
        """

        experiment_dir = result.get("experiment_dir")
        if not experiment_dir:
            return
        path = Path(str(experiment_dir)) / _EXPERIMENT_STATISTICS_FILE
        if not path.is_file():
            return
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(payload, Mapping):
            return
        measurements = {key: payload[key] for key in _COST_MEASUREMENT_KEYS if key in payload}
        if not measurements:
            return
        cost = dict(result.get("cost") or {})
        measured_cost = measurements.get("total_cost", measurements.get("cost"))
        if isinstance(measured_cost, (int, float)) and not isinstance(measured_cost, bool):
            cost["known_cost"] = float(measured_cost)
            cost["unit"] = cost.get("unit") or "model_billed"
        cost["measurements"] = measurements
        cost["source_ref"] = str(path.resolve())
        result["cost"] = cost

    def run_full_loop(self, task: AgentTask, env_overrides: Mapping[str, Any], *, tool_call_id: str = "", idempotency_key: str = "", record_events: bool = True, allow_retry: bool = True) -> Dict[str, Any]:
        exp_root = str(env_overrides.get("EXP_ROOT", task.exp_root))
        before = self._experiment_dirs(exp_root)
        result = self._execute("run_full_loop", [self._bash_path(), "run_loop.sh"], env_overrides=env_overrides, tool_call_id=tool_call_id, idempotency_key=idempotency_key, record_events=record_events, allow_retry=allow_retry)
        result["experiment_dir"] = self._locate_experiment_dir(result.pop("_stdout", ""), exp_root, before=before)
        result.pop("_stderr", None)
        self._write_experiment_dir(result["experiment_dir"])
        self._backfill_cost(result)
        return result

    def resume_full_loop(self, task: AgentTask, env_overrides: Mapping[str, Any], *, tool_call_id: str = "", idempotency_key: str = "", record_events: bool = True, allow_retry: bool = True) -> Dict[str, Any]:
        if not task.resume_exp_dir or not task.resume_start_round:
            raise ToolExecutionError("resume_full_loop requires resume_exp_dir and resume_start_round")
        result = self._execute("resume_full_loop", [self._bash_path(), "run_loop.sh", "--resume-exp-dir", task.resume_exp_dir], env_overrides=env_overrides, tool_call_id=tool_call_id, idempotency_key=idempotency_key, record_events=record_events, allow_retry=allow_retry)
        result.pop("_stdout", None)
        result.pop("_stderr", None)
        result["experiment_dir"] = str(Path(task.resume_exp_dir).resolve())
        result["resume_start_round"] = task.resume_start_round
        self._write_experiment_dir(result["experiment_dir"])
        self._backfill_cost(result)
        return result
