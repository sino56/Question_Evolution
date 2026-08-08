"""Build compatible Agent context packs with cache-safe v2 layers."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional

from .context_layers import PROJECT_HARD_CONSTRAINTS, build_context_layers, redact_context, registered_tools
from .task import AgentTask


def _truncate(value: Any, limit: int) -> Any:
    text = json.dumps(redact_context(value), ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return json.loads(text)
    return {"truncated": True, "preview": text[:limit]}


def build_context_pack(
    task: AgentTask,
    *,
    plan: Optional[Mapping[str, Any]] = None,
    observation: Optional[Mapping[str, Any]] = None,
    previous_decision: Optional[Mapping[str, Any]] = None,
    memory_context: Optional[Mapping[str, Any]] = None,
    runtime_state: Optional[Mapping[str, Any]] = None,
    snapshot_ids: Optional[Mapping[str, Any]] = None,
    max_chars: int = 60000,
) -> Dict[str, Any]:
    """Return legacy fields plus the v2 layered context contract.

    Legacy keys remain for old reports and tools.  Model calls should consume
    the v2 layers through :mod:`agent_runtime.context_prompt`.
    """

    layers = build_context_layers(
        task,
        plan=plan,
        observation=observation,
        previous_decision=previous_decision,
        memory_context=memory_context,
        runtime_state=runtime_state,
        snapshot_ids=snapshot_ids,
    )
    legacy = {
        "goal": task.goal,
        "task_config": {
            "search_mode": task.search_mode,
            "execution_scope": task.execution_scope,
            "review_mode": task.review_mode,
            "boundary_target": task.boundary_target,
            "max_search_steps": task.max_search_steps,
        },
        "project_hard_constraints": PROJECT_HARD_CONSTRAINTS,
        "selected_plan": _truncate(plan or {}, min(12000, max_chars // 3)),
        "observation_summary": _truncate(observation or {}, min(18000, max_chars // 2)),
        "memory_summary": _truncate((observation or {}).get("memory_summary", {}), 5000),
        "memory_context": layers["memory_context"],
        "available_tools": [item["tool_name"] for item in registered_tools(task.allowed_tools)],
        "previous_decision": _truncate(previous_decision or {}, 4000),
    }
    pack = {**legacy, **layers}
    if len(json.dumps(pack, ensure_ascii=False, sort_keys=True)) <= max_chars:
        return pack
    # Preserve the v2 contract for normal production calls.  Its layers are
    # already independently bounded; reduce only duplicated legacy aliases.
    if max_chars >= 5000:
        pack["selected_plan"] = _truncate(plan or {}, 500)
        pack["observation_summary"] = _truncate(observation or {}, 500)
        pack["memory_summary"] = _truncate((observation or {}).get("memory_summary", {}), 500)
        pack["previous_decision"] = _truncate(previous_decision or {}, 500)
        return pack
    # Very small diagnostic limits retain the legacy compact behavior.
    return _truncate(pack, max_chars)
