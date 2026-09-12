"""Stage-5 regressions for the control loop, recovery recipes and session resume.

Each test maps to one finding of
``docs/Agent改造方案/Agent_Harness_代码审查报告_2026-09-11.md`` §4/§7:

- O-2 ``run_pipeline`` / ``resume_pipeline`` / ``run_review`` are reachable,
  the Session actually continues *and re-executes*; rollback machinery exists.
- O-7 a Session can be resumed from its confirmed checkpoint.
- O-8 ``plan_revision`` is owned by the persistence boundary.
- O-9 one single source declares which execution scopes are supported.
- R-2 ``model_calls`` counts model-billed invocations, and step budgets are real.
- R-3 the recovery recipe library is the data source for recovery actions.
- R-4 a degraded resume is explicit and auditable.
- R-5 configuration defects are not disguised as system faults.
- R-6 side-effecting tools are not retried after observable progress.
"""

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import question_evolution_agent as cli
from agent_runtime.decisions import decide_next_action
from agent_runtime.executor import Executor, ExecutorError
from agent_runtime.global_memory import GlobalMemoryStore
from agent_runtime.observer import record_observations, rollback_completed_observation
from agent_runtime.planner import build_plan
from agent_runtime.policy import PolicyViolation, validate_plan
from agent_runtime.recovery import (
    ACTION_REDUCE_OPERATOR_BUDGET,
    ACTION_REOBSERVE_THEN_FAIL_FAST,
    ACTION_STOP_AND_REPORT,
    ACTION_SUSPEND_WITH_BACKUP_ENDPOINT,
    RECIPES,
    describe_recipes,
    select_recipe,
)
from agent_runtime.state import initialize_state, load_state, write_plan_revision
from agent_runtime.task import SUPPORTED_EXECUTION_SCOPES, parse_agent_task
from agent_runtime.tools import ToolRegistry, get_tool_spec


def _task(tmp_path, **changes):
    raw = {
        "goal": "find boundaries",
        "input_file": "data/data.jsonl",
        "allowed_tools": ["check_environment", "run_full_loop", "observe_experiment", "write_agent_report"],
    }
    raw.update(changes)
    return parse_agent_task(raw, project_root=tmp_path)


def _step(tool, *, expected_outputs=None, budget_limit=None, **changes):
    value = {
        "step_id": f"step_{tool}", "tool_name": tool, "tool": tool, "arguments": {},
        "preconditions": [], "expected_outputs": list(expected_outputs or []),
        "budget_limit": dict(budget_limit or {}), "depends_on": [], "stop_if_failed": True,
    }
    value.update(changes)
    return value


def _update(_run_dir, state, **changes):
    state.update(changes)
    return state


def _full_plan(plan_id):
    """A minimal plan that satisfies the published Policy contract."""

    return {
        "plan_id": plan_id,
        "env_overrides": {},
        "steps": [{
            "step_id": "s1", "intent": "preflight", "tool_name": "check_environment", "tool": "check_environment",
            "arguments": {}, "preconditions": [], "expected_outputs": ["environment_checked"],
            "success_condition": "tool_completed", "business_failure_action": "stop_and_report",
            "system_failure_action": "suspend_or_block", "budget_limit": {}, "depends_on": [],
        }],
    }


# --------------------------------------------------------------------------- R-3 / O-2 recipes


def test_recovery_recipe_table_is_the_data_source_for_recovery_actions():
    assert {recipe["recipe_id"] for recipe in describe_recipes()} >= {
        "retryable_system_error", "configuration_error", "fatal_system_error",
        "manifest_corrupted", "score_increased", "judge_instability", "artifact_missing",
    }
    assert select_recipe(failure_category="retryable_system_error").recovery_action == ACTION_SUSPEND_WITH_BACKUP_ENDPOINT
    assert select_recipe(failure_category="configuration_error").recovery_action == ACTION_STOP_AND_REPORT
    # score_increased is negative gain: a v1 plan has no strategy variation
    # axis, so the recipe stops instead of pretending an automatic rollback
    # would "retry with a different operator strategy".
    score_recipe = select_recipe(observation_types=["score_increased"])
    assert score_recipe.recovery_action == ACTION_STOP_AND_REPORT
    assert score_recipe.max_attempts == 0
    assert select_recipe(observation_types=["candidate_invalid"]).recovery_action == ACTION_REDUCE_OPERATOR_BUDGET
    assert select_recipe(observation_types=["artifact_missing"]).recovery_action == ACTION_REOBSERVE_THEN_FAIL_FAST
    # An unknown signature must still yield a terminal, bounded recipe.
    fallback = select_recipe(observation_types=["nothing_known"])
    assert fallback.recovery_action == ACTION_STOP_AND_REPORT
    assert fallback.max_attempts == 0


def test_every_decision_carries_its_recovery_recipe(tmp_path):
    retryable = decide_next_action(
        _task(tmp_path), {"status": "observed"},
        tool_results=[{"tool": "run_full_loop", "ok": False, "recoverable": True, "failure_category": "retryable_system_error"}],
    )
    assert retryable["recovery_recipe_id"] == "retryable_system_error"
    assert retryable["recovery_action"] == ACTION_SUSPEND_WITH_BACKUP_ENDPOINT

    negative = decide_next_action(_task(tmp_path), {"status": "observed", "score_increased_count": 1})
    assert negative["recovery_recipe_id"] == "score_increased"
    assert negative["recovery_action"] == ACTION_STOP_AND_REPORT
    assert negative["recovery_max_attempts"] == 0
    assert negative["action"] == "stop_and_report"


# --------------------------------------------------------------------------- O-2 continuation


def test_continuation_actions_are_reachable_only_with_remaining_rounds(tmp_path):
    observation = {"status": "observed", "pending_count": 3, "target_reached": False, "final_records_count": 0}

    bounded = decide_next_action(_task(tmp_path), observation, continuations_remaining=2)
    assert bounded["action"] == "run_pipeline"
    assert bounded["terminal_reason"] is None
    assert bounded["requires_human_review"] is False

    exhausted = decide_next_action(_task(tmp_path), observation, continuations_remaining=0)
    assert exhausted["action"] == "stop_and_report"
    assert "no remaining execution rounds" in exhausted["reason"]


def test_resume_and_review_continuations_use_their_own_execution_entry_point(tmp_path):
    resumed = parse_agent_task(
        {"goal": "resume", "input_file": "", "resume_exp_dir": "experiments/day/exp", "resume_start_round": 2},
        project_root=tmp_path,
    )
    observation = {"status": "observed", "pending_count": 1, "target_reached": False}
    assert decide_next_action(resumed, observation, continuations_remaining=1)["action"] == "resume_pipeline"

    reviewed = parse_agent_task(
        {"goal": "review", "input_file": "", "review_mode": "report_only",
         "resume_exp_dir": "experiments/day/exp", "allowed_tools": ["observe_experiment", "write_agent_report"]},
        project_root=tmp_path,
    )
    assert decide_next_action(reviewed, observation, continuations_remaining=1)["action"] == "run_review"


def test_a_session_actually_continues_and_bumps_the_plan_revision(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr("agent_runtime.executor.validate_published_artifact", lambda *_a, **_k: (True, "ok"))
    calls = []
    observations = []

    class FakeRegistry(ToolRegistry):
        def __init__(self):
            pass

        def check_environment(self, _task):
            return {"tool": "check_environment", "ok": True, "ready": True, "return_code": 0, "recoverable": False}

        def run_full_loop(self, _task, _env):
            calls.append("run_full_loop")
            exp_dir = tmp_path / "experiments" / "day" / "exp"
            exp_dir.mkdir(parents=True, exist_ok=True)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "recoverable": False, "experiment_dir": str(exp_dir)}

    def fake_observer(*_args, **kwargs):
        observations.append(1)
        (Path(kwargs["run_dir"]) / "agent_observation.json").write_text("{}\n", encoding="utf-8")
        pending = 3 if len(observations) == 1 else 0
        return {
            "status": "observed", "manifest_status": "ok", "target_reached": False,
            "boundary_candidate_count": 0, "pending_count": pending, "final_records_count": 0,
            "score_increased_count": 0, "evidence_refs": [],
        }

    monkeypatch.setattr(cli, "observe_experiment", fake_observer)
    code, run_dir = cli.run_agent("run", _task(tmp_path), registry=FakeRegistry(), max_rounds=2)

    manifest = json.loads((run_dir / "session_manifest.json").read_text(encoding="utf-8"))
    events = (run_dir / "agent_events.jsonl").read_text(encoding="utf-8")
    # The continuation actually re-executed the pipeline tool instead of
    # replaying the checkpointed result.
    assert calls == ["run_full_loop", "run_full_loop"]
    assert len(observations) == 2
    assert manifest["plan_revision"] == 2
    assert manifest["status"] == "completed"
    assert manifest["terminal_reason"] == "no_pending_branches"
    assert "session_round_started" in events
    assert "ledger_entry_superseded" in events
    assert code == 0


def test_continuation_prefers_the_resume_entry_point_when_it_is_allowed(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr("agent_runtime.executor.validate_published_artifact", lambda *_a, **_k: (True, "ok"))
    calls = []
    observations = []
    exp_dir = tmp_path / "experiments" / "day" / "exp"

    class FakeRegistry(ToolRegistry):
        def __init__(self):
            pass

        def check_environment(self, _task):
            return {"tool": "check_environment", "ok": True, "ready": True, "return_code": 0, "recoverable": False}

        def run_full_loop(self, _task, _env):
            calls.append("run_full_loop")
            exp_dir.mkdir(parents=True, exist_ok=True)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "recoverable": False, "experiment_dir": str(exp_dir)}

        def resume_full_loop(self, task, _env):
            calls.append(f"resume_full_loop:{task.resume_exp_dir}:{task.resume_start_round}")
            return {"tool": "resume_full_loop", "ok": True, "return_code": 0, "recoverable": False, "experiment_dir": str(exp_dir), "resume_start_round": task.resume_start_round}

    def fake_observer(*_args, **kwargs):
        observations.append(1)
        (Path(kwargs["run_dir"]) / "agent_observation.json").write_text("{}\n", encoding="utf-8")
        pending = 2 if len(observations) == 1 else 0
        return {
            "status": "observed", "manifest_status": "ok", "target_reached": False,
            "boundary_candidate_count": 0, "pending_count": pending, "final_records_count": 0,
            "score_increased_count": 0, "evidence_refs": [],
        }

    monkeypatch.setattr(cli, "observe_experiment", fake_observer)
    task = _task(tmp_path, allowed_tools=["check_environment", "run_full_loop", "resume_full_loop", "observe_experiment", "write_agent_report"])
    code, run_dir = cli.run_agent("run", task, registry=FakeRegistry(), max_rounds=2)

    manifest = json.loads((run_dir / "session_manifest.json").read_text(encoding="utf-8"))
    # Round 2 resumed this Session's own experiment directory (start_round
    # defaults to 1) instead of starting another fresh experiment.
    assert calls == ["run_full_loop", f"resume_full_loop:{exp_dir}:1"]
    assert manifest["plan_revision"] == 2
    assert manifest["status"] == "completed"
    assert code == 0


def test_resume_session_continuation_supersedes_the_ledger_and_reexecutes(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr("agent_runtime.executor.validate_published_artifact", lambda *_a, **_k: (True, "ok"))
    calls = []
    observations = []
    exp_dir = tmp_path / "experiments" / "day" / "exp"
    exp_dir.mkdir(parents=True)

    class FakeRegistry(ToolRegistry):
        def __init__(self):
            pass

        def resume_full_loop(self, _task, _env):
            calls.append("resume_full_loop")
            return {"tool": "resume_full_loop", "ok": True, "return_code": 0, "recoverable": False, "experiment_dir": str(exp_dir), "resume_start_round": 2}

    def fake_observer(*_args, **kwargs):
        observations.append(1)
        (Path(kwargs["run_dir"]) / "agent_observation.json").write_text("{}\n", encoding="utf-8")
        return {
            "status": "observed", "manifest_status": "ok", "target_reached": False,
            "boundary_candidate_count": 0, "pending_count": 1, "final_records_count": 0,
            "score_increased_count": 0, "evidence_refs": [],
        }

    monkeypatch.setattr(cli, "observe_experiment", fake_observer)
    task = _task(tmp_path, input_file="", resume_exp_dir="experiments/day/exp", resume_start_round=2,
                 allowed_tools=["resume_full_loop", "observe_experiment", "write_agent_report"])
    code, run_dir = cli.run_agent("run", task, registry=FakeRegistry(), max_rounds=2)

    manifest = json.loads((run_dir / "session_manifest.json").read_text(encoding="utf-8"))
    events = (run_dir / "agent_events.jsonl").read_text(encoding="utf-8")
    # Without ledger supersession the second resume would be a replayed
    # idempotent result and pending work could never advance.
    assert calls == ["resume_full_loop", "resume_full_loop"]
    assert manifest["plan_revision"] == 2
    assert "ledger_entry_superseded" in events
    assert code == 0


def test_supersede_tool_ledger_removes_only_side_effecting_entries(tmp_path):
    from agent_runtime.executor import supersede_tool_ledger

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    ledger = {
        "run-key": {"tool": "run_full_loop", "ok": True},
        "observe-key": {"tool": "observe_experiment", "ok": True},
    }
    (run_dir / "tool_idempotency.json").write_text(json.dumps(ledger), encoding="utf-8")

    removed = supersede_tool_ledger(run_dir, reason="control_loop_continue:run_pipeline")

    surviving = json.loads((run_dir / "tool_idempotency.json").read_text(encoding="utf-8"))
    assert removed == ["run-key"]
    assert set(surviving) == {"observe-key"}
    assert "ledger_entry_superseded" in (run_dir / "agent_events.jsonl").read_text(encoding="utf-8")
    # A missing or unreadable ledger never blocks the control loop silently.
    assert supersede_tool_ledger(tmp_path / "missing", reason="x") == []
    (run_dir / "tool_idempotency.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(ExecutorError):
        supersede_tool_ledger(run_dir, reason="x")


def test_rollback_reapplies_the_previous_revision_and_records_it(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_runtime.executor.validate_published_artifact", lambda *_a, **_k: (True, "ok"))
    run_dir = tmp_path / "run"
    state = initialize_state(run_dir, run_id="agent_x", mode="run")
    first = write_plan_revision(run_dir, state, _full_plan("plan_first"))
    second = write_plan_revision(run_dir, state, _full_plan("plan_second"))
    assert second["replan_context"]["replaces_plan_path"].endswith("plan_r001.json")

    executor = Executor(
        task=_task(tmp_path), plan=second, registry=object(), run_dir=run_dir, state=state,
        observe=lambda *_a, **_k: {}, update_state=_update,
        rollback=lambda: write_plan_revision(run_dir, state, first, trigger_reason="rollback_from:plan_second"),
    )
    rolled_back = executor.rollback_to_previous_revision(reason="score_increased is negative gain")

    assert rolled_back["plan_id"] == "plan_first"
    assert rolled_back["plan_revision"] == 3
    events = (run_dir / "agent_events.jsonl").read_text(encoding="utf-8")
    assert "rollback_completed" in events
    timeline = (run_dir / "agent_observation_timeline.jsonl").read_text(encoding="utf-8")
    assert '"type": "rollback_completed"' in timeline


def test_rollback_is_reported_when_no_previous_revision_exists(tmp_path):
    run_dir = tmp_path / "run"
    state = initialize_state(run_dir, run_id="agent_y", mode="run")
    plan = write_plan_revision(run_dir, state, _full_plan("plan_only"))
    executor = Executor(
        task=_task(tmp_path), plan=plan, registry=object(), run_dir=run_dir, state=state,
        observe=lambda *_a, **_k: {}, update_state=_update, rollback=lambda: None,
    )
    assert executor.rollback_to_previous_revision(reason="nothing to roll back") is None


# --------------------------------------------------------------------------- O-7 session resume


def _fake_run_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr("agent_runtime.executor.validate_published_artifact", lambda *_a, **_k: (True, "ok"))
    calls = []

    class FakeRegistry(ToolRegistry):
        def __init__(self):
            pass

        def check_environment(self, _task):
            calls.append("check_environment")
            return {"tool": "check_environment", "ok": True, "ready": True, "return_code": 0, "recoverable": False}

        def run_full_loop(self, _task, _env):
            calls.append("run_full_loop")
            exp_dir = tmp_path / "experiments" / "day" / "exp"
            exp_dir.mkdir(parents=True, exist_ok=True)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "recoverable": False, "experiment_dir": str(exp_dir)}

    def fake_observer(*_args, **kwargs):
        (Path(kwargs["run_dir"]) / "agent_observation.json").write_text("{}\n", encoding="utf-8")
        return {
            "status": "observed", "manifest_status": "ok", "target_reached": False,
            "boundary_candidate_count": 1, "pending_count": 0, "final_records_count": 1,
            "score_increased_count": 0, "evidence_refs": [],
        }

    monkeypatch.setattr(cli, "observe_experiment", fake_observer)
    return FakeRegistry, calls


def test_resume_session_reuses_the_run_dir_and_consumes_the_checkpoint(tmp_path, monkeypatch):
    FakeRegistry, calls = _fake_run_environment(monkeypatch, tmp_path)
    first_code, run_dir = cli.run_agent("run", _task(tmp_path), registry=FakeRegistry())
    first_manifest = json.loads((run_dir / "session_manifest.json").read_text(encoding="utf-8"))
    assert first_manifest["status"] in cli.RESUMABLE_SESSION_STATUSES
    revision_before = first_manifest["plan_revision"]
    calls.clear()

    second_code, resumed_dir = cli.run_agent(
        "run", _task(tmp_path), registry=FakeRegistry(), resume_session_dir=run_dir
    )

    assert resumed_dir == run_dir
    manifest = json.loads((run_dir / "session_manifest.json").read_text(encoding="utf-8"))
    events = (run_dir / "agent_events.jsonl").read_text(encoding="utf-8")
    # The confirmed checkpoint was consumed instead of restarting the Session.
    assert manifest["plan_revision"] == revision_before
    assert "session_resumed" in events
    assert "plan_revision_reused" in events
    assert "step_skipped" in events
    assert calls == []
    assert second_code == first_code


def test_resume_session_rejects_an_unknown_or_finished_session(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    missing = tmp_path / "agent_runs" / "day" / "agent_nope"
    missing.mkdir(parents=True)
    code, returned = cli.run_agent("run", _task(tmp_path), registry=object(), resume_session_dir=missing)
    assert code == 2
    assert returned == missing.resolve()

    run_dir = tmp_path / "agent_runs" / "day" / "agent_done"
    state = initialize_state(run_dir, run_id="agent_done", mode="run")
    _update(run_dir, state, status="completed")
    code, _ = cli.run_agent("run", _task(tmp_path), registry=object(), resume_session_dir=run_dir)
    assert code == 2


# --------------------------------------------------------------------------- O-8 / O-9


def test_plan_revision_is_owned_by_the_persistence_boundary(tmp_path):
    plan = build_plan(_task(tmp_path), command="run")
    assert "plan_revision" not in plan

    run_dir = tmp_path / "run"
    state = initialize_state(run_dir, run_id="agent_z", mode="run")
    stored = write_plan_revision(run_dir, state, plan)
    assert isinstance(stored["plan_revision"], int) and stored["plan_revision"] >= 1
    on_disk = json.loads(Path(state["current_plan_path"]).read_text(encoding="utf-8"))
    assert on_disk["plan_revision"] == stored["plan_revision"]


def test_unsupported_execution_scope_is_reported_from_one_single_source(tmp_path):
    assert SUPPORTED_EXECUTION_SCOPES == {"full_iteration"}
    task = _task(tmp_path, execution_scope="debug_generation_only")
    plan = build_plan(task, command="run")

    assert any("debug_generation_only" in reason for reason in plan["blocked_reasons"])
    with pytest.raises(PolicyViolation, match="full_iteration"):
        validate_plan(task, plan)


# --------------------------------------------------------------------------- R-2 budgets


def test_planner_emits_a_real_step_budget_that_the_executor_enforces(tmp_path):
    plan = build_plan(_task(tmp_path), command="run")
    assert all(step["budget_limit"]["max_tool_calls"] == 1 for step in plan["steps"])

    calls = []

    class Registry:
        def run_full_loop(self, task, env):
            calls.append(env)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0}

    executor = Executor(
        task=_task(tmp_path), plan={"plan_id": "plan-a", "env_overrides": {}}, registry=Registry(),
        run_dir=tmp_path / "run", state={"completed_step_ids": []}, observe=lambda *_a, **_k: {}, update_state=_update,
    )
    executor.execute_step(_step("run_full_loop", budget_limit={"max_tool_calls": 1}))
    with pytest.raises(ExecutorError, match="step budget exhausted"):
        executor.execute_step(
            _step("run_full_loop", arguments={"search_max_depth": 3}, budget_limit={"max_tool_calls": 1})
        )
    assert len(calls) == 1


def test_step_budget_rejects_a_non_positive_limit(tmp_path):
    executor = Executor(
        task=_task(tmp_path), plan={"plan_id": "plan-a", "env_overrides": {}}, registry=object(),
        run_dir=tmp_path / "run", state={"completed_step_ids": []}, observe=lambda *_a, **_k: {}, update_state=_update,
    )
    with pytest.raises(ExecutorError, match="max_tool_calls"):
        executor.execute_step(_step("check_environment", budget_limit={"max_tool_calls": 0}))


# --------------------------------------------------------------------------- R-4 degraded resume


def test_a_missing_resume_snapshot_is_recorded_as_an_explicit_degradation(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    task = _task(tmp_path)
    snapshot, _context, _path, audit = cli._memory_runtime(task, preferred_snapshot_id="MSNAP-missing")

    assert audit["memory_mode"] == "degraded_missing_snapshot"
    assert audit["memory_degraded"] is True
    assert audit["original_memory_snapshot_id"] == "MSNAP-missing"
    assert snapshot["memory_snapshot_id"] != "MSNAP-missing"


def test_degraded_resume_is_audited_in_the_session_manifest(tmp_path, monkeypatch):
    FakeRegistry, _calls = _fake_run_environment(monkeypatch, tmp_path)
    _code, run_dir = cli.run_agent("run", _task(tmp_path), registry=FakeRegistry())

    # Force a resume whose frozen snapshot no longer exists.
    manifest = json.loads((run_dir / "session_manifest.json").read_text(encoding="utf-8"))
    manifest["status"] = "suspended"
    manifest["memory_snapshot_id"] = "MSNAP-gone"
    (run_dir / "session_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "agent_run_state.json").write_text(json.dumps(manifest), encoding="utf-8")

    cli.run_agent("run", _task(tmp_path), registry=FakeRegistry(), resume_session_dir=run_dir)
    resumed = json.loads((run_dir / "session_manifest.json").read_text(encoding="utf-8"))

    assert resumed["memory_mode"] == "degraded_missing_snapshot"
    assert resumed["memory_degraded"] is True
    assert resumed["original_memory_snapshot_id"] == "MSNAP-gone"
    assert "the frozen memory snapshot MSNAP-gone is unavailable" in resumed["memory_degraded_reason"]
    assert "memory_snapshot_degraded" in (run_dir / "agent_events.jsonl").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- R-5 configuration


def test_configuration_defects_are_not_disguised_as_system_faults(tmp_path):
    class Registry:
        def check_environment(self, task):
            raise ValueError("input_file must be a string")

    executor = Executor(
        task=_task(tmp_path), plan={"plan_id": "plan-a", "env_overrides": {}}, registry=Registry(),
        run_dir=tmp_path / "run", state={"completed_step_ids": []}, observe=lambda *_a, **_k: {}, update_state=_update,
    )
    result = executor.execute_step(_step("check_environment", expected_outputs=["environment_checked"]))

    assert result["ok"] is False
    assert result["failure_category"] == "configuration_error"
    assert result["recoverable"] is False

    decision = decide_next_action(_task(tmp_path), {"status": "observed"}, tool_results=[result])
    assert decision["action"] == "blocked"
    assert decision["terminal_reason"] == "configuration_error"
    assert decision["requires_human_review"] is True
    assert decision["recovery_action"] == ACTION_STOP_AND_REPORT


def test_a_system_failure_stays_a_system_failure(tmp_path):
    class Registry:
        def check_environment(self, task):
            raise OSError("disk is offline")

    executor = Executor(
        task=_task(tmp_path), plan={"plan_id": "plan-a", "env_overrides": {}}, registry=Registry(),
        run_dir=tmp_path / "run", state={"completed_step_ids": []}, observe=lambda *_a, **_k: {}, update_state=_update,
    )
    result = executor.execute_step(_step("check_environment", expected_outputs=["environment_checked"]))
    assert result["failure_category"] == "fatal_system_error"


# --------------------------------------------------------------------------- R-6 side effects


def test_side_effecting_tools_do_not_retry_when_a_success_already_exists(tmp_path, monkeypatch):
    attempts = []
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        attempts.append(1)
        return __import__("subprocess").CompletedProcess(args[0], 1, stdout="", stderr="request timed out")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner, sleeper=lambda _: None)
    result = registry.run_full_loop(_task(tmp_path), {"EXP_ROOT": str(tmp_path / "experiments")}, allow_retry=False)

    assert len(attempts) == 1
    assert result["ok"] is False
    events = (tmp_path / "run" / "agent_events.jsonl").read_text(encoding="utf-8")
    assert "tool_retry_withheld" in events


def test_allow_retry_withholds_replay_of_a_completed_side_effect(tmp_path):
    state = {"completed_step_ids": []}
    executor = Executor(
        task=_task(tmp_path), plan={"plan_id": "plan-a", "env_overrides": {}}, registry=object(),
        run_dir=tmp_path / "run", state=state, observe=lambda *_a, **_k: {}, update_state=_update,
    )
    loop_spec = get_tool_spec("run_full_loop")
    observe_spec = get_tool_spec("observe_experiment")

    # Read-only tools keep their retry policy unconditionally.
    assert executor._allow_retry(observe_spec) is True
    # No progress yet: the declared retry policy still applies.
    assert executor._allow_retry(loop_spec) is True

    executor.ledger["k"] = {"ok": True, "tool": "run_full_loop"}
    assert executor._allow_retry(loop_spec) is False

    executor.ledger.clear()
    executor.state["experiment_dir"] = str(tmp_path / "experiments" / "day" / "exp")
    assert executor._allow_retry(loop_spec) is False


def test_observation_timeline_has_a_single_writer(tmp_path):
    run_dir = tmp_path / "run"
    observation = rollback_completed_observation(from_plan_id="plan_a", to_plan_id="plan_b", reason="rolled back")
    written = record_observations(run_dir, [observation])

    assert written[0]["type"] == "rollback_completed"
    timeline = (run_dir / "agent_observation_timeline.jsonl").read_text(encoding="utf-8")
    assert '"source_tool": "rollback"' in timeline
    assert "observation_created" in (run_dir / "agent_events.jsonl").read_text(encoding="utf-8")
    # The contract gate runs for every producer, not only for executor steps.
    with pytest.raises(Exception):
        record_observations(run_dir, [{"type": "not_a_registered_type", "source_tool": "x"}])


def test_recipe_table_covers_every_declared_failure_category():
    from agent_runtime.recovery import system_failure_categories

    assert set(system_failure_categories()) == {
        "retryable_system_error", "fatal_system_error", "configuration_error",
    }
    assert all(recipe.max_attempts >= 0 for recipe in RECIPES)


def test_a_session_bootstrap_freezes_memory_without_inventing_content(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    store = GlobalMemoryStore(tmp_path, initialize=False)
    assert store.db_path.exists() is False

    snapshot, context, _path, audit = cli._memory_runtime(_task(tmp_path))

    assert store.db_path.is_file()
    assert snapshot["memory_snapshot_id"].startswith("MSNAP-")
    # An empty global memory is reported as such, never as fabricated strategies.
    assert snapshot["card_versions"] == {}
    assert context["cards"] == []
    assert context["mode"] == "no_global_memory"
    assert audit["memory_degraded"] is False
