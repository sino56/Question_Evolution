"""Registered subprocess tools for the existing Question Evolution entry points."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

from .events import append_event, summarize_text
from .policy import validate_env_overrides
from .task import AgentTask


Runner = Callable[..., subprocess.CompletedProcess[str]]
_EXPERIMENT_DIR_LINE = re.compile(r"^本次实验目录:\s*(.+?)\s*$", re.MULTILINE)


class ToolExecutionError(RuntimeError):
    pass


class ToolRegistry:
    """Thin, testable wrapper around only the approved project entry points."""

    def __init__(self, *, project_root: Path, run_dir: Path, runner: Runner = subprocess.run):
        self.project_root = project_root.resolve()
        self.run_dir = run_dir
        self.runner = runner
        self.events_path = run_dir / "agent_events.jsonl"

    def _execute(self, tool: str, command: list[str], *, env_overrides: Mapping[str, Any], recoverable: bool = False) -> Dict[str, Any]:
        allowed_env = validate_env_overrides(env_overrides)
        environment = os.environ.copy()
        environment.update(allowed_env)
        append_event(self.events_path, "tool_started", {"tool": tool, "command": command, "env_keys": sorted(allowed_env)})
        started = time.monotonic()
        try:
            completed = self.runner(
                command,
                cwd=str(self.project_root),
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            duration = round(time.monotonic() - started, 6)
            append_event(self.events_path, "tool_failed", {
                "tool": tool,
                "return_code": -1,
                "duration_seconds": duration,
                "stderr_summary": summarize_text(exc),
                "recoverable": recoverable,
            })
            return {"tool": tool, "ok": False, "return_code": -1, "recoverable": recoverable, "stderr": str(exc)}

        duration = round(time.monotonic() - started, 6)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        result = {
            "tool": tool,
            "ok": completed.returncode == 0,
            "return_code": int(completed.returncode),
            "duration_seconds": duration,
            "stdout": stdout,
            "stderr": stderr,
            "recoverable": recoverable,
        }
        event_type = "tool_completed" if result["ok"] else "tool_failed"
        append_event(self.events_path, event_type, {
            "tool": tool,
            "return_code": result["return_code"],
            "duration_seconds": duration,
            "stdout_summary": summarize_text(stdout),
            "stderr_summary": summarize_text(stderr),
            "recoverable": recoverable,
        })
        return result

    def check_environment(self, task: AgentTask) -> Dict[str, Any]:
        command = [sys.executable, "check_runtime_environment.py", "--input-file", task.input_file, "--json"]
        result = self._execute("check_environment", command, env_overrides={})
        parsed: Optional[Dict[str, Any]] = None
        if result["stdout"].strip():
            try:
                parsed = json.loads(result["stdout"])
            except json.JSONDecodeError:
                parsed = None
        result["report"] = parsed
        result["ready"] = bool(parsed and parsed.get("ready_for_real_stage06_e2e"))
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
        if not candidates:
            return None
        return str(max(candidates, key=lambda path: path.stat().st_mtime).resolve())

    def run_full_loop(self, task: AgentTask, env_overrides: Mapping[str, Any]) -> Dict[str, Any]:
        result = self._execute(
            "run_full_loop",
            [self._bash_path(), "run_loop.sh"],
            env_overrides=env_overrides,
        )
        result["experiment_dir"] = self._locate_experiment_dir(result["stdout"], str(env_overrides.get("EXP_ROOT", task.exp_root)))
        return result

    def resume_full_loop(self, task: AgentTask, env_overrides: Mapping[str, Any]) -> Dict[str, Any]:
        if not task.resume_exp_dir or not task.resume_start_round:
            raise ToolExecutionError("resume_full_loop requires resume_exp_dir and resume_start_round")
        # The current project resumes verified artifacts from its existing
        # directory.  It does not expose a separate resume_run_loop.sh or a
        # start-round flag, so the round is retained as audited task metadata.
        result = self._execute(
            "resume_full_loop",
            [self._bash_path(), "run_loop.sh", "--resume-exp-dir", task.resume_exp_dir],
            env_overrides=env_overrides,
        )
        result["experiment_dir"] = str(Path(task.resume_exp_dir).resolve())
        result["resume_start_round"] = task.resume_start_round
        return result
