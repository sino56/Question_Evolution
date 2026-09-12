"""Deterministic, auditable planning for the controlled Agent."""

from __future__ import annotations

import uuid
import json
import os
import urllib.error
import urllib.request
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from schema_validation import SchemaValidationError, load_schema, validate_instance

from .contracts import ContractViolation, validate_contract
from .policy import PolicyViolation, validate_plan
from .task import SUPPORTED_EXECUTION_SCOPES, AgentTask
from .context_prompt import assemble_context_prompt


def select_search_mode(task: AgentTask) -> tuple[str, List[str]]:
    if task.search_mode != "auto":
        return task.search_mode, []
    goal = task.goal.lower()
    if any(marker in goal for marker in ("组合", "叠加", "二次进化", "两算子", "vertical", "stack")):
        return "multi_operator_vertical_stack", ["search_mode=auto matched an operator-composition goal"]
    if any(marker in goal for marker in ("逐轮", "主链", "single branch", "single_branch")):
        return "single_branch", ["search_mode=auto matched a sequential main-chain goal"]
    return "multi_operator_branch", ["search_mode=auto defaulted to branch search because the goal did not select another mode"]


def plan_env_overrides(
    task: AgentTask,
    *,
    command: str,
    memory_snapshot_id: Optional[str] = None,
) -> Dict[str, str]:
    """Return the single authoritative ``plan.env_overrides`` mapping.

    Every plan (initial, replanned, or budget-replanned) derives its
    environment contract from this one function.  ``MEMORY_SNAPSHOT_ID`` is
    part of the router cache identity, so it must never be dropped when a
    plan is rebuilt during a replan decision.
    """

    selected_mode, _ = select_search_mode(task)
    overrides: Dict[str, str] = {
        "SEARCH_MODE": selected_mode,
        "SEARCH_BOUNDARY_TARGET": str(task.boundary_target),
        "MAX_SEARCH_STEPS": str(task.max_search_steps),
        "EXECUTION_SCOPE": task.execution_scope,
    }
    if task.input_file:
        overrides["INPUT_FILE"] = task.input_file
    if task.exp_root:
        overrides["EXP_ROOT"] = task.exp_root
    if memory_snapshot_id:
        overrides["MEMORY_SNAPSHOT_ID"] = str(memory_snapshot_id)
    return overrides


def _step(
    step_id: str,
    tool: str,
    purpose: str,
    inputs: Mapping[str, Any],
    expected_outputs: List[str],
    *,
    preconditions: Optional[List[str]] = None,
    depends_on: Optional[List[str]] = None,
    success_condition: str = "tool_completed",
    business_failure_action: str = "observe_and_report",
    system_failure_action: str = "suspend_or_block",
    budget_limit: Optional[Mapping[str, Any]] = None,
    stop_if_failed: bool = True,
    run_when: str = "always",
) -> Dict[str, Any]:
    """Build a Stage-2 PlanStep while retaining Stage-1 field aliases."""

    return {
        "step_id": step_id,
        "intent": purpose,
        "tool_name": tool,
        "tool": tool,
        "purpose": purpose,
        "arguments": dict(inputs),
        "inputs": dict(inputs),
        "preconditions": list(preconditions or []),
        "expected_outputs": expected_outputs,
        "success_condition": success_condition,
        "business_failure_action": business_failure_action,
        "system_failure_action": system_failure_action,
        "budget_limit": {"max_tool_calls": 1, **dict(budget_limit or {})},
        "depends_on": list(depends_on or []),
        "stop_if_failed": stop_if_failed,
        "run_when": run_when,
    }


def _append_if_allowed(steps: List[Dict[str, Any]], task: AgentTask, blocked: List[str], step: Dict[str, Any]) -> None:
    if step["tool"] not in task.allowed_tools:
        blocked.append(f"required tool is not allowed by task: {step['tool']}")
        return
    steps.append(step)


def _deterministic_plan(task: AgentTask, *, command: str, continuation_experiment_dir: str = "") -> Dict[str, Any]:
    selected_mode, assumptions = select_search_mode(task)
    steps: List[Dict[str, Any]] = []
    blocked: List[str] = []
    env_overrides = plan_env_overrides(task, command=command)

    if task.is_review_only or command == "review":
        plan_kind = "review_plan"
        plan_layers = ["review_plan"]
        assumptions.append("report_only uses existing artifacts and never starts a pipeline subprocess")
        _append_if_allowed(steps, task, blocked, _step(
            "observe_experiment", "observe_experiment", "read published experiment artifacts and M1 summaries",
            {"experiment_dir": task.resume_exp_dir}, ["agent_observation.json", "published_manifest_validation"],
            preconditions=["existing_experiment_dir"],
            success_condition="published_artifacts_observed_or_blocked_with_evidence",
            business_failure_action="report_missing_or_invalid_artifacts",
        ))
    elif command == "resume" or task.is_resume or (
        continuation_experiment_dir and "resume_full_loop" in task.allowed_tools
    ):
        # A recovery plan also serves a control-loop continuation: when this
        # Session already discovered an experiment directory and pending work
        # remains, resuming *that* directory is the only way the continuation
        # advances it (a fresh run_full_loop would start a different experiment).
        plan_kind = "recovery_plan"
        plan_layers = ["recovery_plan"]
        resume_dir = continuation_experiment_dir or task.resume_exp_dir
        resume_start = task.resume_start_round if task.resume_start_round else 1
        if continuation_experiment_dir and not task.is_resume:
            assumptions.append("continuation resumes this Session's own experiment directory")
        _append_if_allowed(steps, task, blocked, _step(
            "resume_full_loop", "resume_full_loop", "resume the existing experiment using the registered loop entry point",
            {"experiment_dir": resume_dir, "start_round": resume_start}, ["updated experiment artifacts", "final/final_scored.jsonl"],
            preconditions=["existing_experiment_dir", "resume_checkpoint_valid", "published_manifest_validation_required"],
            success_condition="resumed_loop_completed_with_published_scored_artifacts",
            business_failure_action="observe_and_report",
            budget_limit={"max_search_steps": task.max_search_steps},
        ))
        _append_if_allowed(steps, task, blocked, _step(
            "observe_experiment", "observe_experiment", "summarize the resumed experiment", {}, ["agent_observation.json"],
            preconditions=["published_manifest_validation_required"],
            depends_on=["resume_full_loop"],
            success_condition="published_artifacts_observed_or_blocked_with_evidence",
            business_failure_action="report_missing_or_invalid_artifacts",
        ))
    else:
        plan_kind = "task_plan"
        plan_layers = ["task_plan", "round_plan"]
        _append_if_allowed(steps, task, blocked, _step(
            "check_environment", "check_environment", "validate runtime prerequisites before a real experiment",
            {"input_file": task.input_file}, ["runtime preflight JSON", "environment_checked"],
            success_condition="runtime_preflight_ready",
            business_failure_action="stop_and_report",
        ))
        _append_if_allowed(steps, task, blocked, _step(
            "run_full_loop", "run_full_loop", "run the existing full Question Evolution loop without changing its control flow",
            {"input_file": task.input_file}, ["experiment_dir", "final/final_scored.jsonl", "published_manifest_validation"],
            preconditions=["environment_checked", "published_manifest_validation_required", "real_scoring_required"],
            depends_on=["check_environment"],
            success_condition="run_loop_completed_with_published_scored_artifacts",
            business_failure_action="observe_and_report",
            budget_limit={"max_search_steps": task.max_search_steps, "boundary_target": task.boundary_target},
        ))
        _append_if_allowed(steps, task, blocked, _step(
            "observe_experiment", "observe_experiment", "summarize the completed experiment and M1 memory", {}, ["agent_observation.json"],
            preconditions=["published_manifest_validation_required"],
            depends_on=["run_full_loop"],
            success_condition="published_artifacts_observed_or_blocked_with_evidence",
            business_failure_action="report_missing_or_invalid_artifacts",
        ))

    _append_if_allowed(steps, task, blocked, _step(
        "write_agent_report", "write_agent_report", "write an auditable M0 run report",
        {}, ["agent_report.md"],
        depends_on=["observe_experiment"],
        success_condition="audit_report_written",
        business_failure_action="report_failure",
        stop_if_failed=False,
    ))
    if task.execution_scope not in SUPPORTED_EXECUTION_SCOPES and not task.is_review_only:
        blocked.append(
            f"execution_scope {task.execution_scope} is declared but has no registered entry point; "
            "only full_iteration is executable"
        )

    return {
        "plan_id": f"plan_{uuid.uuid4().hex[:16]}",
        # ``plan_revision`` is owned by ``state.write_plan_revision``: emitting a
        # constant here produced a field that never represented the real
        # revision and was overwritten every time (report O-8).
        "plan_kind": plan_kind,
        "plan_layers": plan_layers,
        "goal_summary": task.goal[:1000],
        "selected_search_mode": selected_mode,
        "selected_execution_scope": task.execution_scope,
        "selected_review_mode": task.review_mode,
        "budget": {"boundary_target": task.boundary_target, "max_search_steps": task.max_search_steps, "hard_limits": dict(task.budget_limits)},
        "env_overrides": env_overrides,
        "steps": steps,
        "assumptions": assumptions,
        "blocked_reasons": blocked,
        "planner_source": "deterministic",
    }


def _model_response(context_pack: Mapping[str, Any]) -> Mapping[str, Any]:
    model = os.getenv("AGENT_MODEL", "").strip()
    base_url = os.getenv("AGENT_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("AGENT_API_KEY", "").strip()
    if not (model and base_url and api_key):
        raise RuntimeError("AGENT_MODEL, AGENT_BASE_URL, and AGENT_API_KEY are required for model_assisted planning")
    request = urllib.request.Request(
        base_url + "/chat/completions",
        data=json.dumps({
            "model": model,
            "temperature": float(os.getenv("AGENT_TEMPERATURE", "0")),
            "messages": [
                {"role": "system", "content": assemble_context_prompt(context_pack)},
                {"role": "user", "content": "Return only one JSON AgentPlan. You may not propose file edits, prompt changes, or unregistered tools."},
            ],
        }, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    timeout = float(os.getenv("AGENT_TIMEOUT", "120"))
    with urllib.request.urlopen(request, timeout=timeout) as response:  # nosec B310: explicit user-configured local provider
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("control model content is not text")
    content = content.strip().removeprefix("```json").removesuffix("```").strip()
    candidate = json.loads(content)
    if not isinstance(candidate, Mapping):
        raise ValueError("control model plan must be a JSON object")
    return candidate


_MODEL_EDITABLE_PLAN_FIELDS = ("goal_summary", "assumptions")
_MODEL_EDITABLE_STEP_FIELDS = ("intent", "purpose")
# ``plan_id`` / ``step_id`` are deliberately excluded: they are generated fresh
# by the deterministic planner and are never shown to the control model, so a
# model cannot be expected to echo them.  ``_merge_model_plan`` always keeps the
# baseline identity, so those fields cannot be hijacked either.
_PROTECTED_PLAN_FIELDS = (
    "plan_kind", "plan_layers", "selected_search_mode", "selected_execution_scope",
    "selected_review_mode", "budget", "env_overrides", "blocked_reasons",
)
_PROTECTED_STEP_FIELDS = (
    "tool", "tool_name", "arguments", "inputs", "preconditions", "expected_outputs",
    "success_condition", "business_failure_action", "system_failure_action", "budget_limit",
    "depends_on", "stop_if_failed", "run_when",
)


def _validate_model_plan(task: AgentTask, candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> None:
    """Reject any model plan that is not a prose-only edit of the baseline.

    The model may refine ``goal_summary``, ``assumptions``, and per-step
    ``intent``/``purpose``.  Every execution-bearing field must equal the
    deterministic baseline exactly; comparing only the tool sequence let a
    model rewrite budgets, ``stop_if_failed``, and step arguments.
    """

    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "agent_plan.schema.json"
    validate_instance(dict(candidate), load_schema(schema_path), schema_dir=schema_path.parent)
    validate_plan(task, candidate)
    candidate_steps = candidate.get("steps")
    baseline_steps = baseline["steps"]
    if not isinstance(candidate_steps, list) or len(candidate_steps) != len(baseline_steps):
        raise PolicyViolation("model plan changed the registered execution skeleton")
    for field in _PROTECTED_PLAN_FIELDS:
        if candidate.get(field) != baseline.get(field):
            raise PolicyViolation(f"model plan changed protected field: {field}")
    for candidate_step, baseline_step in zip(candidate_steps, baseline_steps):
        if not isinstance(candidate_step, Mapping):
            raise PolicyViolation("model plan step must be an object")
        for field in _PROTECTED_STEP_FIELDS:
            if candidate_step.get(field) != baseline_step.get(field):
                raise PolicyViolation(f"model plan changed protected step field: {field}")


def _merge_model_plan(candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> Dict[str, Any]:
    """Build the effective plan from the baseline plus the model's prose edits."""

    result = deepcopy(dict(baseline))
    goal = candidate.get("goal_summary")
    if isinstance(goal, str) and goal.strip():
        result["goal_summary"] = goal
    assumptions = candidate.get("assumptions")
    if isinstance(assumptions, list) and all(isinstance(item, str) for item in assumptions):
        result["assumptions"] = list(assumptions)
    for merged_step, candidate_step in zip(result["steps"], candidate.get("steps") or []):
        if not isinstance(candidate_step, Mapping):
            continue
        for field in _MODEL_EDITABLE_STEP_FIELDS:
            value = candidate_step.get(field)
            if isinstance(value, str) and value.strip():
                merged_step[field] = value
    return result


def build_plan(
    task: AgentTask,
    *,
    command: str,
    context_pack: Optional[Mapping[str, Any]] = None,
    model_client: Optional[Any] = None,
    continuation_experiment_dir: str = "",
) -> Dict[str, Any]:
    """Build a deterministic plan, optionally accepting a schema-checked model plan.

    A model may improve explanations and assumptions, but cannot alter the
    registered v1 tool sequence, execution scope, or environment contract.
    ``continuation_experiment_dir`` retargets a control-loop continuation at
    the Session's own experiment directory (recovery plan instead of a fresh
    ``run_full_loop``).
    """

    baseline = _deterministic_plan(task, command=command, continuation_experiment_dir=continuation_experiment_dir)
    validate_contract("agent_plan.schema.json", baseline, path="$.plan")
    if task.planning_mode != "model_assisted":
        return baseline
    try:
        candidate = (model_client or _model_response)(context_pack or {})
        _validate_model_plan(task, candidate, baseline)
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError, json.JSONDecodeError, SchemaValidationError, PolicyViolation) as exc:
        baseline["assumptions"].append("model_assisted planning fell back to deterministic planning because model output was unavailable or invalid")
        baseline["model_fallback_reason"] = type(exc).__name__
        return baseline
    result = _merge_model_plan(candidate, baseline)
    result["planner_source"] = "model_assisted"
    try:
        validate_contract("agent_plan.schema.json", result, path="$.plan")
    except ContractViolation as exc:
        baseline["assumptions"].append("model_assisted planning fell back to deterministic planning because model output was unavailable or invalid")
        baseline["model_fallback_reason"] = type(exc).__name__
        return baseline
    return result
