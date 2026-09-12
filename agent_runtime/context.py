"""Build compatible Agent context packs with cache-safe v2 layers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from .context_layers import (
    CONTEXT_TOKEN_BUDGET,
    PROJECT_HARD_CONSTRAINTS,
    _bounded,
    build_context_layers,
    estimate_tokens,
    registered_tools,
)
from .task import AgentTask


def _truncate(value: Any, limit: int) -> Any:
    """Bound a legacy context alias with the same structured truncation.

    The previous implementation cut the serialized JSON mid-token, producing a
    ``preview`` string that was not valid JSON; it now reuses the layered
    ``_bounded`` contract so every emitted field remains parseable and carries
    an ``__overflow__`` pointer.
    """

    return _bounded(value, limit)


def _legacy_aliases(layers: Mapping[str, Any]) -> Dict[str, Any]:
    """Derive every legacy alias from the v2 layers instead of duplicating them.

    ``selected_plan`` / ``observation_summary`` / ``memory_summary`` /
    ``previous_decision`` used to be built from the *inputs* while the v2 layers
    were built separately, so the two copies could disagree -- and only the
    legacy copy was compressed under pressure (report C-3).  Deriving the
    aliases from the layers makes divergence structurally impossible.
    """

    dynamic = layers.get("dynamic_tail") if isinstance(layers.get("dynamic_tail"), Mapping) else {}
    world = layers.get("world_state") if isinstance(layers.get("world_state"), Mapping) else {}
    return {
        "selected_plan": dynamic.get("selected_plan"),
        "observation_summary": dynamic.get("observation_summary"),
        "memory_summary": (world.get("candidate_state") or {}),
        "previous_decision": dynamic.get("last_decision"),
    }


def build_context_pack(
    task: AgentTask,
    *,
    plan: Optional[Mapping[str, Any]] = None,
    observation: Optional[Mapping[str, Any]] = None,
    previous_decision: Optional[Mapping[str, Any]] = None,
    memory_context: Optional[Mapping[str, Any]] = None,
    runtime_state: Optional[Mapping[str, Any]] = None,
    snapshot_ids: Optional[Mapping[str, Any]] = None,
    skills: Optional[Sequence[Any]] = None,
    run_dir: Optional[str | Path] = None,
    max_chars: int = 60000,
    max_tokens: int = CONTEXT_TOKEN_BUDGET,
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
        skills=skills,
        run_dir=run_dir,
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
        "memory_context": layers["memory_context"],
        "available_tools": [item["tool_name"] for item in registered_tools(task.allowed_tools)],
        **_legacy_aliases(layers),
    }
    pack = {**legacy, **layers}
    pack["token_budget"] = {
        **dict(layers.get("token_budget") or {}),
        "budget_tokens": int(max_tokens),
    }
    if len(json.dumps(pack, ensure_ascii=False, sort_keys=True)) <= max_chars and estimate_tokens(pack) <= max_tokens:
        pack["token_budget"]["within_budget"] = True
        return pack
    # Preserve the v2 contract for normal production calls.  Its layers are
    # already independently bounded; reduce the duplicated legacy aliases and
    # the observation-heavy dynamic tail, then re-derive the aliases so the two
    # copies cannot disagree.
    if max_chars >= 5000:
        dynamic = dict(layers["dynamic_tail"])
        dynamic["observation_summary"] = _truncate(observation or {}, 500)
        dynamic["selected_plan"] = _truncate(plan or {}, 500)
        dynamic["last_decision"] = _truncate(previous_decision or {}, 500)
        layers["dynamic_tail"] = dynamic
        pack = {**legacy, **layers, **_legacy_aliases(layers)}
        pack["token_budget"] = {
            **dict(layers.get("token_budget") or {}),
            "budget_tokens": int(max_tokens),
            "estimated_tokens": estimate_tokens(pack),
            "within_budget": False,
            "compacted": True,
        }
        return pack
    # Very small diagnostic limits retain the legacy compact behavior.
    return _truncate(pack, max_chars)
