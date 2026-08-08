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
DECISIONS = {"run_pipeline", "resume_pipeline", "run_review", "stop_and_report", "blocked"}


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
    steps = plan.get("steps")
    if not isinstance(steps, list) or not steps:
        raise PolicyViolation("plan must contain at least one step")
    for step in steps:
        tool = step.get("tool") if isinstance(step, Mapping) else None
        if tool not in REGISTERED_TOOLS:
            raise PolicyViolation(f"unregistered tool in plan: {tool}")
        if tool not in task.allowed_tools:
            raise PolicyViolation(f"tool not permitted by task: {tool}")
    if task.review_mode == "report_only" and any(step["tool"] in {"check_environment", "run_full_loop", "resume_full_loop"} for step in steps):
        raise PolicyViolation("report_only plans cannot execute pipeline tools")
    if task.execution_scope != "full_iteration" and any(step["tool"] in {"run_full_loop", "resume_full_loop"} for step in steps):
        raise PolicyViolation("current registered loop only supports full_iteration execution")


def validate_decision(decision: Mapping[str, Any]) -> None:
    action = decision.get("action")
    if action not in DECISIONS:
        raise PolicyViolation(f"unsupported Agent decision: {action}")
