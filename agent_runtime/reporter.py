"""Safe M0 and read-only review reports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional

from .events import redact
from .skills import load_stage_skills

PROPOSAL_STATUSES = {"proposed", "needs_human_review", "rejected_insufficient_evidence"}


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _proposal_id(payload: Mapping[str, Any]) -> str:
    """Derive the proposal id from its content instead of hard-coding it.

    ``proposal_001`` was a constant, so two different proposals carried the same
    identity and a reader could not tell them apart (report X-5).
    """

    digest = hashlib.sha256(json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return "proposal_" + digest[:16]


def _render_final_output_contract(lines: list[str], observed: Mapping[str, Any]) -> None:
    """Render the design §20 output contract from the published observation."""

    contract = observed.get("final_output_contract") if isinstance(observed.get("final_output_contract"), Mapping) else {}
    lines.extend(["", "## Final output contract", ""])
    if not contract.get("available"):
        lines.append(f"- Not available: {contract.get('reason') or 'the run has not published scored final records'}")
        return
    lines.extend([
        f"- Best sample: {contract.get('best_sample_id') or 'not available'}",
        f"- Score before: {contract.get('score_before')}",
        f"- Score after: {contract.get('score_after')}",
        f"- Score delta: {contract.get('score_delta')} ({contract.get('direction')})",
        f"- Operator path: {', '.join(contract.get('operator_path') or []) or 'not available'}",
        f"- Judge stability: {json.dumps(contract.get('judge_stability') or {}, ensure_ascii=False, sort_keys=True)}",
        f"- Cost summary: {json.dumps(contract.get('cost_summary') or {}, ensure_ascii=False, sort_keys=True)}",
        f"- Scored records considered: {contract.get('evaluated_records', 0)}",
        f"- {contract.get('note', '')}",
    ])
    question = str(contract.get("best_question") or "")
    if question:
        lines.extend(["", "### Best question (bounded excerpt)", "", "```text", question, "```"])


def _render_independence(lines: list[str], review: Mapping[str, Any]) -> None:
    """Render the realised advisor independence (V-8)."""

    independence = review.get("independence") if isinstance(review.get("independence"), Mapping) else {}
    if not independence:
        return
    lines.extend([
        "",
        "## Advisor independence",
        f"- Interpretation: {independence.get('interpretation')}",
        f"- Advisor runs: {independence.get('advisor_runs')} (completed {independence.get('completed_advisors')})",
        f"- Model-backed: {independence.get('model_backed_advisors')} / deterministic: {independence.get('deterministic_advisors')}",
        f"- Model tiers: {', '.join(independence.get('model_tiers') or []) or 'not reported'}",
        f"- Fallbacks used: {independence.get('fallback_used_count')}",
        f"- Reading: {independence.get('statement')}",
    ])


def _render_global_judge(lines: list[str], judge: Mapping[str, Any]) -> None:
    """Render the mounted Global Judge summary (V-6)."""

    if not judge:
        return
    lines.extend(["", "## Global Judge (offline, proposal-only)", f"- Status: {judge.get('status')}"])
    if judge.get("status") != "completed":
        lines.append(f"- Degraded: {judge.get('reason')}")
        return
    lines.extend([
        f"- Judge run: {judge.get('run_id')} (evidence pack {judge.get('evidence_pack_id')})",
        f"- Diagnosis summary: {json.dumps(judge.get('diagnosis_summary') or {}, ensure_ascii=False, sort_keys=True)}",
        f"- Proposals: {judge.get('proposal_count')} (shadow cards {judge.get('shadow_card_count')})",
        f"- Report: {judge.get('report_path')}",
        f"- {judge.get('action_limit')}",
    ])


def write_agent_report(
    run_dir: str | Path,
    *,
    task: Mapping[str, Any],
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    observation: Optional[Mapping[str, Any]],
    tool_results: Iterable[Mapping[str, Any]],
    decision: Optional[Mapping[str, Any]] = None,
    multi_agent_review: Optional[Mapping[str, Any]] = None,
    global_judge: Optional[Mapping[str, Any]] = None,
) -> Path:
    run_path = Path(run_dir)
    skill_load = load_stage_skills(
        "agent_reporting",
        requested_context_layers=(
            "task_context",
            "memory_context_summary",
            "dynamic_tail.observation_summary",
            "dynamic_tail.event_refs",
            "dynamic_tail.tool_results",
            "artifact_refs",
        ),
        available_inputs=("agent_task", "agent_plan", "tool_events", "observation_summary", "decision_record"),
        event_path=run_path / "agent_events.jsonl",
    )
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
        f"- Memory snapshot: {state.get('memory_snapshot_id') or 'not available'} ({state.get('memory_mode') or 'no_global_memory'})",
        f"- Memory context key: {state.get('memory_context_key') or 'not applicable'}",
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
        f"- Reporting procedure: {', '.join(item.spec.skill_id for item in skill_load.loaded) or 'base safety rules'}",
    ])
    if skill_load.fallback_to_base_rules:
        lines.append("- Skill loading degraded to base safety rules; see `agent_events.jsonl` for the loading failure.")
    _render_final_output_contract(lines, observed)
    budget_report = run_path / "budget_reallocation_report.md"
    if budget_report.is_file():
        lines.extend(["", budget_report.read_text(encoding="utf-8").strip()])
    review = dict(multi_agent_review or {})
    merge = dict(review.get("merge") or {})
    if review:
        lines.extend(["", "## Multi-agent review advice", f"- Evidence pack hash: {review.get('evidence_pack_hash', 'not available')}", f"- Advisor runs: {len(review.get('advisor_records') or [])}", f"- Accepted advice: {len(merge.get('accepted_advice') or [])}", f"- Policy-rejected advice: {len(merge.get('policy_rejections') or [])}", f"- Conflicting advice: {len(merge.get('conflicts') or [])}"])
        for item in merge.get("policy_rejections") or []:
            lines.append(f"- Rejected advisor {item.get('advisor_id')}: {item.get('reason')}")
        for conflict in merge.get("conflicts") or []:
            lines.append(f"- Conflict requires human review: {conflict.get('finding_key')}")
        lines.append("- These are read-only recommendations. They do not change scores, formal artifacts, prompts, operators, Router output, or active Memory.")
        _render_independence(lines, review)
    _render_global_judge(lines, dict(global_judge or {}))
    return _write(run_path / "agent_report.md", "\n".join(lines) + "\n")


def write_global_review_artifacts(
    run_dir: str | Path,
    observation: Mapping[str, Any],
    *,
    global_judge: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Path]:
    root = Path(run_dir)
    evidence = list(observation.get("evidence_refs", []))[:10]
    issue = str(observation.get("main_issue") or "insufficient experiment evidence")
    status = "needs_human_review" if evidence else "rejected_insufficient_evidence"
    contract = observation.get("final_output_contract") if isinstance(observation.get("final_output_contract"), Mapping) else {}
    judge = dict(global_judge or {})
    proposal_body = {
        "status": status,
        "topic": issue,
        "evidence_refs": evidence,
        "score_delta": contract.get("score_delta"),
        "best_sample_id": contract.get("best_sample_id"),
        "judge_status": judge.get("status"),
        "judge_run_id": judge.get("run_id"),
        "recommendation": "Review the automatic evidence in a Shadow/Replay workflow; do not modify prompts, Router, scoring, state, or active Memory automatically.",
    }
    proposal = {"proposal_id": _proposal_id(redact(proposal_body)), **proposal_body}
    assert proposal["status"] in PROPOSAL_STATUSES
    proposal_path = root / "optimization_proposals.jsonl"
    proposal_path.write_text(json.dumps(proposal, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Read-only Global Review",
        "",
        f"- Proposal id: {proposal['proposal_id']}",
        f"- Main observed issue: {issue}",
        f"- score_increased branches: {observation.get('score_increased_count', 0)}",
        f"- not_applicable branches: {observation.get('not_applicable_count', 0)}",
        f"- Evidence references: {len(evidence)}",
    ]
    if contract.get("available"):
        lines.extend([
            f"- Best sample: {contract.get('best_sample_id')}",
            f"- Score before -> after: {contract.get('score_before')} -> {contract.get('score_after')} (delta {contract.get('score_delta')})",
            f"- Operator path: {', '.join(contract.get('operator_path') or []) or 'not available'}",
        ])
    lines.append(f"- Global Judge: {judge.get('status') or 'not mounted'}")
    lines.append("- This is a proposal-only review. It does not change prompts, Router, Rubric, scores, state, operators, or Memory.")
    review = "\n".join(lines) + "\n"
    return {"global_review_report": _write(root / "global_review_report.md", review), "optimization_proposals": proposal_path}
