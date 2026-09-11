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
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional

from pipeline_runtime import validate_published_artifact

from .contracts import ContractViolation, validate_contract
from .events import append_event, redact
from .budgeting import BudgetLedgerError, load_or_create_ledger, save_ledger
from .budgeting.budget_state import UNALLOCATED_TARGET
from .observer import normalize_tool_result
from .policy import ENV_ALLOWLIST, validate_env_overrides, validate_plan
from .task import AgentTask, REGISTERED_TOOLS
from .tools import ToolExecutionError, cost_estimate, get_tool_spec


_STEP_ARGUMENT_ENV_ALIASES = {
    "input_file": "INPUT_FILE",
    "exp_root": "EXP_ROOT",
    "search_mode": "SEARCH_MODE",
    "search_boundary_target": "SEARCH_BOUNDARY_TARGET",
    "boundary_target": "SEARCH_BOUNDARY_TARGET",
    "max_search_steps": "MAX_SEARCH_STEPS",
    "execution_scope": "EXECUTION_SCOPE",
}
# Positional tool parameters that are never environment overrides.
_STEP_ARGUMENT_POSITIONAL = {"experiment_dir", "start_round", "resume_exp_dir", "resume_start_round"}
# Step arguments that must stay integers when applied to an ``AgentTask``.
_STEP_ARGUMENT_INT_FIELDS = {
    "SEARCH_BOUNDARY_TARGET": "boundary_target",
    "MAX_SEARCH_STEPS": "max_search_steps",
}
_STEP_ARGUMENT_TASK_FIELDS = {
    "INPUT_FILE": "input_file",
    "EXP_ROOT": "exp_root",
    "SEARCH_MODE": "search_mode",
    "EXECUTION_SCOPE": "execution_scope",
    **{env: field for env, field in _STEP_ARGUMENT_INT_FIELDS.items()},
}


class ExecutorError(RuntimeError):
    """A precondition, budget, idempotency, or artifact failure."""


def _arguments_to_env_overrides(arguments: Mapping[str, Any]) -> Dict[str, str]:
    """Translate documented Step arguments into whitelisted env overrides.

    ``Step.arguments`` used to be decorative for composite tools: the executor
    passed ``plan.env_overrides`` and silently dropped everything a step
    declared, so a Planner could not parameterise a single step (design §9.2).

    Only the explicit alias map and the environment allowlist are accepted.  An
    unknown argument name is reported as a contract error instead of being
    dropped quietly -- a silently ignored argument is what made the original
    bug invisible in the first place.
    """

    resolved: Dict[str, str] = {}
    unknown: list[str] = []
    for raw_key, value in dict(arguments or {}).items():
        name = str(raw_key)
        if name in _STEP_ARGUMENT_POSITIONAL:
            continue
        env_key = _STEP_ARGUMENT_ENV_ALIASES.get(name, name)
        if env_key not in ENV_ALLOWLIST:
            # Accept snake_case spellings of declared environment variables so
            # a step can parameterise itself naturally (e.g. ``search_max_depth``).
            env_key = env_key.upper()
        if env_key in ENV_ALLOWLIST:
            resolved[env_key] = value
        else:
            unknown.append(name)
    if unknown:
        raise ExecutorError(
            "step arguments are not executable: "
            + ", ".join(sorted(unknown))
            + " (expected a registered env override or a positional tool parameter)"
        )
    return {str(key): str(value) for key, value in resolved.items()}


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
        self.budget_ledger = load_or_create_ledger(self.run_dir, task=task, state=state)
        self.results: list[Dict[str, Any]] = []
        self.normalized_observations: list[Dict[str, Any]] = []
        self.observation: Optional[Dict[str, Any]] = None

    def _observation_items(self, result: Mapping[str, Any]) -> list[Dict[str, Any]]:
        """Normalize a tool result and gate every item against its contract."""

        aggregate = result.get("observation") if isinstance(result.get("observation"), Mapping) else None
        items = list(aggregate.get("observations") or []) if aggregate else normalize_tool_result(result)
        validated: list[Dict[str, Any]] = []
        for item in items:
            validate_contract("agent_normalized_observation.schema.json", item, path="$.observation")
            validated.append(dict(item))
        return validated

    def _write_observations(self, items: Iterable[Mapping[str, Any]]) -> None:
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
        """Return a plan-independent idempotency key.

        The key intentionally excludes ``plan_id``.  ``plan_id`` is regenerated
        on every ``build_plan`` call, so binding the key to it meant a replan
        produced a *different* key for the same logical call and re-consumed
        model budget (design §11.2).  The key is now
        ``(tool, version, whitelisted business inputs)`` only; the ledger itself
        is session-scoped (``run_dir`` is the Session directory), so reuse now
        works across every plan revision of one Session.  ``plan_id`` is kept
        purely as provenance on the stored entry.
        """

        spec = get_tool_spec(str(step["tool_name"]))
        inputs = dict(step.get("arguments") or step.get("inputs") or {})
        overrides = self._effective_env_overrides(step)
        values: Dict[str, Any] = {}
        for field in spec.idempotency_key_fields:
            if field in inputs:
                values[field] = inputs[field]
                continue
            # Prefer the resolved environment contract: the same logical call
            # must yield the same key even when a step supplies its input
            # through ``arguments`` rather than through its own task field.
            env_key = _STEP_ARGUMENT_ENV_ALIASES.get(field, field.upper())
            values[field] = overrides.get(env_key, getattr(self.task, field, None))
        # The allowlist *is* the business-input whitelist, so the whole
        # validated environment contract participates in the key.  Relying on
        # ``idempotency_key_fields`` alone let a step change e.g.
        # ``SEARCH_MAX_DEPTH`` without changing its key, which would have made
        # the executor wrongly reuse an earlier run.
        payload = {
            "tool": spec.tool_name,
            "version": spec.version,
            "inputs": redact(values),
            "env": redact(overrides),
        }
        return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    def _effective_env_overrides(self, step: Mapping[str, Any]) -> Dict[str, str]:
        """Merge the plan env contract with whitelisted Step-level overrides.

        Step arguments win over ``plan.env_overrides`` so a single step can
        narrow or widen its own contract (design §9.2).
        """

        merged: Dict[str, Any] = dict(self.plan.get("env_overrides") or {})
        merged.update(_arguments_to_env_overrides(step.get("arguments") or step.get("inputs") or {}))
        return validate_env_overrides(merged)

    def _task_for_step(self, step: Mapping[str, Any]) -> AgentTask:
        """Apply Step arguments to a task copy for tools that take a task.

        ``check_environment`` and ``observe_experiment`` receive the ``AgentTask``
        object rather than a raw env mapping, so their documented arguments are
        projected onto the task instead of being dropped.
        """

        values = _arguments_to_env_overrides(step.get("arguments") or step.get("inputs") or {})
        changes: Dict[str, Any] = {}
        for env_key, field_name in _STEP_ARGUMENT_TASK_FIELDS.items():
            if env_key not in values:
                continue
            raw_value = values[env_key]
            if env_key in _STEP_ARGUMENT_INT_FIELDS:
                try:
                    changes[field_name] = int(raw_value)
                except (TypeError, ValueError) as exc:
                    raise ExecutorError(f"step argument {env_key} must be an integer") from exc
            else:
                changes[field_name] = raw_value
        return replace(self.task, **changes) if changes else self.task

    def _validate_step(self, step: Mapping[str, Any]) -> None:
        tool = str(step.get("tool_name") or step.get("tool") or "")
        if tool not in REGISTERED_TOOLS or tool not in self.task.allowed_tools:
            raise ExecutorError(f"step uses an unregistered or unauthorized tool: {tool}")
        get_tool_spec(tool)
        # Fail fast: a Step argument that cannot be projected onto the tool's
        # contract must never be dropped silently (T-1).
        _arguments_to_env_overrides(step.get("arguments") or step.get("inputs") or {})
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

    def _verify_outputs(self, step: Mapping[str, Any], result: Mapping[str, Any]) -> tuple[bool, str, bool]:
        """Return ``(valid, reason, artifact_gate)``.

        ``artifact_gate`` is True only when the registered tool itself
        reported success but a *formal artifact* check failed.  A tool-reported
        failure keeps its own ``failure_category`` / ``recoverable`` values, so
        a retryable system error is never silently promoted to a fatal one
        (design §3.4: business and system failures stay separated).
        """

        if not result.get("ok"):
            return False, str(result.get("failure_category") or "tool_execution_error"), False
        expected = set(step.get("expected_outputs") or [])
        tool = str(result.get("tool"))
        if tool == "check_environment" and "environment_checked" in expected and not result.get("ready"):
            return False, "environment_not_ready", True
        if tool in {"run_full_loop", "resume_full_loop"}:
            exp_dir = result.get("experiment_dir")
            if "experiment_dir" in expected and (not exp_dir or not Path(str(exp_dir)).is_dir()):
                return False, "artifact_missing:experiment_dir", True
            if "final/final_scored.jsonl" in expected:
                if not exp_dir:
                    return False, "artifact_missing:final/final_scored.jsonl", True
                valid, reason = validate_published_artifact(str(Path(str(exp_dir)) / "final" / "final_scored.jsonl"))
                if not valid:
                    return False, f"artifact_missing:{reason}", True
        if tool == "observe_experiment":
            preconditions = set(step.get("preconditions") or [])
            observed = result.get("observation") if isinstance(result.get("observation"), Mapping) else {}
            if "published_manifest_validation_required" in preconditions and str(observed.get("manifest_status") or "not_checked") == "not_checked":
                # A step that *requires* published-manifest validation cannot be
                # satisfied by "nothing was checked".
                return False, "artifact_missing:published_manifest_not_checked", True
            if "agent_observation.json" in expected and not (self.run_dir / "agent_observation.json").is_file():
                return False, "artifact_missing:agent_observation.json", True
        return True, "ok", False

    def _invoke_registry(self, method_name: str, *args: Any, tool_call_id: str, idempotency_key: str, **kwargs: Any) -> Dict[str, Any]:
        """Invoke a registry capability, passing only the kwargs it declares.

        The previous implementation toggled *all three* runtime kwargs based on
        whether the method happened to name ``record_events``.  A method that
        declared ``tool_call_id`` but not ``record_events`` therefore silently
        lost its idempotency key -- and any signature change degraded into
        duplicate event recording instead of an error (T-8).  Each runtime kwarg
        is now injected independently and only when the callable accepts it.
        """

        method = getattr(self.registry, method_name)
        parameters = inspect.signature(method).parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        for name, value in (
            ("tool_call_id", tool_call_id),
            ("idempotency_key", idempotency_key),
            ("record_events", False),
        ):
            if accepts_kwargs or name in parameters:
                kwargs[name] = value
        return dict(method(*args, **kwargs))

    def _run_step(self, step: Mapping[str, Any], *, idempotency_key: str, tool_call_id: str) -> Dict[str, Any]:
        tool = str(step["tool_name"])
        env_overrides = self._effective_env_overrides(step)
        if tool == "check_environment":
            return self._invoke_registry("check_environment", self._task_for_step(step), tool_call_id=tool_call_id, idempotency_key=idempotency_key)
        if tool == "run_full_loop":
            return self._invoke_registry("run_full_loop", self.task, env_overrides, tool_call_id=tool_call_id, idempotency_key=idempotency_key)
        if tool == "resume_full_loop":
            return self._invoke_registry("resume_full_loop", self.task, env_overrides, tool_call_id=tool_call_id, idempotency_key=idempotency_key)
        if tool == "observe_experiment":
            exp_dir = self.state.get("experiment_dir") or self.task.resume_exp_dir
            if not exp_dir:
                raise ExecutorError("experiment directory could not be located")
            boundary_target = env_overrides.get("SEARCH_BOUNDARY_TARGET") or env_overrides.get("BOUNDARY_TARGET")
            try:
                resolved_boundary_target = int(boundary_target) if boundary_target not in (None, "") else self.task.boundary_target
            except (TypeError, ValueError) as exc:
                raise ExecutorError("SEARCH_BOUNDARY_TARGET must be an integer") from exc
            search_mode = str(env_overrides.get("SEARCH_MODE") or self.plan.get("selected_search_mode") or "")
            observed = self.observe(exp_dir, run_dir=self.run_dir, boundary_target=resolved_boundary_target, task_search_mode=search_mode)
            self.observation = observed
            return {"tool": tool, "tool_version": get_tool_spec(tool).version, "tool_call_id": tool_call_id, "idempotency_key": idempotency_key, "ok": observed.get("status") != "blocked", "return_code": 0 if observed.get("status") != "blocked" else 1, "duration_seconds": 0.0, "retry_count": 0, "failure_category": None if observed.get("status") != "blocked" else "fatal_system_error", "recoverable": False, "observation": observed, "cost": cost_estimate(get_tool_spec(tool))}
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
        if "model_calls" in self.budget_ledger.hard_limits:
            try:
                self.budget_ledger.consume("model_calls", UNALLOCATED_TARGET, 1, evidence_ref={"tool": tool, "tool_call_id": call_id})
            except BudgetLedgerError as exc:
                raise ExecutorError(str(exc)) from exc
            save_ledger(self.run_dir, self.budget_ledger)
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
            result.setdefault("cost", cost_estimate(spec))
            result["duration_seconds"] = round(float(result.get("duration_seconds") or (time.monotonic() - started)), 6)
            valid, reason, artifact_gate = self._verify_outputs(step, result)
            if not valid:
                if artifact_gate:
                    # The tool succeeded but its formal artifact contract was
                    # not met: this is an unrecoverable system failure.
                    result.update({"ok": False, "recoverable": False, "failure_category": "fatal_system_error", "artifact_validation": reason})
                else:
                    # The tool already classified its own failure.  Preserve
                    # ``recoverable``/``failure_category`` so retryable system
                    # failures remain distinguishable from fatal ones.
                    result["artifact_validation"] = reason
                    if not result.get("failure_category"):
                        result["failure_category"] = reason
                    if not isinstance(result.get("recoverable"), bool):
                        result["recoverable"] = False
            validate_contract("agent_tool_result.schema.json", dict(result), path="$.tool_result")
            observation_items = self._observation_items(result)
        except (ExecutorError, ToolExecutionError, OSError, ValueError, ContractViolation) as exc:
            result = {"tool": tool, "tool_version": spec.version, "tool_call_id": call_id, "idempotency_key": key, "ok": False, "return_code": -1, "duration_seconds": round(time.monotonic() - started, 6), "retry_count": 0, "failure_category": "fatal_system_error", "recoverable": False, "stderr_summary": str(exc), "cost": cost_estimate(spec)}
            observation_items = self._observation_items(result)
        entry = redact(result)
        # Provenance only: the key no longer depends on the plan, but the
        # ledger still records which revision produced the entry (T-2).
        entry["produced_by_plan_id"] = self.plan.get("plan_id")
        entry["produced_by_plan_revision"] = self.plan.get("plan_revision")
        self.ledger[key] = entry
        self._save_ledger()
        self.budget_ledger.record_tool_call(tool, tool_call_id=call_id, duration_seconds=float(result.get("duration_seconds") or 0), ok=bool(result.get("ok")))
        save_ledger(self.run_dir, self.budget_ledger)
        self._write_observations(observation_items)
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
            result = {"tool": tool, "tool_version": spec.version, "tool_call_id": call_id, "idempotency_key": key, "ok": ok, "return_code": 0 if ok else 1, "duration_seconds": round(time.monotonic() - started, 6), "retry_count": 0, "failure_category": None if ok else "fatal_system_error", "recoverable": False, "report_path": str(report_path), "cost": cost_estimate(spec)}
        except OSError as exc:
            result = {"tool": tool, "tool_version": spec.version, "tool_call_id": call_id, "idempotency_key": key, "ok": False, "return_code": -1, "duration_seconds": round(time.monotonic() - started, 6), "retry_count": 0, "failure_category": "fatal_system_error", "recoverable": False, "stderr_summary": str(exc), "cost": cost_estimate(spec)}
        append_event(self.events_path, "tool_completed" if result["ok"] else "tool_failed", result)
        try:
            observation_items = self._observation_items(result)
            validate_contract("agent_tool_result.schema.json", dict(result), path="$.tool_result")
        except ContractViolation as exc:
            result.update({"ok": False, "recoverable": False, "failure_category": "fatal_system_error", "artifact_validation": str(exc)})
            observation_items = self._observation_items(result)
        self._write_observations(observation_items)
        self.results.append(result)
        if result["ok"]:
            completed = list(self.state.get("completed_step_ids") or [])
            completed.append(str(step.get("step_id") or "write_agent_report"))
            self.update_state(self.run_dir, self.state, completed_step_ids=completed, current_step_id=None)
        return result
