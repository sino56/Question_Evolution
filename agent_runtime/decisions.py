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
    normalized = list(observation.get("observations") or [])
    observation_types = {str(item.get("type")) for item in normalized if isinstance(item, Mapping)}
    failures = [result for result in tool_results if not result.get("ok", False) and not result.get("recoverable", False)]
    retryable_failures = [result for result in tool_results if not result.get("ok", False) and result.get("recoverable", False)]
    if "manifest_corrupted" in observation_types:
        decision = {"action": "blocked", "reason": "published artifact manifest is corrupted", "requires_human_review": True,
                    "terminal_reason": "manifest_corrupted"}
    elif "tool_fatal_failure" in observation_types or failures:
        decision = {"action": "blocked", "reason": "a registered tool failed without a recovery path", "requires_human_review": True,
                    "terminal_reason": "unrecoverable_tool_failure"}
    elif "tool_retryable_failure" in observation_types or retryable_failures:
        decision = {"action": "suspend", "reason": "a registered tool encountered a retryable system failure after its retry policy", "requires_human_review": False,
                    "terminal_reason": "retryable_tool_failure"}
    elif observation.get("status") == "blocked" or observation.get("manifest_status") == "damaged":
        decision = {"action": "blocked", "reason": str(observation.get("blocked_reason") or "experiment artifact is damaged"), "requires_human_review": True,
                    "terminal_reason": "artifact_blocked"}
    elif task.review_mode == "report_only":
        needs_review = bool(observation.get("evidence_refs"))
        decision = {"action": "stop_and_report", "reason": "read-only review completed", "requires_human_review": needs_review,
                    "terminal_reason": "manual_review_required" if needs_review else "review_completed"}
    elif "score_increased" in observation_types or int(observation.get("score_increased_count") or 0) > 0:
        decision = {"action": "stop_and_report", "reason": "score_increased is negative gain and requires human review", "requires_human_review": True,
                    "terminal_reason": "manual_review_required"}
    elif "not_applicable" in observation_types or int(observation.get("not_applicable_count") or 0) > 0:
        decision = {"action": "stop_and_report", "reason": "operator applicability issue observed; do not penalize the whole operator family", "requires_human_review": True,
                    "terminal_reason": "manual_review_required"}
    elif observation.get("replan_required") or any(bool(item.get("requires_replan")) for item in normalized if isinstance(item, Mapping)):
        decision = {"action": "replan", "reason": str(observation.get("replan_reason") or "observation requires a constrained replan"),
                    "requires_human_review": False, "terminal_reason": None}
    elif _is_budget_exhausted(observation):
        reason = str(observation.get("termination_reason") or "budget_exhausted")
        decision = {"action": "stop_and_report", "reason": reason, "requires_human_review": False,
                    "terminal_reason": reason}
    elif bool(observation.get("target_reached")):
        decision = {"action": "stop_and_report", "reason": "automatic boundary-candidate target reached", "requires_human_review": True,
                    "terminal_reason": "manual_review_required"}
    elif int(observation.get("pending_count") or 0) == 0:
        needs_review = bool(observation.get("score_increased_count") or observation.get("boundary_candidate_count"))
        decision = {"action": "stop_and_report", "reason": "no pending branches remain", "requires_human_review": needs_review,
                    "terminal_reason": "manual_review_required" if needs_review else "no_pending_branches"}
    elif int(observation.get("final_records_count") or 0) > 0:
        decision = {"action": "stop_and_report", "reason": "registered loop completed and produced final records", "requires_human_review": True,
                    "terminal_reason": "manual_review_required"}
    else:
        decision = {
            "action": "stop_and_report",
            "reason": "Agent v1 does not automatically launch another experiment; review the remaining pending work before submitting a new task",
            "requires_human_review": True,
            "terminal_reason": "manual_review_required",
        }
    decision.update({"created_at": _now(), "observation_status": observation.get("status")})
    validate_decision(decision)
    return decision


def _is_budget_exhausted(observation: Mapping[str, Any]) -> bool:
    reason = str(observation.get("termination_reason") or "").lower()
    return bool(observation.get("budget_exhausted")) or "budget" in reason or "max_search_steps" in reason


def write_decision(run_dir: str | Path, decision: Mapping[str, Any]) -> Dict[str, Any]:
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "agent_decisions.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(decision), ensure_ascii=False, sort_keys=True) + "\n")
    append_event(root / "agent_events.jsonl", "decision", dict(decision))
    return dict(decision)
