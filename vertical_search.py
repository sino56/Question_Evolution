"""Pure coordination primitives for vertical multi-operator search.

The module deliberately contains no model or stage calls.  It defines the
stable identities, lightweight state, per-node metadata, operator ordering,
and boundary edge/path records used by the production runner.  Full pipeline
records remain the source of truth for generated questions and evaluations.
"""

from __future__ import annotations

import hashlib
import time
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from prompts.operators import OPERATOR_SPECS


VERTICAL_SEARCH_STATE_VERSION = 1
VERTICAL_NODE_VERSION = 1
VERTICAL_ATTEMPT_VERSION = 1
VERTICAL_SEARCH_MODE = "multi_operator_vertical_stack"
DEFAULT_MAX_DEPTH = 3
DEFAULT_BOUNDARY_TARGET = 5

EVOLUTION_REQUIRED_ACTIONS = {
    "evolve_high_score_overscore",
    "reconstruct_low_score_boundary",
    "probe_middle_score_boundary",
}

NORMAL_TERMINATION_REASONS = {
    "operator_space_exhausted",
    "boundary_target_reached",
}
SYSTEM_TERMINATION_REASONS = {
    "request_budget_exhausted",
    "evaluation_budget_exhausted",
    "timeout",
    "fatal_error",
    "state_recovery_failed",
}

# First-stage fixed preferences from the design.  They affect order only.
COMPLEMENTARY_OPERATORS = {
    "O10_evidence_sufficiency_ladder": (
        "O15_counterfactual_threshold_shift",
        "O17_action_vs_fact_threshold",
        "O18_baseline_scope_mismatch",
    ),
    "O11_unobserved_state_attribution": (
        "O16_close_alternative_normalization",
        "O15_counterfactual_threshold_shift",
        "O17_action_vs_fact_threshold",
    ),
    "O14_information_closure": (
        "O10_evidence_sufficiency_ladder",
        "O12_conjunctive_necessity",
    ),
}


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        text = _clean(value)
        if text and text not in result:
            result.append(text)
    return result


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed if parsed > 0 else default


def _nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, default)


def coerce_score_rate(record: Mapping[str, Any]) -> Optional[float]:
    value = record.get("score_rate")
    if value is None:
        scoring = record.get("scoring_result")
        scoring = scoring if isinstance(scoring, Mapping) else {}
        awarded = scoring.get("total_awarded")
        possible = scoring.get("total_possible")
        try:
            possible_value = float(possible)
            if possible_value > 0:
                value = float(awarded) / possible_value
        except (TypeError, ValueError):
            value = None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def normalized_prompt_hash(prompt: Any) -> str:
    normalized = " ".join(_clean(prompt).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def sample_identity(record: Mapping[str, Any]) -> str:
    for field in ("sample_id", "index"):
        value = _clean(record.get(field))
        if value:
            return value
    prompt_hash = normalized_prompt_hash(record.get("prompt"))
    return f"prompt-{prompt_hash[:16]}" if prompt_hash else "unknown-sample"


def make_root_node_id(record: Mapping[str, Any]) -> str:
    return f"{sample_identity(record)}::root"


def make_node_id(root_node_id: str, operator_stack: Sequence[str]) -> str:
    root = _clean(root_node_id)
    operators = [_clean(value) for value in operator_stack]
    if not root:
        raise ValueError("root_node_id is required")
    if any(not operator for operator in operators):
        raise ValueError("operator_stack entries must not be empty")
    return root if not operators else f"{root}::{'::'.join(operators)}"


def make_attempt_id(parent_node_id: str, operator_id: str) -> str:
    parent = _clean(parent_node_id)
    operator = _clean(operator_id)
    if not parent or not operator:
        raise ValueError("parent_node_id and operator_id are required")
    return f"{parent}::try::{operator}"


def make_path_id(sample_id: str, operator_stack: Sequence[str]) -> str:
    identity = _clean(sample_id)
    operators = [_clean(value) for value in operator_stack]
    if not identity or not operators or any(not value for value in operators):
        raise ValueError("sample_id and a non-empty operator_stack are required")
    return f"{identity}::path::{'>'.join(operators)}"


def should_enter_vertical_search(record: Mapping[str, Any]) -> bool:
    return _clean(record.get("evolution_action")) in EVOLUTION_REQUIRED_ACTIONS


def build_root_node(record: Mapping[str, Any], *, max_depth: int) -> Dict[str, Any]:
    max_depth = _positive_int(max_depth, DEFAULT_MAX_DEPTH)
    root_id = make_root_node_id(record)
    rate = coerce_score_rate(record)
    if rate is None:
        raise ValueError(f"root node {root_id} requires score_rate")
    return {
        "vertical_node_version": VERTICAL_NODE_VERSION,
        "node_id": root_id,
        "sample_id": sample_identity(record),
        "depth": 1,
        "root_node_id": root_id,
        "parent_node_id": None,
        "operator_from_parent": None,
        "operator_stack": [],
        "operator_set": [],
        "score_rate": rate,
        "parent_score_rate": None,
        "root_score_rate": rate,
        "edge_delta_score_rate": None,
        "root_delta_score_rate": 0.0,
        "node_status": "root",
        "frontier_status": "eligible" if max_depth > 1 else "depth_limit",
        "review_status": None,
        "generation_sequence": 0,
    }


def initialize_vertical_search_state(
    record: Mapping[str, Any],
    *,
    max_depth: int = DEFAULT_MAX_DEPTH,
    boundary_target: int = DEFAULT_BOUNDARY_TARGET,
    allow_operator_repeat_in_path: bool = False,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Return ``None`` for samples that the existing flow does not evolve."""

    if not should_enter_vertical_search(record):
        return None
    root = build_root_node(record, max_depth=max_depth)
    prompt_hash = normalized_prompt_hash(record.get("prompt"))
    return {
        "vertical_search_state_version": VERTICAL_SEARCH_STATE_VERSION,
        "search_mode": VERTICAL_SEARCH_MODE,
        "root_node_id": root["node_id"],
        "status": "running",
        "current_depth": 1,
        "max_depth": _positive_int(max_depth, DEFAULT_MAX_DEPTH),
        "boundary_target": _positive_int(boundary_target, DEFAULT_BOUNDARY_TARGET),
        "boundary_candidate_count": 0,
        "completed_operator_attempt_count": 0,
        "pending_expandable_node_count": 1,
        "termination_reason": None,
        "allow_operator_repeat_in_path": bool(allow_operator_repeat_in_path),
        "frontier_node_ids": [root["node_id"]],
        "completed_parent_node_ids": [],
        "registered_node_ids": [root["node_id"]],
        "registered_prompt_hashes": [prompt_hash] if prompt_hash else [],
        "execution_sequence": [],
        "last_progress_at": float(now if now is not None else time.time()),
    }


def upgrade_vertical_search_state(raw_state: Mapping[str, Any]) -> Dict[str, Any]:
    state = deepcopy(dict(raw_state))
    raw_version = state.get("vertical_search_state_version")
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid vertical_search_state_version: {raw_version!r}") from exc
    if version != VERTICAL_SEARCH_STATE_VERSION:
        raise ValueError(
            "vertical search state version mismatch; refuse to mix "
            f"version {version} and {VERTICAL_SEARCH_STATE_VERSION}"
        )
    if _clean(state.get("search_mode")) != VERTICAL_SEARCH_MODE:
        raise ValueError("invalid vertical search mode")
    state["max_depth"] = _positive_int(state.get("max_depth"), DEFAULT_MAX_DEPTH)
    state["boundary_target"] = _positive_int(
        state.get("boundary_target"), DEFAULT_BOUNDARY_TARGET
    )
    state["boundary_candidate_count"] = min(
        _nonnegative_int(state.get("boundary_candidate_count")),
        state["boundary_target"],
    )
    state["completed_operator_attempt_count"] = _nonnegative_int(
        state.get("completed_operator_attempt_count")
    )
    for field in (
        "frontier_node_ids",
        "completed_parent_node_ids",
        "registered_node_ids",
        "registered_prompt_hashes",
    ):
        state[field] = _unique(state.get(field) or [])
    sequence = state.get("execution_sequence")
    state["execution_sequence"] = list(sequence) if isinstance(sequence, list) else []
    state["pending_expandable_node_count"] = len(state["frontier_node_ids"])
    state["current_depth"] = _positive_int(state.get("current_depth"), 1)
    state["allow_operator_repeat_in_path"] = bool(
        state.get("allow_operator_repeat_in_path")
    )
    state.setdefault("status", "running")
    state.setdefault("termination_reason", None)
    state.setdefault("last_progress_at", 0.0)
    return state


def generation_operator_ids() -> List[str]:
    return [
        operator_id
        for operator_id, spec in OPERATOR_SPECS.items()
        if bool(getattr(spec, "generates_question", True))
    ]


def build_vertical_operator_plan(
    routed_record: Mapping[str, Any],
    *,
    operator_stack: Sequence[str],
    allow_operator_repeat_in_path: bool = False,
    registered_operator_ids: Optional[Sequence[str]] = None,
) -> List[str]:
    """Build a deterministic per-frontier plan without hard-banning avoid ids."""

    route = routed_record.get("operator_route")
    route = route if isinstance(route, Mapping) else {}
    registered = _unique(registered_operator_ids or generation_operator_ids())
    registered_set = set(registered)
    avoid = set(_unique(route.get("avoid_operators") or []))
    used = set(_unique(operator_stack))
    last_operator = _clean(operator_stack[-1]) if operator_stack else ""

    preferred = _unique(
        [route.get("primary_operator")]
        + list(route.get("backup_operators") or [])
        + list(COMPLEMENTARY_OPERATORS.get(last_operator, ()))
        + registered
    )
    preferred = [operator for operator in preferred if operator in registered_set]
    if not allow_operator_repeat_in_path:
        preferred = [operator for operator in preferred if operator not in used]
    else:
        preferred = [operator for operator in preferred if operator not in used] + [
            operator for operator in preferred if operator in used
        ]
    return [operator for operator in preferred if operator not in avoid] + [
        operator for operator in preferred if operator in avoid
    ]


def build_child_node(
    parent_node: Mapping[str, Any],
    branch_record: Mapping[str, Any],
    *,
    max_depth: int,
    generation_sequence: int,
) -> Dict[str, Any]:
    parent_rate = coerce_score_rate(parent_node)
    child_rate = coerce_score_rate(branch_record)
    if parent_rate is None or child_rate is None:
        raise ValueError("completed vertical nodes require parent and child score_rate")
    operator_id = _clean(
        branch_record.get("operator_id") or branch_record.get("candidate_operator")
    )
    if not operator_id:
        raise ValueError("completed vertical node requires operator_id")
    parent_stack = [_clean(value) for value in parent_node.get("operator_stack") or []]
    operator_stack = parent_stack + [operator_id]
    root_id = _clean(parent_node.get("root_node_id"))
    node_id = make_node_id(root_id, operator_stack)
    root_rate = parent_node.get("root_score_rate")
    try:
        root_rate_value = float(root_rate)
    except (TypeError, ValueError) as exc:
        raise ValueError("parent node requires root_score_rate") from exc
    edge_delta = child_rate - parent_rate
    if edge_delta < 0:
        node_status = "boundary_candidate"
        frontier_status = (
            "eligible"
            if int(parent_node.get("depth") or 0) + 1 < _positive_int(max_depth, DEFAULT_MAX_DEPTH)
            else "depth_limit"
        )
        review_status: Optional[str] = "pending"
    elif edge_delta > 0:
        node_status = "score_increased"
        frontier_status = "ineligible"
        review_status = None
    else:
        node_status = "no_score_change"
        frontier_status = "ineligible"
        review_status = None
    return {
        "vertical_node_version": VERTICAL_NODE_VERSION,
        "node_id": node_id,
        "sample_id": _clean(parent_node.get("sample_id")),
        "depth": int(parent_node.get("depth") or 0) + 1,
        "root_node_id": root_id,
        "parent_node_id": _clean(parent_node.get("node_id")),
        "operator_from_parent": operator_id,
        "operator_stack": operator_stack,
        "operator_set": sorted(set(operator_stack)),
        "score_rate": child_rate,
        "parent_score_rate": parent_rate,
        "root_score_rate": root_rate_value,
        "edge_delta_score_rate": edge_delta,
        "root_delta_score_rate": child_rate - root_rate_value,
        "node_status": node_status,
        "frontier_status": frontier_status,
        "review_status": review_status,
        "generation_sequence": _nonnegative_int(generation_sequence),
    }


def attach_vertical_node(
    record: Mapping[str, Any], node: Mapping[str, Any]
) -> Dict[str, Any]:
    result = deepcopy(dict(record))
    metadata = deepcopy(dict(node))
    result.update(metadata)
    result["vertical_node"] = metadata
    return result


def build_boundary_edge(node: Mapping[str, Any]) -> Dict[str, Any]:
    if _clean(node.get("node_status")) != "boundary_candidate":
        raise ValueError("boundary edge requires a boundary_candidate node")
    return {
        "edge_id": f"{node['parent_node_id']}--{node['operator_from_parent']}-->{node['node_id']}",
        "sample_id": node["sample_id"],
        "root_node_id": node["root_node_id"],
        "parent_node_id": node["parent_node_id"],
        "child_node_id": node["node_id"],
        "operator_id": node["operator_from_parent"],
        "depth": node["depth"],
        "parent_score_rate": node["parent_score_rate"],
        "child_score_rate": node["score_rate"],
        "edge_delta_score_rate": node["edge_delta_score_rate"],
        "root_delta_score_rate": node["root_delta_score_rate"],
        "review_status": "pending",
    }


def build_boundary_path(
    node: Mapping[str, Any],
    nodes_by_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    if _clean(node.get("node_status")) != "boundary_candidate":
        raise ValueError("boundary path requires a boundary_candidate node")
    chain: List[Mapping[str, Any]] = []
    current: Optional[Mapping[str, Any]] = node
    while current is not None and int(current.get("depth") or 0) > 1:
        chain.append(current)
        current = nodes_by_id.get(_clean(current.get("parent_node_id")))
    if current is None or _clean(current.get("node_id")) != _clean(node.get("root_node_id")):
        raise ValueError("boundary path is missing an ancestor node")
    chain.reverse()
    operator_stack = [_clean(item.get("operator_from_parent")) for item in chain]
    return {
        "path_id": make_path_id(_clean(node.get("sample_id")), operator_stack),
        "sample_id": node["sample_id"],
        "root_node_id": node["root_node_id"],
        "leaf_node_id": node["node_id"],
        "node_ids": [node["root_node_id"]] + [_clean(item.get("node_id")) for item in chain],
        "operator_stack": operator_stack,
        "operator_set": sorted(set(operator_stack)),
        "depth": node["depth"],
        "edge_deltas": [float(item["edge_delta_score_rate"]) for item in chain],
        "root_delta_score_rate": node["root_delta_score_rate"],
        "review_status": "pending",
    }


def build_operator_attempt(
    parent_node: Mapping[str, Any],
    operator_entry: Mapping[str, Any],
    *,
    operator_rank: int,
    branch_summary: Optional[Mapping[str, Any]] = None,
    status_override: Optional[str] = None,
) -> Dict[str, Any]:
    operator_id = _clean(operator_entry.get("operator_id"))
    status = _clean(status_override or operator_entry.get("status"))
    summary = branch_summary if isinstance(branch_summary, Mapping) else {}
    return {
        "vertical_attempt_version": VERTICAL_ATTEMPT_VERSION,
        "attempt_id": make_attempt_id(_clean(parent_node.get("node_id")), operator_id),
        "parent_node_id": parent_node["node_id"],
        "sample_id": parent_node["sample_id"],
        "operator_id": operator_id,
        "depth": int(parent_node.get("depth") or 0) + 1,
        "operator_rank": _positive_int(operator_rank, 1),
        "branch_id": _clean(operator_entry.get("branch_id")),
        "status": status,
        "branch_status": _clean(summary.get("branch_status")) or None,
        "generation_attempt_count": _nonnegative_int(
            operator_entry.get("generation_attempt_count")
        ),
        "validation_retry_count": _nonnegative_int(
            operator_entry.get("validation_retry_count")
        ),
        "duplicate_retry_count": _nonnegative_int(
            operator_entry.get("duplicate_retry_count")
        ),
        "failure_reasons": list(operator_entry.get("failure_reasons") or []),
    }


def mark_system_termination(
    state: Mapping[str, Any], reason: str, *, now: Optional[float] = None
) -> Dict[str, Any]:
    if reason not in SYSTEM_TERMINATION_REASONS:
        raise ValueError(f"unsupported system termination reason: {reason}")
    updated = upgrade_vertical_search_state(state)
    updated["status"] = "partial"
    updated["termination_reason"] = reason
    updated["last_progress_at"] = float(now if now is not None else time.time())
    return updated


def claim_next_frontier(
    state: Mapping[str, Any],
    nodes_by_id: Mapping[str, Mapping[str, Any]],
    *,
    now: Optional[float] = None,
) -> tuple[Dict[str, Any], Optional[str]]:
    """Claim one frontier deterministically while keeping it recoverable."""

    updated = upgrade_vertical_search_state(state)
    if updated["status"] != "running":
        return updated, None
    active = _clean(updated.get("active_parent_node_id"))
    if active:
        if active not in nodes_by_id:
            return mark_system_termination(updated, "state_recovery_failed", now=now), None
        return updated, active

    missing_frontier = [
        node_id
        for node_id in updated["frontier_node_ids"]
        if node_id not in nodes_by_id
    ]
    if missing_frontier:
        failed = mark_system_termination(updated, "state_recovery_failed", now=now)
        failed["recovery_error"] = (
            "missing frontier nodes: " + ", ".join(missing_frontier)
        )
        return failed, None

    candidates = [
        node_id
        for node_id in updated["frontier_node_ids"]
        if node_id not in set(updated["completed_parent_node_ids"])
        and node_id in nodes_by_id
        and int(nodes_by_id[node_id].get("depth") or 0) < updated["max_depth"]
    ]
    candidates.sort(
        key=lambda node_id: (
            int(nodes_by_id[node_id].get("depth") or 0),
            int(nodes_by_id[node_id].get("generation_sequence") or 0),
            node_id,
        )
    )
    if not candidates:
        updated["status"] = "completed"
        updated["termination_reason"] = "operator_space_exhausted"
        updated["frontier_node_ids"] = []
        updated["pending_expandable_node_count"] = 0
        updated["last_progress_at"] = float(now if now is not None else time.time())
        return updated, None

    node_id = candidates[0]
    updated["active_parent_node_id"] = node_id
    updated["current_depth"] = int(nodes_by_id[node_id].get("depth") or 1)
    updated["execution_sequence"].append(
        {
            "sequence": len(updated["execution_sequence"]) + 1,
            "parent_node_id": node_id,
            "depth": updated["current_depth"],
            "status": "running",
        }
    )
    updated["last_progress_at"] = float(now if now is not None else time.time())
    return updated, node_id


def complete_frontier(
    state: Mapping[str, Any],
    parent_node: Mapping[str, Any],
    child_records: Sequence[Mapping[str, Any]],
    *,
    completed_attempt_count: int,
    operator_plan: Sequence[str],
    memory_version: Optional[Mapping[str, Any]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Merge one fully processed parent and enqueue only decreasing children."""

    updated = upgrade_vertical_search_state(state)
    parent_id = _clean(parent_node.get("node_id"))
    active = _clean(updated.get("active_parent_node_id"))
    if active and active != parent_id:
        raise ValueError(f"active frontier mismatch: {active} != {parent_id}")
    if parent_id in updated["completed_parent_node_ids"]:
        return updated

    updated["completed_parent_node_ids"].append(parent_id)
    updated["frontier_node_ids"] = [
        node_id for node_id in updated["frontier_node_ids"] if node_id != parent_id
    ]
    updated["completed_operator_attempt_count"] += _nonnegative_int(
        completed_attempt_count
    )
    for record in child_records:
        node = record.get("vertical_node")
        node = node if isinstance(node, Mapping) else record
        node_id = _clean(node.get("node_id"))
        if not node_id:
            raise ValueError("completed child record is missing node_id")
        if node_id not in updated["registered_node_ids"]:
            updated["registered_node_ids"].append(node_id)
        prompt_hash = normalized_prompt_hash(record.get("prompt"))
        if prompt_hash:
            if prompt_hash in updated["registered_prompt_hashes"]:
                raise ValueError(f"duplicate prompt reached completed node: {node_id}")
            updated["registered_prompt_hashes"].append(prompt_hash)
        if _clean(node.get("node_status")) != "boundary_candidate":
            continue
        if updated["boundary_candidate_count"] >= updated["boundary_target"]:
            raise ValueError("vertical boundary target overflow")
        updated["boundary_candidate_count"] += 1
        if (
            _clean(node.get("frontier_status")) == "eligible"
            and updated["boundary_candidate_count"] < updated["boundary_target"]
            and node_id not in updated["frontier_node_ids"]
        ):
            updated["frontier_node_ids"].append(node_id)

    for execution in reversed(updated["execution_sequence"]):
        if _clean(execution.get("parent_node_id")) == parent_id:
            execution["status"] = "completed"
            execution["operator_plan"] = list(operator_plan)
            if memory_version is not None:
                execution["memory_version"] = deepcopy(dict(memory_version))
            break
    updated.pop("active_parent_node_id", None)
    if updated["boundary_candidate_count"] >= updated["boundary_target"]:
        updated["status"] = "completed"
        updated["termination_reason"] = "boundary_target_reached"
        updated["frontier_node_ids"] = []
    elif not updated["frontier_node_ids"]:
        updated["status"] = "completed"
        updated["termination_reason"] = "operator_space_exhausted"
    else:
        updated["current_depth"] = min(
            int(record.get("vertical_node", record).get("depth") or 1)
            for record in child_records
            if _clean(record.get("vertical_node", record).get("node_id"))
            in updated["frontier_node_ids"]
        ) if any(
            _clean(record.get("vertical_node", record).get("node_id"))
            in updated["frontier_node_ids"]
            for record in child_records
        ) else updated["current_depth"]
    updated["pending_expandable_node_count"] = len(updated["frontier_node_ids"])
    updated["last_progress_at"] = float(now if now is not None else time.time())
    return updated
