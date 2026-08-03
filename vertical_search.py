"""Pure coordination primitives for vertical multi-operator search.

The module deliberately contains no model or stage calls.  It defines the
stable identities, lightweight state, per-node metadata, operator ordering,
and boundary edge/path records used by the production runner.  Full pipeline
records remain the source of truth for generated questions and evaluations.
"""

from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from prompts.operators import OPERATOR_SPECS


VERTICAL_SEARCH_STATE_VERSION = 2
VERTICAL_NODE_VERSION = 1
VERTICAL_ATTEMPT_VERSION = 1
VERTICAL_SEARCH_MODE = "multi_operator_vertical_stack"
DEFAULT_MAX_DEPTH = 3
DEFAULT_SINGLE_OPERATOR_BOUNDARY_TARGET = 5
DEFAULT_STACKED_OPERATOR_BOUNDARY_TARGET = 5
DEFAULT_TOTAL_BOUNDARY_HARD_CAP = 10
# Backward-compatible public name for callers that still use the original
# single target flag.  New vertical state always stores layer-specific limits.
DEFAULT_BOUNDARY_TARGET = DEFAULT_SINGLE_OPERATOR_BOUNDARY_TARGET

EVOLUTION_REQUIRED_ACTIONS = {
    "evolve_high_score_overscore",
    "reconstruct_low_score_boundary",
    "probe_middle_score_boundary",
}

NORMAL_TERMINATION_REASONS = {
    "operator_space_exhausted",
    "single_operator_boundary_target_reached",
    "stacked_operator_boundary_target_reached",
    "total_boundary_hard_cap_reached",
}
SYSTEM_TERMINATION_REASONS = {
    "request_budget_exhausted",
    "evaluation_budget_exhausted",
    "timeout",
    "fatal_error",
    "state_recovery_failed",
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


def _nonnegative_config_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, default)


def _max_depth(value: Any) -> int:
    """The first vertical-search phase permits root plus at most two operators."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_MAX_DEPTH
    if parsed not in {2, 3}:
        raise ValueError("vertical max_depth must be 2 or 3")
    return parsed


def _quota_config(
    *,
    max_depth: int,
    boundary_target: Optional[int],
    single_operator_boundary_target: Optional[int],
    stacked_operator_boundary_target: Optional[int],
    total_boundary_hard_cap: Optional[int],
) -> tuple[int, int, int]:
    """Resolve new layered quotas while retaining the original CLI/API alias."""

    legacy_target = (
        _positive_int(boundary_target, DEFAULT_BOUNDARY_TARGET)
        if boundary_target is not None
        else None
    )
    single_target = _positive_int(
        single_operator_boundary_target,
        legacy_target or DEFAULT_SINGLE_OPERATOR_BOUNDARY_TARGET,
    )
    if max_depth == 2:
        if (
            stacked_operator_boundary_target is not None
            and _nonnegative_config_int(stacked_operator_boundary_target) != 0
        ):
            raise ValueError("max_depth=2 requires stacked_operator_boundary_target=0")
        stacked_target = 0
    else:
        stacked_target = _nonnegative_config_int(
            stacked_operator_boundary_target,
            legacy_target or DEFAULT_STACKED_OPERATOR_BOUNDARY_TARGET,
        )
    hard_cap = _positive_int(
        total_boundary_hard_cap,
        single_target + stacked_target,
    )
    if hard_cap < max(single_target, stacked_target):
        raise ValueError(
            "total_boundary_hard_cap must be at least each enabled layer target"
        )
    return single_target, stacked_target, hard_cap


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


def input_record_sha256(record: Mapping[str, Any]) -> str:
    """Fingerprint the complete vertical-search input used by a checkpoint.

    A sample id is an execution label, not proof that its prompt, baseline
    score, or evaluation evidence is unchanged.  Reusing a checkpoint for a
    different input would silently mix two search trees.
    """

    payload = json.dumps(
        dict(record),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


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
    max_depth = _max_depth(max_depth)
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
    boundary_target: Optional[int] = None,
    single_operator_boundary_target: Optional[int] = None,
    stacked_operator_boundary_target: Optional[int] = None,
    total_boundary_hard_cap: Optional[int] = None,
    allow_operator_repeat_in_path: bool = False,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Return ``None`` for samples that the existing flow does not evolve."""

    if not should_enter_vertical_search(record):
        return None
    resolved_max_depth = _max_depth(max_depth)
    single_target, stacked_target, hard_cap = _quota_config(
        max_depth=resolved_max_depth,
        boundary_target=boundary_target,
        single_operator_boundary_target=single_operator_boundary_target,
        stacked_operator_boundary_target=stacked_operator_boundary_target,
        total_boundary_hard_cap=total_boundary_hard_cap,
    )
    root = build_root_node(record, max_depth=resolved_max_depth)
    prompt_hash = normalized_prompt_hash(record.get("prompt"))
    return {
        "vertical_search_state_version": VERTICAL_SEARCH_STATE_VERSION,
        "search_mode": VERTICAL_SEARCH_MODE,
        "root_node_id": root["node_id"],
        "input_record_sha256": input_record_sha256(record),
        "status": "running",
        "current_depth": 1,
        "max_depth": resolved_max_depth,
        "single_operator_boundary_target": single_target,
        "stacked_operator_boundary_target": stacked_target,
        "total_boundary_hard_cap": hard_cap,
        "single_operator_boundary_count": 0,
        "stacked_operator_boundary_count": 0,
        "total_boundary_count": 0,
        # Kept as an aggregate compatibility projection for existing analysis
        # tools.  New scheduling logic must use the typed counts above.
        "boundary_target": hard_cap,
        "boundary_candidate_count": 0,
        "completed_operator_attempt_count": 0,
        "pending_expandable_node_count": 1,
        "pending_frontier_count": 1,
        "single_operator_root_expansion_stopped": False,
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
    if version not in {1, VERTICAL_SEARCH_STATE_VERSION}:
        raise ValueError(
            "vertical search state version mismatch; refuse to mix "
            f"version {version} and {VERTICAL_SEARCH_STATE_VERSION}"
        )
    if _clean(state.get("search_mode")) != VERTICAL_SEARCH_MODE:
        raise ValueError("invalid vertical search mode")
    state["max_depth"] = _max_depth(state.get("max_depth"))
    if version == 1:
        # A version-1 checkpoint only had an aggregate boundary count.  It is
        # retained as a provisional single-layer count until the runner
        # reconciles it with durable node artifacts on resume.
        legacy_target = _positive_int(
            state.get("boundary_target"), DEFAULT_BOUNDARY_TARGET
        )
        state.update(
            {
                "vertical_search_state_version": VERTICAL_SEARCH_STATE_VERSION,
                "single_operator_boundary_target": legacy_target,
                "stacked_operator_boundary_target": (
                    legacy_target if state["max_depth"] == 3 else 0
                ),
                "total_boundary_hard_cap": (
                    legacy_target * 2 if state["max_depth"] == 3 else legacy_target
                ),
                "single_operator_boundary_count": _nonnegative_int(
                    state.get("boundary_candidate_count")
                ),
                "stacked_operator_boundary_count": 0,
                "single_operator_root_expansion_stopped": False,
            }
        )
    single_target, stacked_target, hard_cap = _quota_config(
        max_depth=state["max_depth"],
        boundary_target=None,
        single_operator_boundary_target=state.get("single_operator_boundary_target"),
        stacked_operator_boundary_target=state.get("stacked_operator_boundary_target"),
        total_boundary_hard_cap=state.get("total_boundary_hard_cap"),
    )
    state["single_operator_boundary_target"] = single_target
    state["stacked_operator_boundary_target"] = stacked_target
    state["total_boundary_hard_cap"] = hard_cap
    state["single_operator_boundary_count"] = _nonnegative_int(
        state.get("single_operator_boundary_count")
    )
    state["stacked_operator_boundary_count"] = _nonnegative_int(
        state.get("stacked_operator_boundary_count")
    )
    state["total_boundary_count"] = (
        state["single_operator_boundary_count"]
        + state["stacked_operator_boundary_count"]
    )
    state["boundary_target"] = state["total_boundary_hard_cap"]
    state["boundary_candidate_count"] = state["total_boundary_count"]
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
    state["pending_frontier_count"] = len(state["frontier_node_ids"])
    state["current_depth"] = _positive_int(state.get("current_depth"), 1)
    state["allow_operator_repeat_in_path"] = bool(
        state.get("allow_operator_repeat_in_path")
    )
    state["single_operator_root_expansion_stopped"] = bool(
        state.get("single_operator_root_expansion_stopped")
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
    """Filter the router's final candidate list for one ordered path.

    Vertical search must not expand the route into a registry-wide enumeration:
    the fresh route plus its runtime constraints are the source of truth.
    """

    route = routed_record.get("operator_route")
    route = route if isinstance(route, Mapping) else {}
    registered = _unique(registered_operator_ids or generation_operator_ids())
    registered_set = set(registered)
    avoid = set(_unique(route.get("avoid_operators") or []))
    used = set(_unique(operator_stack))

    explicit_candidates = route.get("selected_operator_ids")
    preferred = _unique(
        explicit_candidates
        if isinstance(explicit_candidates, list)
        else [route.get("primary_operator")] + list(route.get("backup_operators") or [])
    )
    preferred = [operator for operator in preferred if operator in registered_set]
    if not allow_operator_repeat_in_path:
        preferred = [operator for operator in preferred if operator not in used]
    else:
        preferred = [operator for operator in preferred if operator not in used] + [
            operator for operator in preferred if operator in used
        ]
    return [operator for operator in preferred if operator not in avoid]


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
        "boundary_kind": (
            "single_operator" if node_status == "boundary_candidate" and int(parent_node.get("depth") or 0) == 1
            else "stacked_operator" if node_status == "boundary_candidate" else None
        ),
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
        "boundary_kind": node.get("boundary_kind"),
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
        "boundary_kind": node.get("boundary_kind"),
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


def reconcile_vertical_boundary_counts(
    state: Mapping[str, Any],
    nodes_by_id: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    """Rebuild layered counters from durable completed-node evidence.

    This is intentionally narrow: normalized node artifacts are the recovery
    authority for boundary counts, while the checkpoint remains the authority
    for queue ownership and active execution.
    """

    updated = upgrade_vertical_search_state(state)
    single_count = 0
    stacked_count = 0
    for node in nodes_by_id.values():
        if _clean(node.get("node_status")) != "boundary_candidate":
            continue
        depth = int(node.get("depth") or 0)
        if depth == 2:
            single_count += 1
        elif depth == 3:
            stacked_count += 1
    updated["single_operator_boundary_count"] = single_count
    updated["stacked_operator_boundary_count"] = stacked_count
    updated["total_boundary_count"] = single_count + stacked_count
    updated["boundary_candidate_count"] = updated["total_boundary_count"]
    updated["boundary_target"] = updated["total_boundary_hard_cap"]
    return updated


def _refresh_pending_frontier_counts(state: Dict[str, Any]) -> None:
    count = len(state.get("frontier_node_ids") or [])
    state["pending_expandable_node_count"] = count
    state["pending_frontier_count"] = count


def _complete_from_quotas_or_exhaustion(
    state: Dict[str, Any],
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Apply quota precedence without confusing it with human review."""

    total = int(state["total_boundary_count"])
    if total >= int(state["total_boundary_hard_cap"]):
        state["status"] = "completed"
        state["termination_reason"] = "total_boundary_hard_cap_reached"
        state["frontier_node_ids"] = []
    elif (
        int(state["stacked_operator_boundary_target"]) > 0
        and int(state["stacked_operator_boundary_count"])
        >= int(state["stacked_operator_boundary_target"])
    ):
        state["status"] = "completed"
        state["termination_reason"] = "stacked_operator_boundary_target_reached"
        state["frontier_node_ids"] = []
    elif not state.get("frontier_node_ids"):
        state["status"] = "completed"
        state["termination_reason"] = (
            "single_operator_boundary_target_reached"
            if state.get("single_operator_root_expansion_stopped")
            else "operator_space_exhausted"
        )
    _refresh_pending_frontier_counts(state)
    state["last_progress_at"] = float(now if now is not None else time.time())
    return state


def _parent_is_expandable(
    state: Mapping[str, Any], node: Mapping[str, Any]
) -> bool:
    depth = int(node.get("depth") or 0)
    if depth >= int(state["max_depth"]):
        return False
    if int(state["total_boundary_count"]) >= int(state["total_boundary_hard_cap"]):
        return False
    if depth == 1:
        return int(state["single_operator_boundary_count"]) < int(
            state["single_operator_boundary_target"]
        )
    if depth == 2:
        return (
            int(state["max_depth"]) == 3
            and int(state["stacked_operator_boundary_target"]) > 0
            and int(state["stacked_operator_boundary_count"])
            < int(state["stacked_operator_boundary_target"])
        )
    return False


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
        and _parent_is_expandable(updated, nodes_by_id[node_id])
    ]
    candidates.sort(
        key=lambda node_id: (
            int(nodes_by_id[node_id].get("depth") or 0),
            int(nodes_by_id[node_id].get("generation_sequence") or 0),
            node_id,
        )
    )
    if not candidates:
        updated["frontier_node_ids"] = []
        return _complete_from_quotas_or_exhaustion(updated, now=now), None

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
    """Merge one processed parent and enqueue only depth-2 decreasing nodes."""

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
    parent_depth = int(parent_node.get("depth") or 0)
    for record in child_records:
        node = record.get("vertical_node")
        node = node if isinstance(node, Mapping) else record
        node_id = _clean(node.get("node_id"))
        if not node_id:
            raise ValueError("completed child record is missing node_id")
        is_new_node = node_id not in updated["registered_node_ids"]
        if is_new_node:
            updated["registered_node_ids"].append(node_id)
        prompt_hash = normalized_prompt_hash(record.get("prompt"))
        if prompt_hash and is_new_node:
            if prompt_hash in updated["registered_prompt_hashes"]:
                raise ValueError(f"duplicate prompt reached completed node: {node_id}")
            updated["registered_prompt_hashes"].append(prompt_hash)
        if not is_new_node or _clean(node.get("node_status")) != "boundary_candidate":
            continue
        child_depth = int(node.get("depth") or 0)
        if int(updated["total_boundary_count"]) >= int(
            updated["total_boundary_hard_cap"]
        ):
            raise ValueError("vertical total boundary hard-cap overflow")
        if child_depth == 2:
            if int(updated["single_operator_boundary_count"]) >= int(
                updated["single_operator_boundary_target"]
            ):
                raise ValueError("single-operator boundary target overflow")
            updated["single_operator_boundary_count"] += 1
        elif child_depth == 3:
            if int(updated["stacked_operator_boundary_count"]) >= int(
                updated["stacked_operator_boundary_target"]
            ):
                raise ValueError("stacked-operator boundary target overflow")
            updated["stacked_operator_boundary_count"] += 1
        else:
            raise ValueError(f"unsupported vertical boundary depth: {child_depth}")
        updated["total_boundary_count"] = (
            int(updated["single_operator_boundary_count"])
            + int(updated["stacked_operator_boundary_count"])
        )
        updated["boundary_candidate_count"] = updated["total_boundary_count"]
        if (
            child_depth == 2
            and
            _clean(node.get("frontier_status")) == "eligible"
            and int(updated["max_depth"]) == 3
            and int(updated["stacked_operator_boundary_target"]) > 0
            and int(updated["stacked_operator_boundary_count"])
            < int(updated["stacked_operator_boundary_target"])
            and int(updated["total_boundary_count"])
            < int(updated["total_boundary_hard_cap"])
            and node_id not in updated["frontier_node_ids"]
        ):
            updated["frontier_node_ids"].append(node_id)

    if parent_depth == 1 and int(updated["single_operator_boundary_count"]) >= int(
        updated["single_operator_boundary_target"]
    ):
        updated["single_operator_root_expansion_stopped"] = True

    for execution in reversed(updated["execution_sequence"]):
        if _clean(execution.get("parent_node_id")) == parent_id:
            execution["status"] = "completed"
            execution["operator_plan"] = list(operator_plan)
            if memory_version is not None:
                execution["memory_version"] = deepcopy(dict(memory_version))
            break
    updated.pop("active_parent_node_id", None)
    if updated["frontier_node_ids"]:
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
    return _complete_from_quotas_or_exhaustion(updated, now=now)
