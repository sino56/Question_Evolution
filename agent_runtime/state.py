"""Persistent M0 Agent-run state."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Mapping, Optional


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(dict(value), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def create_run_dir(project_root: Path, *, run_id: Optional[str] = None) -> Path:
    identifier = run_id or f"agent_{uuid.uuid4().hex[:16]}"
    day = datetime.now().strftime("%Y-%m-%d")
    run_dir = project_root / "agent_runs" / day / identifier
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_dir


def initialize_state(run_dir: Path, *, run_id: str, mode: str) -> Dict[str, Any]:
    state = {
        "agent_run_id": run_id,
        "status": "created",
        "command": mode,
        "experiment_dir": None,
        "current_step_id": None,
        "completed_step_ids": [],
        "blocked_reason": None,
    }
    save_state(run_dir, state)
    return state


def load_state(run_dir: Path) -> Dict[str, Any]:
    path = run_dir / "agent_run_state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(run_dir: Path, state: Mapping[str, Any]) -> None:
    _write_json(run_dir / "agent_run_state.json", state)


def update_state(run_dir: Path, state: Dict[str, Any], **changes: Any) -> Dict[str, Any]:
    state.update(changes)
    save_state(run_dir, state)
    return state


def write_task(run_dir: Path, task: Mapping[str, Any]) -> None:
    _write_json(run_dir / "agent_task.json", task)


def write_plan(run_dir: Path, plan: Mapping[str, Any]) -> None:
    _write_json(run_dir / "agent_plan.json", plan)


def write_context(run_dir: Path, context: Mapping[str, Any]) -> None:
    _write_json(run_dir / "agent_context.json", context)
