"""Persistent, append-only-compatible Session manifests for Agent runs."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .events import append_event


SESSION_STATUSES = {
    "created",
    "context_ready",
    "planned",
    "executing",
    "observing",
    "replanning",
    "suspended",
    "completed",
    "stopped",
    "blocked",
    "failed",
}


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def create_run_dir(project_root: Path, *, run_id: Optional[str] = None) -> Path:
    identifier = run_id or f"agent_{uuid.uuid4().hex[:16]}"
    day = datetime.now().strftime("%Y-%m-%d")
    run_dir = project_root / "agent_runs" / day / identifier
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def _default_manifest(run_dir: Path, *, run_id: str, mode: str, root_goal: str = "", budgets: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """Return the Stage-2 fields while retaining every Stage-1 state field."""

    return {
        # Stage-1 compatibility fields.
        "agent_run_id": run_id,
        "command": mode,
        "experiment_dir": None,
        "current_step_id": None,
        "completed_step_ids": [],
        "blocked_reason": None,
        # Stage-2 Session Manifest fields.
        "session_id": run_id,
        "root_goal": root_goal,
        "status": "created",
        "plan_revision": 0,
        "current_plan_path": None,
        "latest_observation_path": str((run_dir / "agent_observation.json").resolve()),
        "event_log_path": str((run_dir / "agent_events.jsonl").resolve()),
        "agent_run_dir": str(run_dir.resolve()),
        "budgets": dict(budgets or {}),
        "budget_ledger_path": str((run_dir / "budget_ledger.json").resolve()),
        "terminal_reason": None,
        "memory_snapshot_id": None,
        "memory_snapshot_path": None,
        "memory_context_key": None,
        "context_cache_key": None,
        "memory_mode": "no_global_memory",
        "requires_manual_review": False,
        "manual_review_status": None,
        "resume_checkpoint": {
            "resume_exp_dir": None,
            "resume_start_round": None,
            "last_completed_step_id": None,
        },
    }


def _hydrate_manifest(run_dir: Path, value: Mapping[str, Any]) -> Dict[str, Any]:
    """Read legacy Agent state as a Session Manifest without migration."""

    run_id = str(value.get("agent_run_id") or value.get("session_id") or run_dir.name)
    base = _default_manifest(run_dir, run_id=run_id, mode=str(value.get("command") or "run"))
    base.update(dict(value))
    base["session_id"] = str(base.get("session_id") or run_id)
    base["agent_run_id"] = str(base.get("agent_run_id") or base["session_id"])
    base["completed_step_ids"] = list(base.get("completed_step_ids") or [])
    base["budgets"] = dict(base.get("budgets") or {})
    base["resume_checkpoint"] = {
        **_default_manifest(run_dir, run_id=run_id, mode=str(base.get("command") or "run"))["resume_checkpoint"],
        **dict(base.get("resume_checkpoint") or {}),
    }
    base["event_log_path"] = str(base.get("event_log_path") or (run_dir / "agent_events.jsonl").resolve())
    base["latest_observation_path"] = str(base.get("latest_observation_path") or (run_dir / "agent_observation.json").resolve())
    base["agent_run_dir"] = str(base.get("agent_run_dir") or run_dir.resolve())
    return base


def initialize_state(
    run_dir: Path,
    *,
    run_id: str,
    mode: str,
    root_goal: str = "",
    budgets: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    state = _default_manifest(run_dir, run_id=run_id, mode=mode, root_goal=root_goal, budgets=budgets)
    save_state(run_dir, state)
    append_event(run_dir / "agent_events.jsonl", "session_created", {"session_id": run_id, "status": "created", "command": mode})
    return state


def load_state(run_dir: Path) -> Dict[str, Any]:
    legacy_path = run_dir / "agent_run_state.json"
    manifest_path = run_dir / "session_manifest.json"
    path = legacy_path if legacy_path.exists() else manifest_path
    return _hydrate_manifest(run_dir, json.loads(path.read_text(encoding="utf-8")))


def save_state(run_dir: Path, state: Mapping[str, Any]) -> None:
    manifest = _hydrate_manifest(run_dir, state)
    # Keep the original filename as the stable Stage-1 public contract and
    # expose the explicit Session name for new consumers.
    _write_json(run_dir / "agent_run_state.json", manifest)
    _write_json(run_dir / "session_manifest.json", manifest)


def update_state(run_dir: Path, state: Dict[str, Any], **changes: Any) -> Dict[str, Any]:
    previous_status = state.get("status")
    requested_status = changes.get("status", previous_status)
    if requested_status not in SESSION_STATUSES:
        raise ValueError(f"unsupported Session status: {requested_status}")
    state.update(changes)
    if state.get("current_step_id") is None and state.get("completed_step_ids"):
        checkpoint = dict(state.get("resume_checkpoint") or {})
        checkpoint["last_completed_step_id"] = state["completed_step_ids"][-1]
        state["resume_checkpoint"] = checkpoint
    save_state(run_dir, state)
    if requested_status != previous_status:
        append_event(
            run_dir / "agent_events.jsonl",
            "session_status_changed",
            {"session_id": state.get("session_id"), "from_status": previous_status, "to_status": requested_status,
             "current_step_id": state.get("current_step_id"), "terminal_reason": state.get("terminal_reason")},
        )
    return state


def write_task(run_dir: Path, task: Mapping[str, Any]) -> None:
    _write_json(run_dir / "agent_task.json", task)


def write_plan(run_dir: Path, plan: Mapping[str, Any]) -> None:
    """Write the Stage-1 compatibility copy of the effective plan."""

    _write_json(run_dir / "agent_plan.json", plan)


def write_plan_revision(
    run_dir: Path,
    state: Dict[str, Any],
    plan: Mapping[str, Any],
    *,
    trigger_reason: str = "initial_plan",
) -> Dict[str, Any]:
    """Persist an immutable plan revision and make it the effective plan."""

    revision = int(state.get("plan_revision") or 0) + 1
    previous_path = state.get("current_plan_path")
    revised = dict(plan)
    revised.update({
        "plan_revision": revision,
        "replan_context": {
            "trigger_reason": trigger_reason,
            "replaces_plan_path": previous_path,
        },
    })
    target = run_dir / "plans" / f"plan_r{revision:03d}.json"
    _write_json(target, revised)
    write_plan(run_dir, revised)
    update_state(
        run_dir,
        state,
        plan_revision=revision,
        current_plan_path=str(target.resolve()),
    )
    append_event(
        run_dir / "agent_events.jsonl",
        "plan_revision_created",
        {"session_id": state.get("session_id"), "plan_revision": revision, "plan_path": str(target.resolve()),
         "trigger_reason": trigger_reason, "replaces_plan_path": previous_path},
    )
    return revised


def write_context(run_dir: Path, context: Mapping[str, Any]) -> None:
    _write_json(run_dir / "agent_context.json", context)
