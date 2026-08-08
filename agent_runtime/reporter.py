"""Safe M0 and read-only review reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


PROPOSAL_STATUSES = {"proposed", "needs_human_review", "rejected_insufficient_evidence"}


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def write_agent_report(
    run_dir: str | Path,
    *,
    task: Mapping[str, Any],
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    observation: Optional[Mapping[str, Any]],
    tool_results: Iterable[Mapping[str, Any]],
    decision: Optional[Mapping[str, Any]] = None,
) -> Path:
    run_path = Path(run_dir)
    observed = dict(observation or {})
    lines = [
        "# Question Evolution Agent Report",
        "",
        f"- Goal: {task.get('goal', '')}",
        f"- Agent run directory: {run_path}",
        f"- Input file: {task.get('input_file') or 'not applicable'}",
        f"- Resume experiment directory: {task.get('resume_exp_dir') or 'not applicable'}",
        f"- Actual experiment directory: {state.get('experiment_dir') or observed.get('experiment_dir') or 'not located'}",
        f"- Session status: {state.get('status', 'not available')}",
        f"- Plan revision: {state.get('plan_revision', 0)}",
        f"- Current plan: {state.get('current_plan_path') or 'not available'}",
        f"- Terminal reason: {state.get('terminal_reason') or 'not applicable'}",
        f"- Manual review: {'pending' if state.get('requires_manual_review') else (state.get('manual_review_status') or 'not required')}",
        f"- Search mode: {plan.get('selected_search_mode')}",
        f"- Execution scope: {plan.get('selected_execution_scope')}",
        f"- Budget: {json.dumps(plan.get('budget', {}), ensure_ascii=False)}",
        "",
        "## Tool results",
    ]
    total_duration = 0.0
    for result in tool_results:
        duration = float(result.get("duration_seconds") or 0)
        total_duration += duration
        lines.append(f"- {result.get('tool')}: {'completed' if result.get('ok') else 'failed'} (return code {result.get('return_code')}, {duration:.3f}s, retries {result.get('retry_count', 0)})")
    lines.append(f"- Recorded tool duration: {total_duration:.3f}s")
    lines.extend([
        "",
        "## Observation",
        f"- Boundary candidates from automatic evidence: {observed.get('boundary_candidate_count', 0)}",
        f"- score_increased: {observed.get('score_increased_count', 0)}",
        f"- not_applicable: {observed.get('not_applicable_count', 0)}",
        f"- validation_failed: {observed.get('validation_failed_count', 0)}",
        f"- branch_error: {observed.get('branch_error_count', 0)}",
        f"- Target reached: {observed.get('target_reached', False)}",
        f"- Main issue: {observed.get('main_issue', 'not available')}",
        f"- Missing artifacts: {', '.join(observed.get('missing_artifacts', [])) or 'none'}",
        f"- Normalized observations: {', '.join(str(item.get('type')) for item in observed.get('observations', []) if isinstance(item, Mapping)) or 'none'}",
        "",
        "## Decision and next step",
        f"- Decision: {(decision or {}).get('action', 'not decided')}",
        f"- Reason: {(decision or {}).get('reason', 'not available')}",
        "- Automatic scores are evidence only; any boundary candidate requires human confirmation before a policy or Memory change.",
    ])
    return _write(run_path / "agent_report.md", "\n".join(lines) + "\n")


def write_global_review_artifacts(run_dir: str | Path, observation: Mapping[str, Any]) -> Dict[str, Path]:
    root = Path(run_dir)
    evidence = list(observation.get("evidence_refs", []))[:10]
    issue = str(observation.get("main_issue") or "insufficient experiment evidence")
    status = "needs_human_review" if evidence else "rejected_insufficient_evidence"
    proposal = {
        "proposal_id": "proposal_001",
        "status": status,
        "topic": issue,
        "evidence_refs": evidence,
        "recommendation": "Review the automatic evidence in a Shadow/Replay workflow; do not modify prompts, Router, scoring, state, or active Memory automatically.",
    }
    assert proposal["status"] in PROPOSAL_STATUSES
    proposal_path = root / "optimization_proposals.jsonl"
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    review = "\n".join([
        "# Read-only Global Review",
        "",
        f"- Main observed issue: {issue}",
        f"- score_increased branches: {observation.get('score_increased_count', 0)}",
        f"- not_applicable branches: {observation.get('not_applicable_count', 0)}",
        f"- Evidence references: {len(evidence)}",
        "- This is a proposal-only review. It does not change prompts, Router, Rubric, scores, state, operators, or Memory.",
    ]) + "\n"
    return {"global_review_report": _write(root / "global_review_report.md", review), "optimization_proposals": proposal_path}
