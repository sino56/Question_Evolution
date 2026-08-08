"""Deterministic, auditable planning for the controlled Agent."""

from __future__ import annotations

import uuid
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from schema_validation import SchemaValidationError, load_schema, validate_instance

from .policy import PolicyViolation, validate_plan
from .task import AgentTask


def select_search_mode(task: AgentTask) -> tuple[str, List[str]]:
    if task.search_mode != "auto":
        return task.search_mode, []
    goal = task.goal.lower()
    if any(marker in goal for marker in ("组合", "叠加", "二次进化", "两算子", "vertical", "stack")):
        return "multi_operator_vertical_stack", ["search_mode=auto matched an operator-composition goal"]
    if any(marker in goal for marker in ("逐轮", "主链", "single branch", "single_branch")):
        return "single_branch", ["search_mode=auto matched a sequential main-chain goal"]
    return "multi_operator_branch", ["search_mode=auto defaulted to branch search because the goal did not select another mode"]


def _step(step_id: str, tool: str, purpose: str, inputs: Mapping[str, Any], expected_outputs: List[str], *, stop_if_failed: bool = True, run_when: str = "always") -> Dict[str, Any]:
    return {
        "step_id": step_id,
        "tool": tool,
        "purpose": purpose,
        "inputs": dict(inputs),
        "expected_outputs": expected_outputs,
        "stop_if_failed": stop_if_failed,
        "run_when": run_when,
    }


def _append_if_allowed(steps: List[Dict[str, Any]], task: AgentTask, blocked: List[str], step: Dict[str, Any]) -> None:
    if step["tool"] not in task.allowed_tools:
        blocked.append(f"required tool is not allowed by task: {step['tool']}")
        return
    steps.append(step)


def _deterministic_plan(task: AgentTask, *, command: str) -> Dict[str, Any]:
    selected_mode, assumptions = select_search_mode(task)
    steps: List[Dict[str, Any]] = []
    blocked: List[str] = []
    env_overrides = {
        "SEARCH_MODE": selected_mode,
        "SEARCH_BOUNDARY_TARGET": str(task.boundary_target),
        "MAX_SEARCH_STEPS": str(task.max_search_steps),
        "EXECUTION_SCOPE": task.execution_scope,
    }
    if task.input_file:
        env_overrides["INPUT_FILE"] = task.input_file
    if task.exp_root:
        env_overrides["EXP_ROOT"] = task.exp_root

    if task.is_review_only or command == "review":
        assumptions.append("report_only uses existing artifacts and never starts a pipeline subprocess")
        _append_if_allowed(steps, task, blocked, _step(
            "observe_experiment", "observe_experiment", "read published experiment artifacts and M1 summaries",
            {"experiment_dir": task.resume_exp_dir}, ["agent_observation.json"],
        ))
    elif command == "resume" or task.is_resume:
        _append_if_allowed(steps, task, blocked, _step(
            "resume_full_loop", "resume_full_loop", "resume the existing experiment using the registered loop entry point",
            {"experiment_dir": task.resume_exp_dir, "start_round": task.resume_start_round}, ["updated experiment artifacts"],
        ))
        _append_if_allowed(steps, task, blocked, _step(
            "observe_experiment", "observe_experiment", "summarize the resumed experiment", {}, ["agent_observation.json"],
        ))
    else:
        _append_if_allowed(steps, task, blocked, _step(
            "check_environment", "check_environment", "validate runtime prerequisites before a real experiment",
            {"input_file": task.input_file}, ["runtime preflight JSON"],
        ))
        _append_if_allowed(steps, task, blocked, _step(
            "run_full_loop", "run_full_loop", "run the existing full Question Evolution loop without changing its control flow",
            {"input_file": task.input_file}, ["experiment directory", "final/final_scored.jsonl"],
        ))
        _append_if_allowed(steps, task, blocked, _step(
            "observe_experiment", "observe_experiment", "summarize the completed experiment and M1 memory", {}, ["agent_observation.json"],
        ))

    _append_if_allowed(steps, task, blocked, _step(
        "write_agent_report", "write_agent_report", "write an auditable M0 run report",
        {}, ["agent_report.md"], stop_if_failed=False,
    ))
    if task.execution_scope != "full_iteration" and not task.is_review_only:
        blocked.append("the current registered loop only supports full_iteration; no partial execution entry point is registered")

    return {
        "plan_id": f"plan_{uuid.uuid4().hex[:16]}",
        "goal_summary": task.goal[:1000],
        "selected_search_mode": selected_mode,
        "selected_execution_scope": task.execution_scope,
        "selected_review_mode": task.review_mode,
        "budget": {"boundary_target": task.boundary_target, "max_search_steps": task.max_search_steps},
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
                {"role": "system", "content": "Return only one JSON AgentPlan. You may not propose file edits, prompt changes, or unregistered tools."},
                {"role": "user", "content": json.dumps(context_pack, ensure_ascii=False)},
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


def _validate_model_plan(task: AgentTask, candidate: Mapping[str, Any], baseline: Mapping[str, Any]) -> None:
    schema_path = Path(__file__).resolve().parents[1] / "schemas" / "agent_plan.schema.json"
    validate_instance(dict(candidate), load_schema(schema_path), schema_dir=schema_path.parent)
    validate_plan(task, candidate)
    protected = ("selected_search_mode", "selected_execution_scope", "selected_review_mode", "budget", "env_overrides")
    for field in protected:
        if candidate.get(field) != baseline.get(field):
            raise PolicyViolation(f"model plan changed protected field: {field}")
    expected_tools = [step["tool"] for step in baseline["steps"]]
    actual_tools = [step.get("tool") for step in candidate.get("steps", []) if isinstance(step, Mapping)]
    if actual_tools != expected_tools:
        raise PolicyViolation("model plan changed the registered execution skeleton")


def build_plan(
    task: AgentTask,
    *,
    command: str,
    context_pack: Optional[Mapping[str, Any]] = None,
    model_client: Optional[Any] = None,
) -> Dict[str, Any]:
    """Build a deterministic plan, optionally accepting a schema-checked model plan.

    A model may improve explanations and assumptions, but cannot alter the
    registered v1 tool sequence, execution scope, or environment contract.
    """

    baseline = _deterministic_plan(task, command=command)
    if task.planning_mode != "model_assisted":
        return baseline
    try:
        candidate = (model_client or _model_response)(context_pack or {})
        _validate_model_plan(task, candidate, baseline)
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError, json.JSONDecodeError, SchemaValidationError, PolicyViolation) as exc:
        baseline["assumptions"].append("model_assisted planning fell back to deterministic planning because model output was unavailable or invalid")
        baseline["model_fallback_reason"] = type(exc).__name__
        return baseline
    result = dict(candidate)
    result["planner_source"] = "model_assisted"
    return result
