"""CLI for the controlled Question Evolution Agent v1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Optional

from agent_runtime.decisions import decide_next_action, write_decision
from agent_runtime.context import build_context_pack
from agent_runtime.multi_agent.coordinator import run_post_experiment_review
from agent_runtime.global_memory import GlobalMemoryStore, SnapshotUnavailable, router_cache_key
from agent_runtime.executor import Executor, ExecutorError
from agent_runtime.observer import observe_experiment
from agent_runtime.planner import build_plan
from agent_runtime.policy import PolicyViolation, validate_plan
from agent_runtime.reporter import write_agent_report, write_global_review_artifacts
from agent_runtime.state import create_run_dir, initialize_state, update_state, write_context, write_plan_revision, write_task
from agent_runtime.task import AgentTask, TaskValidationError, load_agent_task, parse_agent_task
from agent_runtime.tools import ToolRegistry


ROOT = Path(__file__).resolve().parent


def _memory_runtime(task: AgentTask) -> tuple[dict[str, Any], dict[str, Any], str | None]:
    """Freeze memory before planning; failed resume snapshots degrade explicitly."""

    store = GlobalMemoryStore(ROOT)
    degraded = False
    if task.memory_snapshot_id:
        try:
            snapshot = store.load_snapshot(task.memory_snapshot_id)
        except SnapshotUnavailable:
            if not task.is_resume:
                raise
            snapshot = store.create_snapshot()
            degraded = True
    else:
        snapshot = store.create_snapshot()
        # A resumed session without its original identifier must never read the
        # latest global cards as though they were the frozen original snapshot.
        degraded = task.is_resume
    if task.allow_global_memory_read and not degraded and snapshot.get("mode") == "global_memory":
        context = store.retrieve(snapshot_id=str(snapshot["memory_snapshot_id"]), query=task.goal, top_k=3)
    else:
        context = {"memory_snapshot_id": snapshot["memory_snapshot_id"], "memory_context_key": None, "retrieval_config_version": "global-memory-retrieval-v1", "top_k": 0, "cards": [], "mode": "no_global_memory" if degraded else snapshot.get("mode", "no_global_memory")}
    path = store.root / "snapshots" / f"{snapshot['memory_snapshot_id']}.json"
    return snapshot, context, str(path) if path.exists() else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled Question Evolution Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "resume", "dry-run"):
        child = subparsers.add_parser(command)
        child.add_argument("--task", required=True)
    review = subparsers.add_parser("review")
    review.add_argument("--exp-dir", required=True)
    review.add_argument("--task")
    return parser


def _review_task(args: argparse.Namespace) -> AgentTask:
    if args.task:
        task = load_agent_task(args.task, project_root=ROOT)
        if task.review_mode != "report_only":
            raise TaskValidationError("review requires review_mode=report_only")
        return task
    return parse_agent_task(
        {
            "goal": "Read-only review of an existing Question Evolution experiment",
            "review_mode": "report_only",
            "resume_exp_dir": args.exp_dir,
            "allowed_tools": ["observe_experiment", "write_agent_report"],
        },
        project_root=ROOT,
    )


def _blocked_observation(reason: str, experiment_dir: str = "") -> Dict[str, Any]:
    return {
        "experiment_dir": experiment_dir,
        "status": "blocked",
        "blocked_reason": reason,
        "manifest_status": "not_checked",
        "final_records_count": 0,
        "pending_count": 0,
        "boundary_candidate_count": 0,
        "score_increased_count": 0,
        "not_applicable_count": 0,
        "validation_failed_count": 0,
        "branch_error_count": 0,
        "target_reached": False,
        "missing_artifacts": [],
        "evidence_refs": [],
    }


def run_agent(command: str, task: AgentTask, *, registry: Optional[ToolRegistry] = None) -> tuple[int, Path]:
    run_dir = create_run_dir(ROOT)
    state = initialize_state(
        run_dir,
        run_id=run_dir.name,
        mode=command,
        root_goal=task.goal,
        budgets={"max_search_steps": task.max_search_steps, "boundary_target": task.boundary_target},
    )
    write_task(run_dir, task.as_dict())
    try:
        snapshot, memory_context, snapshot_path = _memory_runtime(task)
    except SnapshotUnavailable as exc:
        observation = _blocked_observation(str(exc), task.resume_exp_dir)
        update_state(run_dir, state, status="blocked", terminal_reason="memory_snapshot_unavailable", blocked_reason=str(exc))
        write_agent_report(run_dir, task=task.as_dict(), state=state, plan={}, observation=observation, tool_results=[], decision={"action": "blocked", "reason": str(exc)})
        return 2, run_dir
    update_state(run_dir, state, memory_snapshot_id=snapshot["memory_snapshot_id"], memory_snapshot_path=snapshot_path, memory_context_key=memory_context.get("memory_context_key"), memory_mode=memory_context.get("mode", snapshot.get("mode", "no_global_memory")))
    initial_context = build_context_pack(task, memory_context=memory_context)
    update_state(run_dir, state, status="context_ready")
    plan = build_plan(task, command=command, context_pack=initial_context)
    plan["memory_snapshot_id"] = snapshot["memory_snapshot_id"]
    plan["memory_context_key"] = memory_context.get("memory_context_key")
    plan["router_cache_key"] = router_cache_key(base_key=str(plan["plan_id"]), memory_snapshot_id=str(snapshot["memory_snapshot_id"]))
    plan["env_overrides"]["MEMORY_SNAPSHOT_ID"] = snapshot["memory_snapshot_id"]
    try:
        validate_plan(task, plan)
    except PolicyViolation as exc:
        plan["blocked_reasons"].append(str(exc))
    plan = write_plan_revision(run_dir, state, plan)
    write_context(run_dir, build_context_pack(task, plan=plan, memory_context=memory_context))
    update_state(run_dir, state, status="planned", current_step_id=plan["steps"][0]["step_id"] if plan["steps"] else None)
    if plan["blocked_reasons"]:
        observation = _blocked_observation("; ".join(plan["blocked_reasons"]), task.resume_exp_dir)
        decision = decide_next_action(task, observation)
        write_decision(run_dir, decision)
        update_state(
            run_dir,
            state,
            status="blocked",
            current_step_id=None,
            blocked_reason=decision["reason"],
            terminal_reason="invalid_plan",
            requires_manual_review=bool(decision.get("requires_human_review")),
            manual_review_status="pending" if decision.get("requires_human_review") else None,
        )
        write_agent_report(run_dir, task=task.as_dict(), state=state, plan=plan, observation=observation, tool_results=[], decision=decision)
        return 2, run_dir

    if command == "dry-run":
        update_state(run_dir, state, status="completed", current_step_id=None, completed_step_ids=["plan"], terminal_reason="dry_run_completed")
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0, run_dir

    registry = registry or ToolRegistry(project_root=ROOT, run_dir=run_dir)
    executor = Executor(
        task=task,
        plan=plan,
        registry=registry,
        run_dir=run_dir,
        state=state,
        observe=observe_experiment,
        update_state=update_state,
    )
    try:
        results = executor.execute(plan["steps"])
    except ExecutorError as exc:
        results = [{"tool": "executor", "ok": False, "return_code": -1, "recoverable": False, "failure_category": "fatal_system_error", "stderr_summary": str(exc)}]
    observation = executor.observation
    if observation is None:
        observation = _blocked_observation("observation was not reached", state.get("experiment_dir") or task.resume_exp_dir)
    multi_agent_review: Dict[str, Any] = {}
    try:
        multi_agent_review = run_post_experiment_review(run_dir, task=task.as_dict(), state=state, plan=plan, observation=observation)
    except Exception as exc:
        # Advisor collaboration is review-only.  Primary experiment results,
        # decision logic, and report creation remain available on degradation.
        multi_agent_review = {"merge": {"accepted_advice": [], "policy_rejections": [], "conflicts": []}, "degraded_reason": str(exc)}
    decision = decide_next_action(task, observation, tool_results=results)
    write_decision(run_dir, decision)
    if decision["action"] == "replan":
        update_state(run_dir, state, status="replanning", current_step_id=None)
        plan = write_plan_revision(
            run_dir,
            state,
            {**build_plan(task, command=command, context_pack=build_context_pack(task, plan=plan, observation=observation, previous_decision=decision, memory_context=memory_context)), "memory_snapshot_id": snapshot["memory_snapshot_id"], "memory_context_key": memory_context.get("memory_context_key"), "router_cache_key": router_cache_key(base_key=str(plan["plan_id"]), memory_snapshot_id=str(snapshot["memory_snapshot_id"]))},
            trigger_reason=decision["reason"],
        )
        status = "suspended"
        terminal_reason = "replan_pending_execution"
        manual_review = False
    elif decision["action"] == "blocked":
        status = "blocked"
        terminal_reason = str(decision.get("terminal_reason") or "blocked")
        manual_review = bool(decision.get("requires_human_review"))
    elif decision["action"] == "suspend" or decision.get("requires_human_review"):
        # Automatic scoring remains evidence only.  A Session with pending
        # review is intentionally suspended rather than marked completed.
        status = "suspended"
        terminal_reason = str(decision.get("terminal_reason") or "manual_review_required")
        manual_review = bool(decision.get("requires_human_review"))
    else:
        status = "completed"
        terminal_reason = str(decision.get("terminal_reason") or "completed")
        manual_review = False
    update_state(
        run_dir,
        state,
        status=status,
        current_step_id=None,
        blocked_reason=decision["reason"] if status == "blocked" else None,
        terminal_reason=terminal_reason,
        requires_manual_review=manual_review,
        manual_review_status="pending" if manual_review else None,
    )
    report_step = next((step for step in plan["steps"] if step.get("tool_name") == "write_agent_report"), None)
    if report_step:
        executor.execute_report(report_step, lambda: write_agent_report(run_dir, task=task.as_dict(), state=state, plan=plan, observation=observation, tool_results=results, decision=decision, multi_agent_review=multi_agent_review))
    if command == "review":
        write_global_review_artifacts(run_dir, observation)
    return (2 if status == "blocked" else 0), run_dir


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        task = _review_task(args) if args.command == "review" else load_agent_task(args.task, project_root=ROOT)
        code, run_dir = run_agent(args.command, task)
    except (TaskValidationError, PolicyViolation) as exc:
        print(f"AgentTask error: {exc}")
        return 2
    print(f"Agent run directory: {run_dir}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
