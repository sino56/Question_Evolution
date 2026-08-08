"""Deterministic stop/block decisions for Agent v1."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from .events import append_event
from .policy import validate_decision
from .task import AgentTask


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def decide_next_action(
    task: AgentTask,
    observation: Mapping[str, Any],
    *,
    tool_results: Iterable[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    failures = [result for result in tool_results if not result.get("ok", False) and not result.get("recoverable", False)]
    if failures:
        decision = {"action": "blocked", "reason": "a registered tool failed without a recovery path", "requires_human_review": True}
    elif observation.get("status") == "blocked" or observation.get("manifest_status") == "damaged":
        decision = {"action": "blocked", "reason": str(observation.get("blocked_reason") or "experiment artifact is damaged"), "requires_human_review": True}
    elif task.review_mode == "report_only":
        decision = {"action": "stop_and_report", "reason": "read-only review completed", "requires_human_review": bool(observation.get("evidence_refs"))}
    elif bool(observation.get("target_reached")):
        decision = {"action": "stop_and_report", "reason": "automatic boundary-candidate target reached", "requires_human_review": True}
    elif int(observation.get("pending_count") or 0) == 0:
        decision = {"action": "stop_and_report", "reason": "no pending branches remain", "requires_human_review": bool(observation.get("score_increased_count"))}
    elif int(observation.get("final_records_count") or 0) > 0:
        decision = {"action": "stop_and_report", "reason": "registered loop completed and produced final records", "requires_human_review": True}
    else:
        decision = {
            "action": "stop_and_report",
            "reason": "Agent v1 does not automatically launch another experiment; review the remaining pending work before submitting a new task",
            "requires_human_review": True,
        }
    decision.update({"created_at": _now(), "observation_status": observation.get("status")})
    validate_decision(decision)
    return decision


def write_decision(run_dir: str | Path, decision: Mapping[str, Any]) -> Dict[str, Any]:
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "agent_decisions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(decision), ensure_ascii=False, sort_keys=True) + "\n")
    append_event(root / "agent_events.jsonl", "decision", dict(decision))
    return dict(decision)
