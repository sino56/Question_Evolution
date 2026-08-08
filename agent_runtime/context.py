"""Build short, audit-friendly context packs without copying sensitive artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .events import redact
from .task import AgentTask, REGISTERED_TOOLS


PROJECT_HARD_CONSTRAINTS = [
    "Agent controls registered tools only; domain routing, generation, validation, and scoring remain in the pipeline.",
    "Do not mutate prompts, Router, Rubric, operators, schemas, scores, state, or active Memory.",
    "Do not treat automatic scores as confirmed capability boundaries.",
    "Do not inject complete JSONL, logs, answers, rubrics, or secrets into the control layer.",
]


def _truncate(value: Any, limit: int) -> Any:
    text = json.dumps(redact(value), ensure_ascii=False, sort_keys=True)
    if len(text) <= limit:
        return json.loads(text)
    return {"truncated": True, "preview": text[:limit]}


def build_context_pack(
    task: AgentTask,
    *,
    plan: Optional[Mapping[str, Any]] = None,
    observation: Optional[Mapping[str, Any]] = None,
    previous_decision: Optional[Mapping[str, Any]] = None,
    max_chars: int = 60000,
) -> Dict[str, Any]:
    pack = {
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
        "available_tools": sorted(set(task.allowed_tools) & REGISTERED_TOOLS),
        "previous_decision": _truncate(previous_decision or {}, 4000),
    }
    return _truncate(pack, max_chars)
