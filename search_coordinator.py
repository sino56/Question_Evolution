"""State machine for parent-scoped multi-operator branch search.

The coordinator deliberately owns only lightweight scheduling state.  Full
candidate, answer, rubric, and judge records are stored as branch artifacts and
referenced by stable ``branch_id`` values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from pipeline_runtime import load_json_records, stable_record_key
from operator_ranking import rank_selected_operators
from branch_artifacts import split_legacy_search_state
from prompts.operators import OPERATOR_SPECS


SEARCH_STATE_VERSION = 1
DEFAULT_BOUNDARY_TARGET = 5
DEFAULT_BRANCH_WINDOW = 1
ASSIGNMENT_MODE_NATURAL = "natural"
ASSIGNMENT_MODE_LIVE = "live"

OPERATOR_TERMINAL_STATUSES = {
    "completed",
    "duplicate_exhausted",
    "not_applicable",
    "validation_failed",
    "branch_error",
}
SEARCH_COMPLETION_REASONS = {
    "boundary_target_reached",
    "candidate_list_exhausted",
    "operator_space_exhausted",
}
SEARCH_PARTIAL_REASONS = {
    "partial_coverage",
    "evaluation_budget_exhausted",
    "request_budget_exhausted",
    "timeout",
    "fatal_error",
}
SEARCH_TERMINAL_REASONS = SEARCH_COMPLETION_REASONS | SEARCH_PARTIAL_REASONS | {"aborted"}
BRANCH_TERMINAL_STATUSES = {
    "boundary_candidate",
    "no_score_change",
    "score_increased",
    "duplicate_exhausted",
    "validation_failed",
    "branch_error",
    "not_applicable",
}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _unique_strings(values: Iterable[Any]) -> List[str]:
    result: List[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _as_nonnegative_int(value: Any, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return max(0, default)


def _as_positive_int(value: Any, default: int) -> int:
    parsed = _as_nonnegative_int(value, default)
    return parsed if parsed > 0 else default


def _assignment_mode(value: Any, default: str = ASSIGNMENT_MODE_NATURAL) -> str:
    mode = _clean_text(value).lower() or default
    if mode not in {ASSIGNMENT_MODE_NATURAL, ASSIGNMENT_MODE_LIVE}:
        raise ValueError(f"unsupported assignment_mode: {mode}")
    return mode


def _route_assignment_mode(record: Optional[Mapping[str, Any]]) -> str:
    route = record.get("operator_route") if isinstance(record, Mapping) else None
    return _assignment_mode(route.get("assignment_mode") if isinstance(route, Mapping) else None)


def parent_node_id(record: Mapping[str, Any]) -> str:
    existing = _clean_text(record.get("parent_node_id"))
    if existing:
        return existing
    for field in ("sample_id", "index"):
        identity = _clean_text(record.get(field))
        if identity:
            return f"{identity}::root"
    prompt = _clean_text(record.get("prompt"))
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    return f"prompt-{digest}::root"


def make_branch_id(parent_id: str, operator_id: str) -> str:
    if not _clean_text(parent_id) or not _clean_text(operator_id):
        raise ValueError("parent_id and operator_id are required for a branch ID")
    return f"{parent_id}::{operator_id}"


def _registered_generation_operator_ids() -> List[str]:
    return [
        operator_id
        for operator_id, spec in OPERATOR_SPECS.items()
        if bool(getattr(spec, "generates_question", True))
    ]


def selected_operator_ids(record: Mapping[str, Any], *, forced_coverage: bool = False) -> List[str]:
    """Freeze the current sample's eligible candidate list.

    Natural search consumes only the route's explicit candidate members.  The
    registry is used for validation and audit, never as a natural-search
    fallback.  Full coverage must be requested explicitly.
    """

    route = record.get("operator_route")
    route = route if isinstance(route, Mapping) else {}
    if forced_coverage:
        requested = route.get("forced_operator_ids")
        raw_ids = (
            requested
            if isinstance(requested, list) and requested
            else _registered_generation_operator_ids()
        )
    else:
        raw_ids: List[Any] = []
        explicit_list_found = False
        for field in ("selected_operator_ids", "candidate_operator_ids"):
            explicit = route.get(field)
            if isinstance(explicit, list):
                raw_ids.extend(explicit)
                explicit_list_found = True
                break
        if not explicit_list_found:
            raw_ids.append(route.get("primary_operator"))
            backups = route.get("backup_operators")
            if isinstance(backups, list):
                raw_ids.extend(backups)

    avoid = set(_unique_strings(route.get("avoid_operators") or []))
    selected: List[str] = []
    for operator_id in _unique_strings(raw_ids):
        spec = OPERATOR_SPECS.get(operator_id)
        if spec is None or not bool(getattr(spec, "generates_question", True)):
            continue
        if not forced_coverage and operator_id in avoid:
            continue
        selected.append(operator_id)
    return selected


def _validate_live_operator_route(record: Mapping[str, Any]) -> None:
    """Validate the frozen Router-to-search business interface.

    The search coordinator intentionally knows no Router evidence or scoring
    metrics.  It only checks the immutable selected list and its compatibility
    projections before it creates branch plans.
    """

    route = record.get("operator_route")
    if not isinstance(route, Mapping):
        raise ValueError("live assignment requires operator_route")
    if _clean_text(route.get("routing_mode")) != "hybrid":
        raise ValueError("live assignment requires routing_mode=hybrid")
    selected = route.get("selected_operator_ids")
    if not isinstance(selected, list):
        raise ValueError("live assignment requires operator_route.selected_operator_ids")
    normalized = _unique_strings(selected)
    if normalized != selected:
        raise ValueError("live selected_operator_ids must be unique, ordered strings")
    valid = selected_operator_ids(record)
    if valid != normalized:
        raise ValueError("live selected_operator_ids contain a non-executable or avoided operator")
    if normalized:
        if _clean_text(route.get("primary_operator")) != normalized[0]:
            raise ValueError("live primary_operator must project selected_operator_ids[0]")
        if _unique_strings(route.get("backup_operators") or []) != normalized[1:]:
            raise ValueError("live backup_operators must project the remaining selected_operator_ids")
    elif _clean_text(route.get("primary_operator")) or _unique_strings(route.get("backup_operators") or []):
        raise ValueError("empty live selected_operator_ids cannot have compatibility candidates")


def _operator_entry(parent_id: str, operator_id: str) -> Dict[str, Any]:
    return {
        "operator_id": operator_id,
        "status": "pending",
        "branch_id": make_branch_id(parent_id, operator_id),
        "branch_stage": "pending",
        "generation_attempt_count": 0,
        "validation_retry_count": 0,
        "duplicate_retry_count": 0,
        "failure_reasons": [],
    }


def _is_search_terminal(state: Mapping[str, Any]) -> bool:
    if _clean_text(state.get("termination_reason")) in SEARCH_TERMINAL_REASONS:
        return True
    return _clean_text(state.get("status")) in {"completed", "aborted", "partial"}


def _derive_attempted_operator_ids(operator_plan: Sequence[Mapping[str, Any]]) -> List[str]:
    return [
        _clean_text(entry.get("operator_id"))
        for entry in operator_plan
        if _clean_text(entry.get("status")) != "pending"
        and _clean_text(entry.get("operator_id"))
    ]


def _derive_in_flight_branch_ids(operator_plan: Sequence[Mapping[str, Any]]) -> List[str]:
    return [
        _clean_text(entry.get("branch_id"))
        for entry in operator_plan
        if _clean_text(entry.get("status")) == "running"
        and _clean_text(entry.get("branch_id"))
    ]


def _upgrade_operator_plan(
    raw_plan: Any,
    *,
    parent_id: str,
    selected_ids: Sequence[str],
) -> List[Dict[str, Any]]:
    by_operator: Dict[str, Mapping[str, Any]] = {}
    if isinstance(raw_plan, list):
        for raw_entry in raw_plan:
            if not isinstance(raw_entry, Mapping):
                continue
            operator_id = _clean_text(raw_entry.get("operator_id"))
            if operator_id and operator_id not in by_operator:
                by_operator[operator_id] = raw_entry

    upgraded: List[Dict[str, Any]] = []
    for operator_id in selected_ids:
        entry = _operator_entry(parent_id, operator_id)
        raw_entry = by_operator.get(operator_id)
        if raw_entry:
            entry.update(deepcopy(dict(raw_entry)))
            entry["operator_id"] = operator_id
            entry["branch_id"] = make_branch_id(parent_id, operator_id)
            entry["generation_attempt_count"] = _as_nonnegative_int(
                entry.get("generation_attempt_count")
            )
            entry["validation_retry_count"] = _as_nonnegative_int(
                entry.get("validation_retry_count")
            )
            entry["duplicate_retry_count"] = _as_nonnegative_int(
                entry.get("duplicate_retry_count")
            )
            reasons = entry.get("failure_reasons")
            entry["failure_reasons"] = list(reasons) if isinstance(reasons, list) else []
        upgraded.append(entry)
    return upgraded


def upgrade_search_state(
    raw_state: Mapping[str, Any],
    *,
    record: Optional[Mapping[str, Any]] = None,
    branch_window: Optional[int] = None,
    boundary_target: Optional[int] = None,
    assignment_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """Upgrade legacy/unversioned scheduling state without resetting progress."""

    state, _legacy_artifacts = split_legacy_search_state(raw_state)
    raw_version = state.get("search_state_version")
    if raw_version is not None:
        try:
            parsed_version = int(raw_version)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid search_state_version: {raw_version!r}"
            ) from exc
        if parsed_version != SEARCH_STATE_VERSION:
            raise ValueError(
                "search state version mismatch; refuse to mix "
                f"version {parsed_version} and {SEARCH_STATE_VERSION}"
            )
    parent_id = _clean_text(state.get("parent_node_id"))
    if not parent_id and record is not None:
        parent_id = parent_node_id(record)
    if not parent_id:
        raise ValueError("search state is missing parent_node_id")

    route_mode = _route_assignment_mode(record)
    existing_assignment = state.get("assignment_mode")
    if existing_assignment is None:
        # A state created before assignment mode existed is historical.  It
        # remains natural and cannot be silently promoted by a live route.
        if route_mode == ASSIGNMENT_MODE_LIVE:
            raise ValueError(
                "legacy search state has no assignment_mode; start a new live experiment instead of upgrading it"
            )
        resolved_assignment = ASSIGNMENT_MODE_NATURAL
    else:
        resolved_assignment = _assignment_mode(existing_assignment)
    requested_assignment = _assignment_mode(assignment_mode, resolved_assignment) if assignment_mode else resolved_assignment
    if requested_assignment != resolved_assignment:
        raise ValueError("search state assignment_mode mismatch; refuse to mix route revisions")
    if record is not None and route_mode != resolved_assignment:
        raise ValueError("operator route assignment_mode does not match the frozen search state")

    selected = _unique_strings(state.get("selected_operator_ids") or [])
    if not selected:
        raw_plan = state.get("operator_plan")
        if isinstance(raw_plan, list):
            selected = _unique_strings(
                entry.get("operator_id")
                for entry in raw_plan
                if isinstance(entry, Mapping)
            )
    if not selected and record is not None:
        selected = selected_operator_ids(
            record,
            forced_coverage=bool(state.get("forced_coverage")),
        )

    state["search_state_version"] = SEARCH_STATE_VERSION
    state["parent_node_id"] = parent_id
    state["assignment_mode"] = resolved_assignment
    state["selected_operator_ids"] = selected
    state["selected_operator_count"] = len(selected)
    state["operator_plan"] = _upgrade_operator_plan(
        state.get("operator_plan"),
        parent_id=parent_id,
        selected_ids=selected,
    )
    state["boundary_target"] = _as_positive_int(
        boundary_target if boundary_target is not None else state.get("boundary_target"),
        DEFAULT_BOUNDARY_TARGET,
    )
    raw_boundary_count = _as_nonnegative_int(state.get("boundary_candidate_count"))
    state["boundary_candidate_count"] = (
        raw_boundary_count
        if resolved_assignment == ASSIGNMENT_MODE_LIVE
        else min(raw_boundary_count, state["boundary_target"])
    )
    state["branch_window"] = _as_positive_int(
        branch_window if branch_window is not None else state.get("branch_window"),
        DEFAULT_BRANCH_WINDOW,
    )
    state["in_flight_branch_ids"] = _derive_in_flight_branch_ids(state["operator_plan"])
    state["attempted_selected_operator_ids"] = _derive_attempted_operator_ids(
        state["operator_plan"]
    )
    state["decision_completed_count"] = _as_nonnegative_int(
        state.get("decision_completed_count")
    )
    state["experimental_evaluation_pending_count"] = _as_nonnegative_int(
        state.get("experimental_evaluation_pending_count")
    )
    state["scheduler_iteration"] = _as_nonnegative_int(state.get("scheduler_iteration"))
    state["last_progress_at"] = float(state.get("last_progress_at") or 0)
    state["forced_coverage"] = bool(state.get("forced_coverage"))
    state.setdefault("coverage_status", "partial")
    state.setdefault("status", "running")
    state.setdefault("termination_reason", None)
    state.setdefault("branch_summaries", {})
    if not isinstance(state["branch_summaries"], dict):
        state["branch_summaries"] = {}
    state["seen_prompt_hashes"] = _unique_strings(
        state.get("seen_prompt_hashes") or []
    )
    return state


def upgrade_search_state_with_artifacts(
    raw_state: Mapping[str, Any],
    *,
    record: Optional[Mapping[str, Any]] = None,
    branch_window: Optional[int] = None,
    boundary_target: Optional[int] = None,
    assignment_mode: Optional[str] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    lightweight, artifacts = split_legacy_search_state(raw_state)
    return (
        upgrade_search_state(
            lightweight,
            record=record,
            branch_window=branch_window,
            boundary_target=boundary_target,
            assignment_mode=assignment_mode,
        ),
        artifacts,
    )


def initialize_search_state(
    record: Mapping[str, Any],
    *,
    branch_window: int = DEFAULT_BRANCH_WINDOW,
    boundary_target: int = DEFAULT_BOUNDARY_TARGET,
    forced_coverage: bool = False,
    operator_sort_mode: str = "route",
    operator_statistics: Optional[Mapping[str, Any]] = None,
    exploration_ratio: float = 0.1,
    assignment_mode: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Create state once, or resume existing state without reinitialization."""

    existing = record.get("search_state")
    if not isinstance(existing, Mapping):
        existing = record.get("multi_operator_search_state")
    if isinstance(existing, Mapping):
        return upgrade_search_state(
            existing,
            record=record,
            branch_window=branch_window,
            boundary_target=boundary_target,
            assignment_mode=assignment_mode,
        )

    parent_id = parent_node_id(record)
    resolved_assignment = _assignment_mode(
        assignment_mode,
        _route_assignment_mode(record),
    )
    route_assignment = _route_assignment_mode(record)
    if route_assignment != resolved_assignment:
        raise ValueError("operator route assignment_mode does not match search initialization")
    if resolved_assignment == ASSIGNMENT_MODE_LIVE:
        _validate_live_operator_route(record)
    selected = selected_operator_ids(record, forced_coverage=forced_coverage)
    if operator_sort_mode not in {"route", "yield_per_time"}:
        raise ValueError(f"unsupported operator_sort_mode: {operator_sort_mode}")
    if resolved_assignment == ASSIGNMENT_MODE_LIVE and operator_sort_mode != "route":
        raise ValueError("live assignment preserves Router rank and cannot use operator statistics sorting")
    if operator_sort_mode == "yield_per_time":
        route = record.get("operator_route")
        route = route if isinstance(route, Mapping) else {}
        profile = record.get("sample_profile")
        profile = profile if isinstance(profile, Mapping) else {}
        selected = rank_selected_operators(
            selected,
            primary_operator=_clean_text(route.get("primary_operator")),
            backup_operators=route.get("backup_operators") or [],
            statistics=operator_statistics,
            sample_profile=profile,
            exploration_ratio=exploration_ratio,
        )
    registered = _registered_generation_operator_ids()
    state = {
        "search_state_version": SEARCH_STATE_VERSION,
        "parent_node_id": parent_id,
        "parent_record_key": stable_record_key(dict(record)),
        "assignment_mode": resolved_assignment,
        "route_revision": _clean_text(
            (record.get("operator_route") or {}).get("route_revision")
            if isinstance(record.get("operator_route"), Mapping)
            else ""
        )
        or None,
        "status": "running",
        "termination_reason": None,
        "boundary_target": _as_positive_int(boundary_target, DEFAULT_BOUNDARY_TARGET),
        "boundary_candidate_count": 0,
        "selected_operator_ids": selected,
        "selected_operator_count": len(selected),
        "attempted_selected_operator_ids": [],
        "omitted_registered_operator_ids": [
            operator_id for operator_id in registered if operator_id not in selected
        ],
        "coverage_status": "partial",
        "forced_coverage": bool(forced_coverage),
        "operator_sort_mode": operator_sort_mode,
        "operator_exploration_ratio": max(0.0, float(exploration_ratio)),
        "branch_window": _as_positive_int(branch_window, DEFAULT_BRANCH_WINDOW),
        "in_flight_branch_ids": [],
        "decision_completed_count": 0,
        "experimental_evaluation_pending_count": 0,
        "last_progress_at": float(now if now is not None else time.time()),
        "scheduler_iteration": 0,
        "operator_plan": [_operator_entry(parent_id, operator_id) for operator_id in selected],
        "branch_summaries": {},
        "seen_prompt_hashes": [
            hashlib.sha256(
                " ".join(_clean_text(record.get("prompt")).split()).encode("utf-8")
            ).hexdigest()
        ]
        if _clean_text(record.get("prompt"))
        else [],
    }
    if not selected:
        state["status"] = "completed"
        state["coverage_status"] = "complete"
        state["termination_reason"] = (
            "operator_space_exhausted" if forced_coverage else "candidate_list_exhausted"
        )
    return state


def _normalized_prompt_hash(prompt: Any) -> str:
    normalized = " ".join(_clean_text(prompt).split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def _entry_for_branch(state: Mapping[str, Any], branch_id: str) -> Dict[str, Any]:
    for entry in state.get("operator_plan") or []:
        if isinstance(entry, dict) and _clean_text(entry.get("branch_id")) == branch_id:
            return entry
    raise KeyError(f"unknown branch_id: {branch_id}")


def register_generated_prompt(
    state: Mapping[str, Any],
    *,
    branch_id: str,
    prompt: str,
    now: Optional[float] = None,
) -> Tuple[Dict[str, Any], str]:
    """Register one generation attempt before any quality validation.

    Returns ``accepted``, ``retry_duplicate`` or ``duplicate_exhausted``.
    Registration is deterministic when callers submit sibling generations in
    operator-plan order.
    """

    updated = upgrade_search_state(state)
    entry = _entry_for_branch(updated, branch_id)
    if entry.get("status") != "running":
        if entry.get("status") == "duplicate_exhausted":
            return updated, "duplicate_exhausted"
        raise ValueError(f"branch is not running: {branch_id}")

    entry["generation_attempt_count"] = _as_nonnegative_int(
        entry.get("generation_attempt_count")
    ) + 1
    prompt_hash = _normalized_prompt_hash(prompt)
    seen = _unique_strings(updated.get("seen_prompt_hashes") or [])
    if not prompt_hash:
        raise ValueError("generated prompt must not be empty")
    if prompt_hash in seen:
        duplicate_retries = _as_nonnegative_int(entry.get("duplicate_retry_count"))
        if duplicate_retries < 1:
            entry["duplicate_retry_count"] = duplicate_retries + 1
            entry["branch_stage"] = "duplicate_retry_pending"
            action = "retry_duplicate"
        else:
            entry["status"] = "duplicate_exhausted"
            entry["branch_stage"] = "completed"
            entry["completed_at"] = float(now if now is not None else time.time())
            updated["branch_summaries"][branch_id] = {
                "branch_id": branch_id,
                "operator_id": entry["operator_id"],
                "branch_status": "duplicate_exhausted",
                "generation_attempt_count": entry["generation_attempt_count"],
                "duplicate_retry_count": entry["duplicate_retry_count"],
            }
            action = "duplicate_exhausted"
    else:
        seen.append(prompt_hash)
        updated["seen_prompt_hashes"] = seen
        entry["branch_stage"] = "candidate_generated"
        entry["candidate_prompt_sha256"] = prompt_hash
        action = "accepted"

    updated["in_flight_branch_ids"] = _derive_in_flight_branch_ids(
        updated["operator_plan"]
    )
    updated["attempted_selected_operator_ids"] = _derive_attempted_operator_ids(
        updated["operator_plan"]
    )
    updated["last_progress_at"] = float(now if now is not None else time.time())
    return updated, action


def mark_branch_terminal(
    state: Mapping[str, Any],
    *,
    branch_id: str,
    branch_status: str,
    reason: str = "",
    now: Optional[float] = None,
) -> Dict[str, Any]:
    if branch_status not in {
        "duplicate_exhausted",
        "validation_failed",
        "branch_error",
        "not_applicable",
    }:
        raise ValueError(f"unsupported pre-decision terminal status: {branch_status}")
    updated = upgrade_search_state(state)
    entry = _entry_for_branch(updated, branch_id)
    if entry.get("status") in OPERATOR_TERMINAL_STATUSES:
        return updated
    if entry.get("status") != "running":
        raise ValueError(f"branch is not running: {branch_id}")
    entry["status"] = (
        "not_applicable"
        if branch_status == "not_applicable"
        else "duplicate_exhausted"
        if branch_status == "duplicate_exhausted"
        else "completed"
        if branch_status == "validation_failed"
        else "branch_error"
    )
    entry["branch_stage"] = "completed"
    entry["completed_at"] = float(now if now is not None else time.time())
    if reason:
        entry["failure_reasons"] = _unique_strings(
            list(entry.get("failure_reasons") or []) + [reason]
        )
    updated["branch_summaries"][branch_id] = {
        "branch_id": branch_id,
        "operator_id": entry["operator_id"],
        "branch_status": branch_status,
        "failure_reason": reason,
    }
    return _refresh_search_state(updated, now=now)


def _coerce_score_rate(record: Mapping[str, Any]) -> Optional[float]:
    value = record.get("score_rate")
    if value is None:
        scoring_result = record.get("scoring_result")
        if isinstance(scoring_result, Mapping):
            total_awarded = scoring_result.get("total_awarded")
            total_possible = scoring_result.get("total_possible")
            try:
                possible = float(total_possible)
                if possible > 0:
                    value = float(total_awarded) / possible
            except (TypeError, ValueError):
                value = None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _refresh_search_state(
    state: Mapping[str, Any],
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    updated = upgrade_search_state(state)
    updated["in_flight_branch_ids"] = _derive_in_flight_branch_ids(
        updated["operator_plan"]
    )
    updated["attempted_selected_operator_ids"] = _derive_attempted_operator_ids(
        updated["operator_plan"]
    )
    pending = any(entry.get("status") == "pending" for entry in updated["operator_plan"])
    live_assignment = updated.get("assignment_mode") == ASSIGNMENT_MODE_LIVE
    if not live_assignment and updated["boundary_candidate_count"] >= updated["boundary_target"]:
        updated["termination_reason"] = "boundary_target_reached"
        updated["coverage_status"] = (
            "complete"
            if not pending and not updated["in_flight_branch_ids"]
            else "partial"
        )
        updated["status"] = (
            "completed" if not updated["in_flight_branch_ids"] else "running"
        )
    elif not pending and not updated["in_flight_branch_ids"]:
        updated["status"] = "completed"
        updated["coverage_status"] = "complete"
        updated["termination_reason"] = (
            "operator_space_exhausted"
            if updated.get("forced_coverage")
            else "candidate_list_exhausted"
        )
    updated["last_progress_at"] = float(now if now is not None else time.time())
    return updated


def merge_decision_result(
    state: Mapping[str, Any],
    decision_record: Mapping[str, Any],
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Merge one Qwen-complete branch without waiting for experimental GPT."""

    updated = upgrade_search_state(state)
    if _is_search_terminal(updated):
        return updated
    branch_id = _clean_text(
        decision_record.get("branch_id") or decision_record.get("candidate_id")
    )
    entry = _entry_for_branch(updated, branch_id)
    existing_summary = updated["branch_summaries"].get(branch_id)
    if isinstance(existing_summary, Mapping) and _clean_text(
        existing_summary.get("branch_status")
    ) in BRANCH_TERMINAL_STATUSES:
        return updated
    if entry.get("status") != "running":
        raise ValueError(f"branch is not running: {branch_id}")

    parent_rate = decision_record.get("parent_score_rate")
    try:
        parent_rate = float(parent_rate)
    except (TypeError, ValueError):
        parent_rate = None
    child_rate = _coerce_score_rate(decision_record)
    if parent_rate is None or child_rate is None:
        return mark_branch_terminal(
            updated,
            branch_id=branch_id,
            branch_status="branch_error",
            reason="missing parent or child decision score",
            now=now,
        )

    if child_rate < parent_rate:
        branch_status = "boundary_candidate"
    elif child_rate > parent_rate:
        branch_status = "score_increased"
    else:
        branch_status = "no_score_change"
    if branch_status == "boundary_candidate":
        if (
            updated.get("assignment_mode") != ASSIGNMENT_MODE_LIVE
            and updated["boundary_candidate_count"] >= updated["boundary_target"]
        ):
            raise ValueError("boundary target overflow")
        updated["boundary_candidate_count"] += 1

    entry["status"] = "completed"
    entry["branch_stage"] = "decision_completed"
    entry["completed_at"] = float(now if now is not None else time.time())
    experimental_status = _clean_text(
        decision_record.get("experimental_evaluation_status")
    ) or "pending"
    updated["decision_completed_count"] += 1
    if experimental_status == "pending":
        updated["experimental_evaluation_pending_count"] += 1
    updated["branch_summaries"][branch_id] = {
        "branch_id": branch_id,
        "parent_node_id": updated["parent_node_id"],
        "operator_id": entry["operator_id"],
        "parent_score_rate": parent_rate,
        "child_score_rate": child_rate,
        "delta_score_rate": child_rate - parent_rate,
        "branch_status": branch_status,
        "review_status": "pending" if branch_status == "boundary_candidate" else None,
        "decision_evaluation_status": "completed",
        "experimental_evaluation_status": experimental_status,
    }
    return _refresh_search_state(updated, now=now)


def mark_experimental_evaluation_completed(
    state: Mapping[str, Any],
    branch_id: str,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    return mark_experimental_evaluation_finished(
        state,
        branch_id,
        status="completed",
        now=now,
    )


def mark_experimental_evaluation_finished(
    state: Mapping[str, Any],
    branch_id: str,
    *,
    status: str,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    if status not in {"completed", "failed"}:
        raise ValueError(f"unsupported experimental evaluation status: {status}")
    updated = upgrade_search_state(state)
    summary = updated["branch_summaries"].get(branch_id)
    if not isinstance(summary, dict):
        raise KeyError(f"missing branch summary: {branch_id}")
    if summary.get("experimental_evaluation_status") in {"completed", "failed"}:
        return updated
    summary["experimental_evaluation_status"] = status
    updated["experimental_evaluation_pending_count"] = max(
        0,
        updated["experimental_evaluation_pending_count"] - 1,
    )
    updated["last_progress_at"] = float(now if now is not None else time.time())
    return updated


def build_dispatch_records(
    parent_record: Mapping[str, Any],
    state: Mapping[str, Any],
    *,
    now: Optional[float] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Claim a window and build stable, operator-forced branch inputs."""

    updated, claimed = claim_branches(state, now=now)
    records: List[Dict[str, Any]] = []
    parent_rate = _coerce_score_rate(parent_record)
    plan_sequence = {
        _clean_text(entry.get("branch_id")): index
        for index, entry in enumerate(updated.get("operator_plan") or [], start=1)
    }
    for claim in claimed:
        branch_record = deepcopy(dict(parent_record))
        branch_record.pop("search_state", None)
        branch_record.pop("multi_operator_search_state", None)
        branch_record["parent_node_id"] = updated["parent_node_id"]
        branch_record["branch_id"] = claim["branch_id"]
        branch_record["candidate_group_id"] = updated["parent_node_id"]
        branch_record["candidate_id"] = claim["branch_id"]
        branch_record["candidate_operator"] = claim["operator_id"]
        branch_record["parent_score_rate"] = parent_rate
        route = branch_record.get("operator_route")
        route = deepcopy(route) if isinstance(route, dict) else {}
        route["selected_operator_ids"] = list(updated["selected_operator_ids"])
        route["primary_operator"] = claim["operator_id"]
        route["backup_operators"] = []
        route["avoid_operators"] = []
        branch_record["operator_route"] = route
        branch_record["search_dispatch"] = {
            "branch_window": updated["branch_window"],
            "scheduler_iteration": updated["scheduler_iteration"],
            "generation_sequence": plan_sequence[claim["branch_id"]],
            "sibling_generation_serial": True,
            "resume_from_stage": claim["resume_from_stage"],
        }
        records.append(branch_record)
    return updated, records


def claim_branches(
    state: Mapping[str, Any],
    *,
    now: Optional[float] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Atomically model a bounded branch claim on one state value."""

    updated = upgrade_search_state(state)
    if _is_search_terminal(updated):
        return updated, []

    in_flight = _derive_in_flight_branch_ids(updated["operator_plan"])
    remaining_boundary_slots = (
        len(updated["operator_plan"])
        if updated.get("assignment_mode") == ASSIGNMENT_MODE_LIVE
        else max(0, updated["boundary_target"] - updated["boundary_candidate_count"])
    )
    available_in_flight_slots = max(0, updated["branch_window"] - len(in_flight))
    pending_entries = [
        entry for entry in updated["operator_plan"] if entry.get("status") == "pending"
    ]
    dispatch_count = min(
        remaining_boundary_slots,
        available_in_flight_slots,
        len(pending_entries),
    )

    claimed: List[Dict[str, Any]] = []
    claimed_at = float(now if now is not None else time.time())
    for entry in pending_entries[:dispatch_count]:
        entry["status"] = "running"
        entry["branch_stage"] = "claimed"
        entry["claimed_at"] = claimed_at
        claimed.append(
            {
                "branch_id": entry["branch_id"],
                "parent_node_id": updated["parent_node_id"],
                "operator_id": entry["operator_id"],
                "resume_from_stage": "generation",
            }
        )

    updated["in_flight_branch_ids"] = _derive_in_flight_branch_ids(
        updated["operator_plan"]
    )
    updated["attempted_selected_operator_ids"] = _derive_attempted_operator_ids(
        updated["operator_plan"]
    )
    if claimed:
        updated["scheduler_iteration"] += 1
        updated["last_progress_at"] = claimed_at
    elif not pending_entries and not updated["in_flight_branch_ids"]:
        updated["status"] = "completed"
        updated["coverage_status"] = "complete"
        updated["termination_reason"] = (
            "operator_space_exhausted"
            if updated.get("forced_coverage")
            else "candidate_list_exhausted"
        )
    return updated, claimed


def recover_in_flight_branches(
    state: Mapping[str, Any],
    stage_checkpoints: Mapping[str, str],
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Recover running branches from their last confirmed stage.

    A branch with no confirmed output is safe to return to ``pending``.  Any
    confirmed stage keeps the branch running and resumes strictly after that
    stage, so a generated candidate is never regenerated merely because the
    coordinator process stopped.
    """

    updated = upgrade_search_state(state)
    if _is_search_terminal(updated):
        return updated
    progress_at = float(now if now is not None else time.time())
    changed = False
    for entry in updated["operator_plan"]:
        if entry.get("status") != "running":
            continue
        branch_id = _clean_text(entry.get("branch_id"))
        confirmed_stage = _clean_text(stage_checkpoints.get(branch_id))
        if not confirmed_stage:
            entry["status"] = "pending"
            entry["branch_stage"] = "pending"
            entry.pop("claimed_at", None)
        else:
            entry["branch_stage"] = confirmed_stage
            entry["resume_from_stage"] = confirmed_stage
        changed = True
    updated["in_flight_branch_ids"] = _derive_in_flight_branch_ids(
        updated["operator_plan"]
    )
    updated["attempted_selected_operator_ids"] = _derive_attempted_operator_ids(
        updated["operator_plan"]
    )
    if changed:
        updated["last_progress_at"] = progress_at
    return updated


def attach_search_state(record: Mapping[str, Any], state: Mapping[str, Any]) -> Dict[str, Any]:
    result = deepcopy(dict(record))
    # Canonicalize the legacy alias so full embedded legacy branch collections
    # cannot remain attached beside the new lightweight state.
    result.pop("multi_operator_search_state", None)
    result["search_state"] = deepcopy(dict(state))
    return result


def _write_jsonl_atomic(records: Sequence[Mapping[str, Any]], output_path: str) -> None:
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(destination.parent),
        suffix=".tmp",
    ) as target:
        temporary = target.name
        for record in records:
            target.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, destination)


def initialize_records(
    records: Sequence[Mapping[str, Any]],
    *,
    branch_window: int,
    boundary_target: int,
    forced_coverage: bool,
    operator_sort_mode: str = "route",
    operator_statistics: Optional[Mapping[str, Any]] = None,
    exploration_ratio: float = 0.1,
    assignment_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    return [
        attach_search_state(
            record,
            initialize_search_state(
                record,
                branch_window=branch_window,
                boundary_target=boundary_target,
                forced_coverage=forced_coverage,
                operator_sort_mode=operator_sort_mode,
                operator_statistics=operator_statistics,
                exploration_ratio=exploration_ratio,
                assignment_mode=assignment_mode,
            ),
        )
        for record in records
    ]


def dispatch_records(
    records: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    state_records: List[Dict[str, Any]] = []
    branch_records: List[Dict[str, Any]] = []
    for record in records:
        raw_state = record.get("search_state")
        if not isinstance(raw_state, Mapping):
            raise ValueError(
                f"record {stable_record_key(dict(record))} is missing search_state"
            )
        updated, dispatched = build_dispatch_records(record, raw_state)
        state_records.append(attach_search_state(record, updated))
        branch_records.extend(dispatched)
    return state_records, branch_records


def merge_decision_records(
    state_records: Sequence[Mapping[str, Any]],
    decision_records: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    decisions_by_parent: Dict[str, List[Mapping[str, Any]]] = {}
    for decision in decision_records:
        parent_id = _clean_text(decision.get("parent_node_id"))
        if not parent_id:
            branch_id = _clean_text(
                decision.get("branch_id") or decision.get("candidate_id")
            )
            parent_id = branch_id.rsplit("::", 1)[0] if "::" in branch_id else ""
        decisions_by_parent.setdefault(parent_id, []).append(decision)

    merged: List[Dict[str, Any]] = []
    for record in state_records:
        raw_state = record.get("search_state")
        if not isinstance(raw_state, Mapping):
            raise ValueError(
                f"record {stable_record_key(dict(record))} is missing search_state"
            )
        state = upgrade_search_state(raw_state, record=record)
        for decision in decisions_by_parent.get(state["parent_node_id"], []):
            generation = decision.get("candidate_generation")
            generation = generation if isinstance(generation, Mapping) else {}
            generation_status = _clean_text(generation.get("generation_status"))
            validation = decision.get("validation_result")
            validation = validation if isinstance(validation, Mapping) else {}
            branch_id = _clean_text(
                decision.get("branch_id") or decision.get("candidate_id")
            )
            if generation_status == "not_applicable":
                state = mark_branch_terminal(
                    state,
                    branch_id=branch_id,
                    branch_status="not_applicable",
                    reason=_clean_text(generation.get("not_applicable_reason")),
                )
            elif validation.get("passed") is False:
                state = mark_branch_terminal(
                    state,
                    branch_id=branch_id,
                    branch_status="validation_failed",
                    reason=_clean_text(validation.get("reject_reason")),
                )
            else:
                state = merge_decision_result(state, decision)
        merged.append(attach_search_state(record, state))
    return merged


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Manage lightweight multi-operator search state.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--input", required=True)
    initialize.add_argument("--output", required=True)
    initialize.add_argument("--branch-window", type=int, default=DEFAULT_BRANCH_WINDOW)
    initialize.add_argument("--boundary-target", type=int, default=DEFAULT_BOUNDARY_TARGET)
    initialize.add_argument("--forced-coverage", action="store_true")
    initialize.add_argument(
        "--operator-sort-mode",
        choices=["route", "yield_per_time"],
        default="route",
    )
    initialize.add_argument("--operator-statistics", default=None)
    initialize.add_argument("--exploration-ratio", type=float, default=0.1)
    initialize.add_argument(
        "--assignment-mode",
        choices=[ASSIGNMENT_MODE_NATURAL, ASSIGNMENT_MODE_LIVE],
        default=ASSIGNMENT_MODE_NATURAL,
    )
    dispatch = subparsers.add_parser("dispatch")
    dispatch.add_argument("--input", required=True)
    dispatch.add_argument("--state-output", required=True)
    dispatch.add_argument("--branches-output", required=True)
    merge = subparsers.add_parser("merge")
    merge.add_argument("--state-input", required=True)
    merge.add_argument("--decision-input", required=True)
    merge.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "initialize":
        if args.branch_window < 1:
            raise ValueError("--branch-window must be >= 1")
        if args.boundary_target < 1:
            raise ValueError("--boundary-target must be >= 1")
        records = load_json_records(args.input, stage="search_initialize")
        operator_statistics = None
        if args.operator_statistics:
            with open(args.operator_statistics, "r", encoding="utf-8") as source:
                operator_statistics = json.load(source)
        initialized = initialize_records(
            records,
            branch_window=args.branch_window,
            boundary_target=args.boundary_target,
            forced_coverage=args.forced_coverage,
            operator_sort_mode=args.operator_sort_mode,
            operator_statistics=operator_statistics,
            exploration_ratio=args.exploration_ratio,
            assignment_mode=args.assignment_mode,
        )
        _write_jsonl_atomic(initialized, args.output)
    elif args.command == "dispatch":
        records = load_json_records(args.input, stage="search_dispatch")
        states, branches = dispatch_records(records)
        _write_jsonl_atomic(states, args.state_output)
        _write_jsonl_atomic(branches, args.branches_output)
    elif args.command == "merge":
        states = load_json_records(args.state_input, stage="search_merge_state")
        decisions = load_json_records(
            args.decision_input,
            stage="search_merge_decision",
        )
        merged = merge_decision_records(states, decisions)
        _write_jsonl_atomic(merged, args.output)


if __name__ == "__main__":
    main()
