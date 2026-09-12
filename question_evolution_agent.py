"""CLI for the controlled Question Evolution Agent v1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from agent_runtime.decisions import decide_next_action, write_decision
from agent_runtime.budgeting import (
    assess_budget_reallocation,
    build_budget_replan,
    write_budget_artifacts,
)
from agent_runtime.context import build_context_pack
from agent_runtime.contracts import ContractViolation
from agent_runtime.events import append_event
from agent_runtime.multi_agent.coordinator import run_post_experiment_review
from agent_runtime.global_judge import mount_judge_for_review
from agent_runtime.global_memory import RETRIEVAL_CONFIG_VERSION, GlobalMemoryStore, SnapshotUnavailable, router_cache_key
from agent_runtime.executor import Executor, ExecutorError
from agent_runtime.observer import observe_experiment
from agent_runtime.planner import build_plan, plan_env_overrides
from agent_runtime.policy import PolicyViolation, validate_plan
from agent_runtime.reporter import write_agent_report, write_global_review_artifacts
from agent_runtime.skills import load_stage_skills
from agent_runtime.state import create_run_dir, initialize_state, load_state, update_state, write_context, write_plan_revision, write_task
from agent_runtime.task import AgentTask, TaskValidationError, load_agent_task, parse_agent_task
from agent_runtime.tools import ToolRegistry


ROOT = Path(__file__).resolve().parent
# The control loop may continue inside one Session, but never without an
# explicit, bounded round budget (design §6.2 / report O-2).
MAX_SESSION_ROUNDS = 3
MAX_SESSION_ROLLBACKS = 1
RESUMABLE_SESSION_STATUSES = {"planned", "executing", "observing", "replanning", "suspended", "stopped", "blocked", "failed"}


def _memory_runtime(task: AgentTask, *, preferred_snapshot_id: str = "") -> tuple[dict[str, Any], dict[str, Any], str | None, dict[str, Any]]:
    """Freeze memory before planning.

    Returns ``(snapshot, context, snapshot_path, audit)``.  The audit block makes
    a degraded resume *explicit* -- ``memory_mode`` plus the unrecoverable
    ``original_memory_snapshot_id`` and the reason -- instead of silently
    substituting a fresh snapshot (report R-4, design §16.4).
    """

    store = GlobalMemoryStore(ROOT)
    requested = preferred_snapshot_id or task.memory_snapshot_id
    audit: dict[str, Any] = {
        "memory_mode": "global_memory",
        "original_memory_snapshot_id": requested or None,
        "memory_degraded": False,
        "memory_degraded_reason": None,
    }
    if requested:
        try:
            snapshot = store.load_snapshot(requested)
        except SnapshotUnavailable:
            if not (task.is_resume or preferred_snapshot_id):
                raise
            snapshot = store.create_snapshot()
            audit.update({
                "memory_mode": "degraded_missing_snapshot",
                "memory_degraded": True,
                "memory_degraded_reason": f"the frozen memory snapshot {requested} is unavailable",
            })
    else:
        snapshot = store.create_snapshot()
        if task.is_resume:
            # A resumed session without its original identifier must never read
            # the latest global cards as though they were the frozen original.
            audit.update({
                "memory_mode": "degraded_missing_snapshot",
                "memory_degraded": True,
                "memory_degraded_reason": "the resumed session did not record its original memory snapshot",
            })
    read_allowed = task.allow_global_memory_read and not audit["memory_degraded"] and snapshot.get("mode") == "global_memory"
    if read_allowed:
        context = store.retrieve(snapshot_id=str(snapshot["memory_snapshot_id"]), query=task.goal, top_k=3)
    else:
        # Reference the single source of truth instead of a hard-coded literal:
        # a duplicated version string drifts from the real retriever (V-1/V-2).
        context = {
            "memory_snapshot_id": snapshot["memory_snapshot_id"], "memory_context_key": None,
            "retrieval_config_version": RETRIEVAL_CONFIG_VERSION, "top_k": 0, "cards": [],
            "mode": "no_global_memory" if audit["memory_degraded"] else snapshot.get("mode", "no_global_memory"),
        }
    audit["memory_context_mode"] = context.get("mode")
    path = store.root / "snapshots" / f"{snapshot['memory_snapshot_id']}.json"
    return snapshot, context, str(path) if path.exists() else None, audit


def _open_session(run_dir: Path, *, command: str) -> Dict[str, Any]:
    """Load an existing Session directory so a new process can continue it.

    ``create_run_dir(..., exist_ok=False)`` meant every invocation started a
    fresh Session, so ``resume_checkpoint`` had no consumer and the idempotency
    ledger was always empty (report O-7).  Resuming reuses the *same* ``run_dir``,
    which makes the confirmed checkpoint and the Session-scoped ledger usable.
    """

    if not (run_dir / "session_manifest.json").is_file():
        raise ExecutorError(f"session directory has no session manifest: {run_dir}")
    state = load_state(run_dir)
    status = str(state.get("status") or "")
    if status not in RESUMABLE_SESSION_STATUSES:
        raise ExecutorError(f"session {state.get('session_id')} is not resumable from status '{status}'")
    append_event(
        run_dir / "agent_events.jsonl",
        "session_resumed",
        {
            "session_id": state.get("session_id"), "mode": command, "from_status": status,
            "resume_checkpoint": state.get("resume_checkpoint"),
            "memory_snapshot_id": state.get("memory_snapshot_id"),
        },
    )
    return state



def _first_observation_id(observations: Any) -> str | None:
    """Return the anchor observation_id for decision correlation."""

    for item in observations or []:
        if isinstance(item, Mapping) and item.get("observation_id"):
            return str(item["observation_id"])
    return None


def _bind_memory_identity(
    plan: Dict[str, Any],
    *,
    task: AgentTask,
    command: str,
    snapshot: Dict[str, Any],
    memory_context: Dict[str, Any],
) -> Dict[str, Any]:
    """Attach the frozen memory identity to a plan from one single source.

    Both the initial plan and every replanned plan go through this helper, so
    ``env_overrides["MEMORY_SNAPSHOT_ID"]`` (which the router folds into its
    cache identity) can never be dropped by a later rebuild.
    """

    snapshot_id = str(snapshot["memory_snapshot_id"])
    plan["env_overrides"] = plan_env_overrides(task, command=command, memory_snapshot_id=snapshot_id)
    plan["memory_snapshot_id"] = snapshot_id
    plan["memory_context_key"] = memory_context.get("memory_context_key")
    plan["router_cache_key"] = router_cache_key(base_key=str(plan["plan_id"]), memory_snapshot_id=snapshot_id)
    return plan


def _add_session_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the bounded control-loop / resume switches shared by run commands."""

    parser.add_argument("--task", required=True)
    parser.add_argument(
        "--resume-session",
        default=None,
        help="reuse an existing agent_runs/<day>/<session> directory and consume its confirmed checkpoint",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=MAX_SESSION_ROUNDS,
        help=f"maximum execute/observe/decide rounds inside one Session (default {MAX_SESSION_ROUNDS})",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled Question Evolution Agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("run", "resume", "dry-run"):
        _add_session_arguments(subparsers.add_parser(command))
    review = subparsers.add_parser("review")
    review.add_argument("--exp-dir", required=True)
    review.add_argument("--task")
    review.add_argument("--resume-session", default=None)
    review.add_argument("--max-rounds", type=int, default=MAX_SESSION_ROUNDS)
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


def _load_or_build_plan(
    command: str,
    task: AgentTask,
    *,
    run_dir: Path,
    state: Dict[str, Any],
    context: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    memory_context: Mapping[str, Any],
    resumed: bool,
) -> Dict[str, Any]:
    """Return the effective plan, reusing the stored revision when resuming.

    A resumed Session must continue *its own* plan revision: re-planning would
    mint a new ``plan_id`` and make the confirmed checkpoint meaningless (O-7).
    """

    if resumed:
        plan_path = state.get("current_plan_path")
        if plan_path and Path(str(plan_path)).is_file():
            stored = json.loads(Path(str(plan_path)).read_text(encoding="utf-8"))
            if isinstance(stored, dict) and stored.get("steps"):
                stored.setdefault("blocked_reasons", [])
                append_event(run_dir / "agent_events.jsonl", "plan_revision_reused", {
                    "session_id": state.get("session_id"),
                    "plan_revision": stored.get("plan_revision"),
                    "plan_path": str(plan_path),
                })
                return stored
    plan = build_plan(task, command=command, context_pack=context)
    plan = _bind_memory_identity(plan, task=task, command=command, snapshot=snapshot, memory_context=memory_context)
    try:
        validate_plan(task, plan)
    except PolicyViolation as exc:
        plan["blocked_reasons"].append(str(exc))
    return write_plan_revision(run_dir, state, plan)


def run_agent(
    command: str,
    task: AgentTask,
    *,
    registry: Optional[ToolRegistry] = None,
    resume_session_dir: str | Path | None = None,
    max_rounds: int = MAX_SESSION_ROUNDS,
) -> tuple[int, Path]:
    """Run one Session: bootstrap, then a bounded execute/observe/decide loop.

    ``O-2``/``O-7``: the loop consumes a confirmed checkpoint when the process is
    resumed, and it may continue inside one Session -- driven by ``run_pipeline``
    / ``resume_pipeline`` / ``run_review`` and by the recovery recipes -- but
    only within ``max_rounds`` and ``MAX_SESSION_ROLLBACKS`` bounds.
    """

    resumed = False
    if resume_session_dir is not None:
        run_dir = Path(resume_session_dir).resolve()
        try:
            state = _open_session(run_dir, command=command)
        except (ExecutorError, ValueError, OSError) as exc:
            print(f"Session resume error: {exc}")
            return 2, run_dir
        resumed = True
    else:
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
        snapshot, memory_context, snapshot_path, memory_audit = _memory_runtime(
            task, preferred_snapshot_id=str(state.get("memory_snapshot_id") or "") if resumed else ""
        )
    except SnapshotUnavailable as exc:
        observation = _blocked_observation(str(exc), task.resume_exp_dir)
        update_state(run_dir, state, status="blocked", terminal_reason="memory_snapshot_unavailable", blocked_reason=str(exc))
        write_agent_report(run_dir, task=task.as_dict(), state=state, plan={}, observation=observation, tool_results=[], decision={"action": "blocked", "reason": str(exc)})
        return 2, run_dir
    update_state(
        run_dir, state,
        memory_snapshot_id=snapshot["memory_snapshot_id"],
        memory_snapshot_path=snapshot_path,
        memory_context_key=memory_context.get("memory_context_key"),
        memory_mode=memory_audit["memory_mode"],
        original_memory_snapshot_id=memory_audit["original_memory_snapshot_id"],
        memory_degraded=bool(memory_audit["memory_degraded"]),
        memory_degraded_reason=memory_audit["memory_degraded_reason"],
    )
    if memory_audit["memory_degraded"]:
        append_event(run_dir / "agent_events.jsonl", "memory_snapshot_degraded", {
            "session_id": state.get("session_id"),
            "original_memory_snapshot_id": memory_audit["original_memory_snapshot_id"],
            "substituted_memory_snapshot_id": snapshot["memory_snapshot_id"],
            "reason": memory_audit["memory_degraded_reason"],
        })
    planning_skills = load_stage_skills(
        "planning_strategy",
        requested_context_layers=(
            "task_context",
            "memory_context_summary",
            "dynamic_tail.observation_summary",
            "dynamic_tail.event_refs",
            "artifact_refs",
        ),
        available_inputs=("agent_task", "budget", "allowed_tools", "memory_top_k", "observation_summary"),
        event_path=run_dir / "agent_events.jsonl",
    )
    update_state(run_dir, state, status="context_ready")
    # V-5: the loaded SKILL.md bodies are injected into the stable prefix (and
    # their content hash into the cache identity) instead of only being logged.
    loaded_skills = list(getattr(planning_skills, "loaded", ()) or ())
    initial_context = build_context_pack(
        task, memory_context=memory_context, runtime_state=state,
        skills=loaded_skills, run_dir=run_dir,
    )
    update_state(run_dir, state, context_cache_key=initial_context["context_cache"]["context_cache_key"])
    plan = _load_or_build_plan(
        command, task, run_dir=run_dir, state=state, context=initial_context,
        snapshot=snapshot, memory_context=memory_context, resumed=resumed,
    )
    persisted_context = build_context_pack(
        task, plan=plan, memory_context=memory_context, runtime_state=state,
        skills=loaded_skills, run_dir=run_dir,
    )
    write_context(run_dir, persisted_context)
    update_state(run_dir, state, context_cache_key=persisted_context["context_cache"]["context_cache_key"])
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
    executor: Optional[Executor] = None
    results: list[Dict[str, Any]] = []
    observation: Dict[str, Any] = _blocked_observation("observation was not reached", task.resume_exp_dir)
    decision: Dict[str, Any] = {"action": "blocked", "reason": "the control loop did not run"}
    multi_agent_review: Dict[str, Any] = {}
    rounds_used = 0
    rollbacks_used = 0
    budget_ledger = budget_observation = budget_proposal = budget_decision = None

    def _rollback() -> Optional[Mapping[str, Any]]:
        """Re-apply the previous plan revision as a *new* revision (append-only)."""

        replaces = (plan.get("replan_context") or {}).get("replaces_plan_path")
        if not replaces or not Path(str(replaces)).is_file():
            return None
        previous = json.loads(Path(str(replaces)).read_text(encoding="utf-8"))
        if not isinstance(previous, dict) or not previous.get("steps"):
            return None
        return write_plan_revision(run_dir, state, previous, trigger_reason=f"rollback_from:{plan.get('plan_id')}")

    while True:
        executor = Executor(
            task=task,
            plan=plan,
            registry=registry,
            run_dir=run_dir,
            state=state,
            observe=observe_experiment,
            update_state=update_state,
            rollback=_rollback,
        )
        try:
            results = executor.execute(plan["steps"])
        except ExecutorError as exc:
            results = [{"tool": "executor", "ok": False, "return_code": -1, "recoverable": False, "failure_category": "fatal_system_error", "stderr_summary": str(exc)}]
        observation = executor.observation
        if observation is None:
            observation = _blocked_observation("observation was not reached", state.get("experiment_dir") or task.resume_exp_dir)
        # Stage 10 reads the published aggregate only.  It records a proposal and
        # validator decision now, but applies an approved transfer only when the
        # normal deterministic controller already selected a future-only replan.
        budget_ledger, budget_observation, budget_proposal, budget_decision = assess_budget_reallocation(
            run_dir, task=task, state=state, observation=observation,
        )
        observation["budget_reallocation"] = {
            "proposal_id": budget_proposal["proposal_id"],
            "decision_id": budget_decision["decision_id"],
            "status": budget_decision["status"],
        }
        try:
            multi_agent_review = run_post_experiment_review(run_dir, task=task.as_dict(), state=state, plan=plan, observation=observation)
        except Exception as exc:
            # Advisor collaboration is review-only.  Primary experiment results,
            # decision logic, and report creation remain available on degradation.
            multi_agent_review = {"merge": {"accepted_advice": [], "policy_rejections": [], "conflicts": []}, "degraded_reason": str(exc)}
        continuations_remaining = max(0, int(max_rounds) - rounds_used - 1)
        decision = decide_next_action(
            task,
            observation,
            tool_results=results,
            session_id=str(state.get("session_id") or run_dir.name),
            plan_id=str(plan.get("plan_id") or ""),
            plan_revision=int(state.get("plan_revision") or 0),
            observation_id=_first_observation_id(executor.normalized_observations),
            continuations_remaining=continuations_remaining,
        )
        if decision["action"] in {"blocked", "suspend"} or observation.get("score_increased_count") or observation.get("budget_exhausted"):
            # This documents the recovery procedure without granting it any write
            # authority.  The deterministic decision and normal policy checks are
            # still the only route to an actual resume or replan.
            load_stage_skills(
                "recovery_diagnosis",
                requested_context_layers=(
                    "task_context",
                    "dynamic_tail.observation_summary",
                    "dynamic_tail.event_refs",
                    "dynamic_tail.tool_results",
                    "artifact_refs",
                ),
                available_inputs=("agent_events", "tool_results", "checkpoint", "manifest", "termination_reason"),
                event_path=run_dir / "agent_events.jsonl",
            )
        write_decision(run_dir, decision)

        if decision["action"] in {"run_pipeline", "resume_pipeline", "run_review"}:
            # O-2: the Session continues instead of parking every round.  Each
            # continuation is a *new plan revision* in the same Session, so the
            # ledger and checkpoint keep their meaning.
            rounds_used += 1
            append_event(run_dir / "agent_events.jsonl", "session_round_started", {
                "session_id": state.get("session_id"), "round": rounds_used,
                "action": decision["action"], "plan_revision": state.get("plan_revision"),
                "continuations_remaining": continuations_remaining, "reason": decision["reason"],
            })
            update_state(run_dir, state, status="replanning", current_step_id=None)
            continuation_context = build_context_pack(
                task, plan=plan, observation=observation, previous_decision=decision,
                memory_context=memory_context, runtime_state=state,
                skills=loaded_skills, run_dir=run_dir,
            )
            plan = write_plan_revision(
                run_dir,
                state,
                _bind_memory_identity(
                    build_plan(task, command=command, context_pack=continuation_context),
                    task=task, command=command, snapshot=snapshot, memory_context=memory_context,
                ),
                trigger_reason=f"control_loop_continue:{decision['action']}",
            )
            persisted_context = build_context_pack(
                task, plan=plan, observation=observation, previous_decision=decision,
                memory_context=memory_context, runtime_state=state,
                skills=loaded_skills, run_dir=run_dir,
            )
            write_context(run_dir, persisted_context)
            update_state(run_dir, state, context_cache_key=persisted_context["context_cache"]["context_cache_key"])
            update_state(run_dir, state, status="planned", current_step_id=plan["steps"][0]["step_id"] if plan["steps"] else None)
            continue

        if decision.get("recovery_action") == "rollback_and_retry" and rollbacks_used < MAX_SESSION_ROLLBACKS:
            rolled_back = executor.rollback_to_previous_revision(reason=str(decision.get("recovery_reason") or decision["reason"]))
            if rolled_back is not None:
                rollbacks_used += 1
                plan = rolled_back
                # The rolled-back revision is the previous, already-executed
                # plan: its steps are deliberately *not* marked completed, so the
                # retry re-runs them against the current evidence.
                update_state(
                    run_dir, state, status="executing", completed_step_ids=[],
                    current_step_id=plan["steps"][0]["step_id"] if plan["steps"] else None,
                )
                continue

        if decision["action"] == "replan":
            update_state(run_dir, state, status="replanning", current_step_id=None)
            replan_prompt_context = build_context_pack(
                task,
                plan=plan,
                observation=observation,
                previous_decision=decision,
                memory_context=memory_context,
                runtime_state=state,
                skills=loaded_skills,
                run_dir=run_dir,
            )
            if budget_decision["status"] == "approved":
                budget_ledger.apply_changes(budget_decision["approved_changes"], proposal_id=budget_proposal["proposal_id"])
                write_budget_artifacts(run_dir, ledger=budget_ledger, proposal=budget_proposal, decision=budget_decision)
                update_state(run_dir, state, budgets={**dict(state.get("budgets") or {}), "remaining": budget_ledger.remaining_by_type()})
                plan = write_plan_revision(
                    run_dir,
                    state,
                    build_budget_replan(
                        plan,
                        proposal=budget_proposal,
                        decision=budget_decision,
                        ledger=budget_ledger,
                        completed_step_ids=list(state.get("completed_step_ids") or []),
                    ),
                    trigger_reason=f"approved_budget_reallocation:{budget_proposal['proposal_id']}",
                )
            else:
                plan = write_plan_revision(
                    run_dir,
                    state,
                    _bind_memory_identity(
                        build_plan(task, command=command, context_pack=replan_prompt_context),
                        task=task,
                        command=command,
                        snapshot=snapshot,
                        memory_context=memory_context,
                    ),
                    trigger_reason=decision["reason"],
                )
            persisted_context = build_context_pack(
                task,
                plan=plan,
                observation=observation,
                previous_decision=decision,
                memory_context=memory_context,
                runtime_state=state,
                skills=loaded_skills,
                run_dir=run_dir,
            )
            write_context(run_dir, persisted_context)
            update_state(run_dir, state, context_cache_key=persisted_context["context_cache"]["context_cache_key"])
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
        break

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
    global_judge: Dict[str, Any] = {}
    if command == "review":
        # V-6: mount the offline Global Judge on the review path.  It is
        # proposal-only and fail-open, so a rejection can never change the
        # Session outcome -- but the design's offline attribution now runs.
        experiment_dir = str(state.get("experiment_dir") or task.resume_exp_dir or "")
        if experiment_dir:
            global_judge = mount_judge_for_review(
                run_dir,
                experiment_dir=experiment_dir,
                snapshot_id=str(state.get("memory_snapshot_id") or ""),
                project_root=ROOT,
            )
        else:
            global_judge = {"status": "degraded", "reason": "no experiment directory was resolved for the review"}
    report_step = next((step for step in plan["steps"] if step.get("tool_name") == "write_agent_report"), None)
    if report_step and executor is not None:
        executor.execute_report(report_step, lambda: write_agent_report(run_dir, task=task.as_dict(), state=state, plan=plan, observation=observation, tool_results=results, decision=decision, multi_agent_review=multi_agent_review, global_judge=global_judge))
    if command == "review":
        write_global_review_artifacts(run_dir, observation, global_judge=global_judge)
    return (2 if status == "blocked" else 0), run_dir


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        task = _review_task(args) if args.command == "review" else load_agent_task(args.task, project_root=ROOT)
        code, run_dir = run_agent(
            args.command,
            task,
            resume_session_dir=getattr(args, "resume_session", None),
            max_rounds=int(getattr(args, "max_rounds", MAX_SESSION_ROUNDS)),
        )
    except (TaskValidationError, PolicyViolation) as exc:
        print(f"AgentTask error: {exc}")
        return 2
    except ContractViolation as exc:
        print(f"Agent contract violation: {exc}")
        return 2
    print(f"Agent run directory: {run_dir}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
