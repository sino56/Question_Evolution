"""Deterministic execution of validated Agent plan steps.

The executor deliberately contains no planning logic.  It only invokes
registered capabilities, validates their formal outputs, records checkpoints,
and preserves enough structured result data for observation and reporting.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional

from pipeline_runtime import validate_published_artifact

from .events import append_event, redact
from .observer import normalize_tool_result
from .policy import validate_plan
from .task import AgentTask, REGISTERED_TOOLS
from .tools import ToolExecutionError, get_tool_spec


class ExecutorError(RuntimeError):
    """A precondition, budget, idempotency, or artifact failure."""


class Executor:
    """Execute one previously validated plan with no open-ended reasoning."""

    def __init__(
        self,
        *,
        task: AgentTask,
        plan: Mapping[str, Any],
        registry: Any,
        run_dir: str | Path,
        state: MutableMapping[str, Any],
        observe: Callable[..., Dict[str, Any]],
        update_state: Callable[..., Any],
    ) -> None:
        self.task = task
        self.plan = plan
        # The CLI validates before construction; repeat the guard when a full
        # plan is supplied so direct runtime use cannot bypass Policy/Plan
        # validation. Small unit-level step tests may intentionally omit it.
        if "steps" in plan:
            validate_plan(task, plan)
        self.registry = registry
        self.run_dir = Path(run_dir)
        self.state = state
        self.observe = observe
        self.update_state = update_state
        self.events_path = self.run_dir / "agent_events.jsonl"
        self.ledger_path = self.run_dir / "tool_idempotency.json"
        self.ledger = self._load_ledger()
        self.results: list[Dict[str, Any]] = []
        self.normalized_observations: list[Dict[str, Any]] = []
        self.observation: Optional[Dict[str, Any]] = None

    def _record_observations(self, result: Mapping[str, Any]) -> None:
        aggregate = result.get("observation") if isinstance(result.get("observation"), Mapping) else None
        items = list(aggregate.get("observations") or []) if aggregate else normalize_tool_result(result)
        target = self.run_dir / "agent_observation_timeline.jsonl"
        with target.open("a", encoding="utf-8") as handle:
            for item in items:
                safe_item = redact(item)
                handle.write(json.dumps(safe_item, ensure_ascii=False, sort_keys=True) + "\n")
                append_event(self.events_path, "observation_created", safe_item)
                self.normalized_observations.append(safe_item)

    def _load_ledger(self) -> Dict[str, Dict[str, Any]]:
        if not self.ledger_path.exists():
            return {}
        try:
            value = json.loads(self.ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ExecutorError(f"idempotency ledger is unreadable: {exc}") from exc
        if not isinstance(value, dict):
            raise ExecutorError("idempotency ledger must be a JSON object")
        return {str(key): dict(item) for key, item in value.items() if isinstance(item, Mapping)}

    def _save_ledger(self) -> None:
        temporary = self.ledger_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(self.ledger_path)

    def _idempotency_key(self, step: Mapping[str, Any]) -> str:
        spec = get_tool_spec(str(step["tool_name"]))
        inputs = dict(step.get("arguments") or step.get("inputs") or {})
        values = {field: inputs.get(field, getattr(self.task, field, None)) for field in spec.idempotency_key_fields}
        payload = {"tool": spec.tool_name, "version": spec.version, "plan_id": self.plan.get("plan_id"), "inputs": redact(values)}
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _validate_step(self, step: Mapping[str, Any]) -> None:
        tool = str(step.get("tool_name") or step.get("tool") or "")
        if tool not in REGISTERED_TOOLS or tool not in self.task.allowed_tools:
            raise ExecutorError(f"step uses an unregistered or unauthorized tool: {tool}")
        get_tool_spec(tool)
        completed = set(self.state.get("completed_step_ids") or [])
        missing_dependencies = set(step.get("depends_on") or []) - completed
        if missing_dependencies:
            raise ExecutorError("step dependencies are not completed: " + ", ".join(sorted(missing_dependencies)))
        budget = step.get("budget_limit") or {}
        maximum = budget.get("max_tool_calls")
        if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1):
            raise ExecutorError("max_tool_calls must be a positive integer")
        if isinstance(maximum, int) and sum(1 for result in self.results if result.get("tool") == tool) >= maximum:
            raise ExecutorError(f"tool budget exhausted for {tool}")

    def _validate_preconditions(self, step: Mapping[str, Any]) -> None:
        prior = {str(result.get("tool")): result for result in self.results}
        for condition in step.get("preconditions") or []:
            if condition == "environment_checked":
                if not bool(prior.get("check_environment", {}).get("ready")):
                    raise ExecutorError("environment precondition is not satisfied")
            elif condition == "existing_experiment_dir":
                exp_dir = self.state.get("experiment_dir") or self.task.resume_exp_dir
                if not exp_dir or not Path(exp_dir).is_dir():
                    raise ExecutorError("existing experiment directory is missing")
            elif condition == "resume_checkpoint_valid":
                exp_dir = self.task.resume_exp_dir
                if not exp_dir or not Path(exp_dir).is_dir():
                    raise ExecutorError("resume checkpoint directory is missing")
            elif condition in {"published_manifest_validation_required", "real_scoring_required"}:
                # These are output gates, checked after the registered composite
                # tool runs; accepting them here never bypasses verification.
                continue
            else:
                raise ExecutorError(f"unsupported step precondition: {condition}")

    def _verify_outputs(self, step: Mapping[str, Any], result: Mapping[str, Any]) -> tuple[bool, str]:
        if not result.get("ok"):
            return False, str(result.get("failure_category") or "tool_execution_error")
        expected = set(step.get("expected_outputs") or [])
        tool = str(result.get("tool"))
        if tool == "check_environment" and "environment_checked" in expected and not result.get("ready"):
            return False, "environment_not_ready"
        if tool in {"run_full_loop", "resume_full_loop"}:
            exp_dir = result.get("experiment_dir")
            if "experiment_dir" in expected and (not exp_dir or not Path(str(exp_dir)).is_dir()):
                return False, "artifact_missing:experiment_dir"
            if "final/final_scored.jsonl" in expected:
                if not exp_dir:
                    return False, "artifact_missing:final/final_scored.jsonl"
                valid, reason = validate_published_artifact(str(Path(str(exp_dir)) / "final" / "final_scored.jsonl"))
                if not valid:
                    return False, f"artifact_missing:{reason}"
        if tool == "observe_experiment" and "agent_observation.json" in expected and not (self.run_dir / "agent_observation.json").is_file():
            return False, "artifact_missing:agent_observation.json"
        return True, "ok"

    def _invoke_registry(self, method_name: str, *args: Any, tool_call_id: str, idempotency_key: str, **kwargs: Any) -> Dict[str, Any]:
        method = getattr(self.registry, method_name)
        parameters = inspect.signature(method).parameters
        if "record_events" in parameters or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
            kwargs.update({"tool_call_id": tool_call_id, "idempotency_key": idempotency_key, "record_events": False})
        return dict(method(*args, **kwargs))

    def _run_step(self, step: Mapping[str, Any], *, idempotency_key: str, tool_call_id: str) -> Dict[str, Any]:
        tool = str(step["tool_name"])
        if tool == "check_environment":
            return self._invoke_registry("check_environment", self.task, tool_call_id=tool_call_id, idempotency_key=idempotency_key)
        if tool == "run_full_loop":
            return self._invoke_registry("run_full_loop", self.task, self.plan.get("env_overrides", {}), tool_call_id=tool_call_id, idempotency_key=idempotency_key)
        if tool == "resume_full_loop":
            return self._invoke_registry("resume_full_loop", self.task, self.plan.get("env_overrides", {}), tool_call_id=tool_call_id, idempotency_key=idempotency_key)
        if tool == "observe_experiment":
            exp_dir = self.state.get("experiment_dir") or self.task.resume_exp_dir
            if not exp_dir:
                raise ExecutorError("experiment directory could not be located")
            observed = self.observe(exp_dir, run_dir=self.run_dir, boundary_target=self.task.boundary_target, task_search_mode=str(self.plan.get("selected_search_mode") or ""))
            self.observation = observed
            return {"tool": tool, "tool_version": get_tool_spec(tool).version, "tool_call_id": tool_call_id, "idempotency_key": idempotency_key, "ok": observed.get("status") != "blocked", "return_code": 0 if observed.get("status") != "blocked" else 1, "duration_seconds": 0.0, "retry_count": 0, "failure_category": None if observed.get("status") != "blocked" else "fatal_system_error", "recoverable": False, "observation": observed, "cost": {"known_cost": 0, "unit": "local"}}
        raise ExecutorError(f"{tool} must be executed after a durable decision")

    def execute_step(self, step: Mapping[str, Any]) -> Dict[str, Any]:
        self._validate_step(step)
        self._validate_preconditions(step)
        tool = str(step["tool_name"])
        key = self._idempotency_key(step)
        if key in self.ledger and self.ledger[key].get("ok"):
            reused = {**self.ledger[key], "reused": True}
            append_event(self.events_path, "tool_reused", {"tool": tool, "idempotency_key": key, "tool_call_id": reused.get("tool_call_id")})
            self.results.append(reused)
            return reused

        call_id = f"call_{uuid.uuid4().hex[:16]}"
        spec = get_tool_spec(tool)
        append_event(self.events_path, "tool_started", {"tool": tool, "tool_version": spec.version, "tool_call_id": call_id, "idempotency_key": key, "timeout_seconds": spec.timeout_seconds})
        started = time.monotonic()
        try:
            result = self._run_step(step, idempotency_key=key, tool_call_id=call_id)
            result.setdefault("tool", tool)
            result.setdefault("tool_version", spec.version)
            result.setdefault("tool_call_id", call_id)
            result.setdefault("idempotency_key", key)
            result.setdefault("retry_count", 0)
            result.setdefault("recoverable", False)
            result.setdefault("cost", {"known_cost": None, "unit": "not_reported"})
            result["duration_seconds"] = round(float(result.get("duration_seconds") or (time.monotonic() - started)), 6)
            valid, reason = self._verify_outputs(step, result)
            if not valid:
                result.update({"ok": False, "recoverable": False, "failure_category": "fatal_system_error", "artifact_validation": reason})
        except (ExecutorError, ToolExecutionError, OSError, ValueError) as exc:
            result = {"tool": tool, "tool_version": spec.version, "tool_call_id": call_id, "idempotency_key": key, "ok": False, "return_code": -1, "duration_seconds": round(time.monotonic() - started, 6), "retry_count": 0, "failure_category": "fatal_system_error", "recoverable": False, "stderr_summary": str(exc), "cost": {"known_cost": None, "unit": "not_reported"}}
        self.ledger[key] = redact(result)
        self._save_ledger()
        self._record_observations(result)
        event_type = "tool_completed" if result.get("ok") else "tool_failed"
        append_event(self.events_path, event_type, result)
        self.results.append(result)
        if result.get("ok"):
            completed = list(self.state.get("completed_step_ids") or [])
            completed.append(str(step["step_id"]))
            self.update_state(self.run_dir, self.state, completed_step_ids=completed, current_step_id=None)
            append_event(self.events_path, "checkpoint_confirmed", {"step_id": step["step_id"], "tool": tool, "idempotency_key": key})
            if result.get("experiment_dir"):
                self.update_state(self.run_dir, self.state, experiment_dir=result["experiment_dir"])
        return result

    def execute(self, steps: Iterable[Mapping[str, Any]]) -> list[Dict[str, Any]]:
        for step in steps:
            if step.get("tool_name") == "write_agent_report":
                continue
            self.update_state(self.run_dir, self.state, status="observing" if step.get("tool_name") == "observe_experiment" else "executing", current_step_id=step.get("step_id"))
            result = self.execute_step(step)
            if not result.get("ok") and step.get("stop_if_failed", True):
                break
        return self.results

    def execute_report(self, step: Mapping[str, Any], writer: Callable[[], Path]) -> Dict[str, Any]:
        """Run the deferred reporting tool after the Decision is durable."""

        tool = str(step.get("tool_name") or "write_agent_report")
        if tool != "write_agent_report" or tool not in self.task.allowed_tools:
            raise ExecutorError("write_agent_report is not authorized by this plan")
        key = self._idempotency_key(step)
        call_id = f"call_{uuid.uuid4().hex[:16]}"
        spec = get_tool_spec(tool)
        append_event(self.events_path, "tool_started", {"tool": tool, "tool_version": spec.version, "tool_call_id": call_id, "idempotency_key": key, "timeout_seconds": spec.timeout_seconds})
        started = time.monotonic()
        try:
            report_path = writer()
            ok = Path(report_path).is_file()
            result = {"tool": tool, "tool_version": spec.version, "tool_call_id": call_id, "idempotency_key": key, "ok": ok, "return_code": 0 if ok else 1, "duration_seconds": round(time.monotonic() - started, 6), "retry_count": 0, "failure_category": None if ok else "fatal_system_error", "recoverable": False, "report_path": str(report_path), "cost": {"known_cost": 0, "unit": "local"}}
        except OSError as exc:
            result = {"tool": tool, "tool_version": spec.version, "tool_call_id": call_id, "idempotency_key": key, "ok": False, "return_code": -1, "duration_seconds": round(time.monotonic() - started, 6), "retry_count": 0, "failure_category": "fatal_system_error", "recoverable": False, "stderr_summary": str(exc), "cost": {"known_cost": 0, "unit": "local"}}
        append_event(self.events_path, "tool_completed" if result["ok"] else "tool_failed", result)
        self._record_observations(result)
        self.results.append(result)
        if result["ok"]:
            completed = list(self.state.get("completed_step_ids") or [])
            completed.append(str(step.get("step_id") or "write_agent_report"))
            self.update_state(self.run_dir, self.state, completed_step_ids=completed, current_step_id=None)
        return result
