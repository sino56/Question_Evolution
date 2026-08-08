"""Hard v1 boundary checks for plans, tools, and decisions."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping

from .task import EXECUTION_SCOPES, REGISTERED_TOOLS, AgentTask


class PolicyViolation(ValueError):
    pass


ENV_ALLOWLIST = {
    "INPUT_FILE",
    "EXP_ROOT",
    "SEARCH_MODE",
    "SEARCH_BOUNDARY_TARGET",
    "BOUNDARY_TARGET",
    "MAX_SEARCH_STEPS",
    "EXECUTION_SCOPE",
    "SEARCH_MAX_DEPTH",
    "SEARCH_BRANCH_WINDOW",
    "SEARCH_MAX_REQUEST_ATTEMPTS_PER_SAMPLE",
    "SEARCH_MAX_EVALUATIONS_PER_SAMPLE",
    "SEARCH_SAMPLE_TIMEOUT_SECONDS",
    "ROUTER_CONCURRENCY",
    "SCORING_CONCURRENCY",
}
DECISIONS = {"run_pipeline", "resume_pipeline", "run_review", "stop_and_report", "replan", "blocked"}
PLAN_KINDS = {"task_plan", "recovery_plan", "review_plan"}
_REQUIRED_STEP_FIELDS = {
    "step_id", "intent", "tool_name", "arguments", "preconditions", "expected_outputs",
    "success_condition", "business_failure_action", "system_failure_action", "budget_limit", "depends_on",
}
_PROTECTED_ARGUMENT_MARKERS = ("prompt", "router", "rubric", "memory", "operator", "schema", "state")
_REQUIRED_TOOL_OUTPUTS = {
    "check_environment": "environment_checked",
    "run_full_loop": "final/final_scored.jsonl",
    "resume_full_loop": "final/final_scored.jsonl",
    "observe_experiment": "agent_observation.json",
    "write_agent_report": "agent_report.md",
}


def validate_task_policy(task: AgentTask) -> None:
    if task.allow_prompt_mutation:
        raise PolicyViolation("prompt mutation is not permitted by Agent v1")
    if task.allow_memory_active_publish:
        raise PolicyViolation("active Memory publication is not permitted by Agent v1")
    if task.execution_scope not in EXECUTION_SCOPES:
        raise PolicyViolation("illegal execution_scope")


def validate_env_overrides(overrides: Mapping[str, Any]) -> Dict[str, str]:
    illegal = sorted(set(overrides) - ENV_ALLOWLIST)
    if illegal:
        raise PolicyViolation("environment overrides are not allowed: " + ", ".join(illegal))
    return {str(key): str(value) for key, value in overrides.items()}


def validate_plan(task: AgentTask, plan: Mapping[str, Any]) -> None:
    validate_task_policy(task)
    overrides = plan.get("env_overrides", {})
    if not isinstance(overrides, Mapping):
        raise PolicyViolation("plan.env_overrides must be an object")
    validate_env_overrides(overrides)
    if plan.get("selected_execution_scope") not in {None, task.execution_scope}:
        raise PolicyViolation("plan execution_scope does not match task execution_scope")
    if plan.get("selected_review_mode") not in {None, task.review_mode}:
        raise PolicyViolation("plan review_mode does not match task review_mode")
    plan_kind = plan.get("plan_kind")
    if plan_kind is not None and plan_kind not in PLAN_KINDS:
        raise PolicyViolation("illegal plan_kind")
    if not isinstance(plan.get("plan_revision", 0), int) or isinstance(plan.get("plan_revision", 0), bool) or plan.get("plan_revision", 0) < 0:
        raise PolicyViolation("plan_revision must be a non-negative integer")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PolicyViolation("plan must contain at least one step")
    step_ids: set[str] = set()
    for step in steps:
        if not isinstance(step, Mapping):
            raise PolicyViolation("plan step must be an object")
        # Hand-written compatibility plans are still accepted by the Stage-1
        # policy tests; all emitted Session plans carry the full contract.
        if any(field in step for field in _REQUIRED_STEP_FIELDS):
            missing = sorted(field for field in _REQUIRED_STEP_FIELDS if field not in step)
            if missing:
                raise PolicyViolation("plan step missing required fields: " + ", ".join(missing))
        tool = step.get("tool_name", step.get("tool"))
        legacy_tool = step.get("tool")
        if legacy_tool is not None and tool != legacy_tool:
            raise PolicyViolation("tool and tool_name must match")
        if tool not in REGISTERED_TOOLS:
            raise PolicyViolation(f"unregistered tool in plan: {tool}")
        if tool not in task.allowed_tools:
            raise PolicyViolation(f"tool not permitted by task: {tool}")
        step_id = step.get("step_id")
        if step_id is not None:
            if not isinstance(step_id, str) or not step_id or step_id in step_ids:
                raise PolicyViolation("plan steps require unique step_id values")
            step_ids.add(step_id)
        if "arguments" in step and not isinstance(step["arguments"], Mapping):
            raise PolicyViolation("plan step arguments must be an object")
        if "preconditions" in step and (not isinstance(step["preconditions"], list) or not all(isinstance(item, str) and item for item in step["preconditions"])):
            raise PolicyViolation("plan step preconditions must be non-empty strings")
        if "expected_outputs" in step and (not isinstance(step["expected_outputs"], list) or not step["expected_outputs"]):
            raise PolicyViolation("plan step expected_outputs are required")
        required_output = _REQUIRED_TOOL_OUTPUTS.get(str(tool))
        if required_output and "expected_outputs" in step and required_output not in set(step.get("expected_outputs", [])):
            raise PolicyViolation(f"plan step is missing required output for {tool}: {required_output}")
        if "depends_on" in step and (not isinstance(step["depends_on"], list) or not all(isinstance(item, str) and item for item in step["depends_on"])):
            raise PolicyViolation("plan step depends_on must be non-empty strings")
        if "budget_limit" in step and not isinstance(step["budget_limit"], Mapping):
            raise PolicyViolation("plan step budget_limit must be an object")
        if "business_failure_action" in step and "retry" in str(step["business_failure_action"]).lower():
            raise PolicyViolation("business failures cannot be treated as system retries")
        arguments = step.get("arguments", step.get("inputs", {}))
        if isinstance(arguments, Mapping):
            protected = [str(key) for key in arguments if any(marker in str(key).lower() for marker in _PROTECTED_ARGUMENT_MARKERS)]
            if protected:
                raise PolicyViolation("plan cannot modify protected formal assets: " + ", ".join(sorted(protected)))
    if task.review_mode == "report_only" and any(step["tool"] in {"check_environment", "run_full_loop", "resume_full_loop"} for step in steps):
        raise PolicyViolation("report_only plans cannot execute pipeline tools")
    if task.execution_scope != "full_iteration" and any(step["tool"] in {"run_full_loop", "resume_full_loop"} for step in steps):
        raise PolicyViolation("current registered loop only supports full_iteration execution")
    _validate_execution_skeleton(task, steps)


def _validate_execution_skeleton(task: AgentTask, steps: list[Mapping[str, Any]]) -> None:
    """Reject plans that skip preflight, published-artifact checks, or scoring."""

    tools = [step.get("tool_name", step.get("tool")) for step in steps]
    step_by_id = {str(step.get("step_id")): step for step in steps}
    for step in steps:
        missing_dependencies = sorted(set(step.get("depends_on", [])) - set(step_by_id))
        if missing_dependencies:
            raise PolicyViolation("plan step depends on missing step: " + ", ".join(missing_dependencies))
    if task.review_mode == "report_only":
        return
    if "run_full_loop" in tools:
        try:
            check_index = tools.index("check_environment")
            run_index = tools.index("run_full_loop")
        except ValueError as exc:
            raise PolicyViolation("new-experiment plan cannot bypass environment check") from exc
        if check_index > run_index:
            raise PolicyViolation("environment check must precede run_full_loop")
        run_step = steps[run_index]
        requirements = set(run_step.get("preconditions", []))
        outputs = set(run_step.get("expected_outputs", []))
        if "environment_checked" not in requirements or "published_manifest_validation_required" not in requirements:
            raise PolicyViolation("run_full_loop must require environment and manifest validation")
        if "real_scoring_required" not in requirements or "final/final_scored.jsonl" not in outputs:
            raise PolicyViolation("run_full_loop cannot bypass real scoring")
        observe_step = next((step for step in steps if step.get("tool_name", step.get("tool")) == "observe_experiment"), None)
        if not observe_step or "published_manifest_validation_required" not in set(observe_step.get("preconditions", [])):
            raise PolicyViolation("new-experiment plan cannot bypass manifest validation during observation")
    if "resume_full_loop" in tools:
        resume_step = steps[tools.index("resume_full_loop")]
        requirements = set(resume_step.get("preconditions", []))
        if not {"existing_experiment_dir", "resume_checkpoint_valid", "published_manifest_validation_required"}.issubset(requirements):
            raise PolicyViolation("resume plan must validate its checkpoint and manifest")


def validate_decision(decision: Mapping[str, Any]) -> None:
    action = decision.get("action")
    if action not in DECISIONS:
        raise PolicyViolation(f"unsupported Agent decision: {action}")
