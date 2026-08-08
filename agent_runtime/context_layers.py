"""Build the versioned, cache-safe layers of an Agent context pack."""

from __future__ import annotations

import json
from typing import Any, Mapping, Optional

from .context_cache import CONTEXT_SCHEMA_VERSION, PROMPT_TEMPLATE_VERSION, cache_metadata, memory_context_key
from .events import redact
from .task import AgentTask, REGISTERED_TOOLS


POLICY_SNAPSHOT_ID = "agent-policy-v1"
PROMPT_SNAPSHOT_ID = "frozen"
OPERATOR_SNAPSHOT_ID = "frozen"
TOOL_REGISTRY_VERSION = "agent-tool-registry-v1"
SKILL_REGISTRY_VERSION = "agent-skill-registry-v1"
TOOL_REGISTRY_ORDER = (
    "check_environment",
    "run_full_loop",
    "resume_full_loop",
    "observe_experiment",
    "write_agent_report",
)
TOOL_DESCRIPTIONS = {
    "check_environment": "Validate runtime prerequisites before a real experiment.",
    "run_full_loop": "Run the frozen Question Evolution loop through registered orchestration only.",
    "resume_full_loop": "Resume a published experiment from a confirmed checkpoint.",
    "observe_experiment": "Read and summarize published experiment artifacts.",
    "write_agent_report": "Write an auditable Agent report from summaries and evidence references.",
}
PROJECT_HARD_CONSTRAINTS = [
    "Agent controls registered tools only; domain routing, generation, validation, and scoring remain in the pipeline.",
    "Do not mutate prompts, Router, Rubric, operators, schemas, scores, state, or active Memory.",
    "Do not treat automatic scores as confirmed capability boundaries.",
    "Do not inject complete JSONL, logs, answers, rubrics, or secrets into the control layer.",
]


def registered_tools(allowed_tools: list[str] | tuple[str, ...]) -> list[dict[str, str]]:
    """Return tools in registry order rather than caller or filesystem order."""

    allowed = set(allowed_tools) & set(REGISTERED_TOOLS)
    return [
        {"tool_name": name, "description": TOOL_DESCRIPTIONS[name]}
        for name in TOOL_REGISTRY_ORDER
        if name in allowed
    ]


def normalize_memory_context(
    memory_context: Optional[Mapping[str, Any]],
    *,
    query: str,
) -> dict[str, Any]:
    """Normalize Top-K cards and make the retrieval identity explicit.

    Cards keep their IDs, versions, and evidence references intact.  Their
    ordering is deterministic by retrieval score, card ID, and version.
    """

    raw = dict(memory_context or {})
    snapshot_id = _text(raw.get("memory_snapshot_id"))
    retrieval_version = _text(raw.get("retrieval_config_version")) or "global-memory-retrieval-v1"
    try:
        top_k = max(0, int(raw.get("top_k", 0)))
    except (TypeError, ValueError):
        top_k = 0
    cards = [dict(item) for item in raw.get("cards") or [] if isinstance(item, Mapping)]
    cards.sort(key=lambda item: (-_number(item.get("retrieval_score")), _text(item.get("card_id")), _version_key(item.get("version"))))
    result = {
        "memory_snapshot_id": snapshot_id or None,
        "retrieval_config_version": retrieval_version,
        "top_k": top_k,
        "cards": redact_context(cards),
        "mode": _text(raw.get("mode")) or "no_global_memory",
    }
    if snapshot_id and top_k:
        result["memory_context_key"] = memory_context_key(
            memory_snapshot_id=snapshot_id,
            normalized_query=query,
            retrieval_config_version=retrieval_version,
            top_k=top_k,
        )
    else:
        result["memory_context_key"] = raw.get("memory_context_key") if isinstance(raw.get("memory_context_key"), str) else None
    return result


def build_context_layers(
    task: AgentTask,
    *,
    plan: Optional[Mapping[str, Any]] = None,
    observation: Optional[Mapping[str, Any]] = None,
    previous_decision: Optional[Mapping[str, Any]] = None,
    memory_context: Optional[Mapping[str, Any]] = None,
    runtime_state: Optional[Mapping[str, Any]] = None,
    snapshot_ids: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Classify every current context field into its cache behavior layer."""

    plan_value = dict(plan or {})
    observation_value = dict(observation or {})
    runtime = dict(runtime_state or {})
    snapshots = dict(snapshot_ids or {})
    memory = normalize_memory_context(memory_context, query=task.goal)
    selected_search_mode = _text(plan_value.get("selected_search_mode")) or task.search_mode
    selected_execution_scope = _text(plan_value.get("selected_execution_scope")) or task.execution_scope
    selected_plan_type = _text(plan_value.get("plan_kind")) or "task_plan"
    snapshot_prefix = {
        "context_schema_version": CONTEXT_SCHEMA_VERSION,
        "prompt_template_version": PROMPT_TEMPLATE_VERSION,
        "policy_snapshot_id": _text(snapshots.get("policy_snapshot_id")) or POLICY_SNAPSHOT_ID,
        "prompt_snapshot_id": _text(snapshots.get("prompt_snapshot_id")) or PROMPT_SNAPSHOT_ID,
        "operator_snapshot_id": _text(snapshots.get("operator_snapshot_id")) or OPERATOR_SNAPSHOT_ID,
        "memory_snapshot_id": _text(snapshots.get("memory_snapshot_id")) or _text(memory.get("memory_snapshot_id")) or _text(task.memory_snapshot_id),
        "tool_registry_version": _text(snapshots.get("tool_registry_version")) or TOOL_REGISTRY_VERSION,
        "skill_registry_version": _text(snapshots.get("skill_registry_version")) or SKILL_REGISTRY_VERSION,
    }
    stable_prefix = {
        "agent_role": "Controlled Question Evolution Agent; only registered tools may be called.",
        "hard_constraints": PROJECT_HARD_CONSTRAINTS,
        "output_schemas": {
            "plan": "agent_plan.schema.json",
            "observation": "agent_observation.schema.json",
            "decision": "agent_decision.schema.json",
            "advice": "advisor_advice.schema.json",
        },
        "tool_registry": registered_tools(task.allowed_tools),
        "skill_protocol": {
            "registry_version": snapshot_prefix["skill_registry_version"],
            "rule": "Skills are read-only procedures and may use only their declared context layers.",
        },
        "report_requirement": "Automatic scores are candidate evidence only, never confirmed capability boundaries.",
    }
    task_context = {
        "goal": task.goal,
        "selected_search_mode": selected_search_mode,
        "selected_execution_scope": selected_execution_scope,
        "plan_type": selected_plan_type,
        "budget": {
            "boundary_target": task.boundary_target,
            "max_search_steps": task.max_search_steps,
        },
        "allowed_tools": [item["tool_name"] for item in stable_prefix["tool_registry"]],
        "task_config": {
            "review_mode": task.review_mode,
            "planning_mode": task.planning_mode,
            "allow_global_memory_read": task.allow_global_memory_read,
        },
    }
    dynamic_tail = {
        "agent_run_id": runtime.get("agent_run_id"),
        "agent_run_dir": runtime.get("agent_run_dir"),
        "experiment_dir": runtime.get("experiment_dir") or observation_value.get("experiment_dir"),
        "current_step_id": runtime.get("current_step_id"),
        "current_plan_path": runtime.get("current_plan_path"),
        "resume_checkpoint": runtime.get("resume_checkpoint"),
        "observation_summary": _bounded(observation_value, 18000),
        "last_decision": _bounded(dict(previous_decision or {}), 4000),
        "generated_at": runtime.get("generated_at"),
        "stdout_summary": runtime.get("stdout_summary"),
        "stderr_summary": runtime.get("stderr_summary"),
        "parse_errors": runtime.get("parse_errors"),
        "paths": {
            "input_file": task.input_file,
            "exp_root": task.exp_root,
            "resume_exp_dir": task.resume_exp_dir,
        },
        "plan_revision": plan_value.get("plan_revision"),
        "selected_plan": _bounded(plan_value, 12000),
    }
    context_cache = cache_metadata(
        stable_prefix=stable_prefix,
        snapshot_prefix=snapshot_prefix,
        task_context=task_context,
        memory_context=memory,
        dynamic_tail=dynamic_tail,
    )
    return {
        "context_schema_version": CONTEXT_SCHEMA_VERSION,
        "context_cache": context_cache,
        "stable_prefix": stable_prefix,
        "snapshot_prefix": snapshot_prefix,
        "task_context": task_context,
        "memory_context": memory,
        "dynamic_tail": dynamic_tail,
    }


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def redact_context(value: Any, *, key: str = "") -> Any:
    """Redact secrets while retaining non-secret SHA-256 cache identities."""

    if key in {"context_cache_key", "memory_context_key"} and isinstance(value, str) and value.startswith("sha256:"):
        return value
    if isinstance(value, Mapping):
        return {str(name): redact_context(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [redact_context(item) for item in value]
    if isinstance(value, tuple):
        return [redact_context(item) for item in value]
    return redact(value, key=key)


def _bounded(value: Any, limit: int) -> Any:
    safe = redact_context(value)
    text = json.dumps(safe, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(text) <= limit:
        return safe
    return {"truncated": True, "preview": text[:limit]}


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _version_key(value: Any) -> tuple[int, str]:
    try:
        return int(value), ""
    except (TypeError, ValueError):
        return 0, _text(value)
