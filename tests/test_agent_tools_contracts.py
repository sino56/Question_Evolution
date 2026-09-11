"""Stage-3 regressions: Step arguments, idempotency, process trees, cost.

Each test maps to one finding of
``docs/Agent改造方案/Agent_Harness_代码审查报告_2026-09-11.md`` §5:

- T-1 Step ``arguments`` must actually reach the composite tools.
- T-2 the idempotency key must survive a plan revision.
- T-3 a timeout must terminate the whole process tree, not just the parent.
- T-4 the experiment directory must never be guessed from modification time.
- T-5 retry backoff must grow exponentially and stay capped.
- T-6 a structured error category must win over prose matching.
- T-7 ``cost`` must be a real measurement dimension.
- T-8 registry kwargs must be injected per declared parameter, without dead code.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.executor import Executor, ExecutorError
from agent_runtime.task import parse_agent_task
from agent_runtime.tools import (
    TOOL_SPECS,
    RetryPolicy,
    ToolRegistry,
    classify_system_failure,
    cost_estimate,
    get_tool_spec,
    run_in_process_group,
)


def _spec_with_policy(policy):
    """Swap a tool's retry policy without touching the registered contract."""

    spec = get_tool_spec("run_full_loop")
    return spec.__class__(
        spec.tool_name, spec.version, spec.kind, spec.input_schema, spec.output_schema,
        spec.side_effects, spec.idempotency_key_fields, spec.timeout_seconds, policy,
        spec.expected_artifacts, spec.observation_types, spec.cost_policy,
    )


def _task(tmp_path):
    return parse_agent_task(
        {
            "goal": "find score-drop candidates",
            "input_file": "data/data.jsonl",
            "allowed_tools": [
                "check_environment",
                "run_full_loop",
                "resume_full_loop",
                "observe_experiment",
                "write_agent_report",
            ],
        },
        project_root=tmp_path,
    )


def _step(tool, *, arguments=None, expected_outputs=None, **changes):
    value = {
        "step_id": f"step_{tool}",
        "tool_name": tool,
        "tool": tool,
        "arguments": dict(arguments or {}),
        "preconditions": [],
        "expected_outputs": list(expected_outputs if expected_outputs is not None else []),
        "budget_limit": {},
        "depends_on": [],
        "stop_if_failed": True,
    }
    value.update(changes)
    return value


def _update(_run_dir, state, **changes):
    state.update(changes)
    return state


def _executor(tmp_path, *, registry, plan=None, task=None, state=None):
    return Executor(
        task=task or _task(tmp_path),
        plan=plan if plan is not None else {"plan_id": "plan-a", "plan_revision": 1, "env_overrides": {}},
        registry=registry,
        run_dir=tmp_path / "run",
        state=state if state is not None else {"completed_step_ids": []},
        observe=lambda *_args, **_kwargs: {},
        update_state=_update,
    )


# --------------------------------------------------------------------------- T-1


def test_step_arguments_override_plan_env_overrides_for_composite_tools(tmp_path):
    seen = {}

    class Registry:
        def run_full_loop(self, task, env):
            seen.update(env)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0}

    plan = {"plan_id": "plan-a", "plan_revision": 1, "env_overrides": {"SEARCH_MODE": "single_branch", "MAX_SEARCH_STEPS": "25"}}
    step = _step("run_full_loop", arguments={"search_mode": "multi_operator_branch", "max_search_steps": 9})
    result = _executor(tmp_path, registry=Registry(), plan=plan).execute_step(step)

    assert result["ok"] is True
    assert seen["SEARCH_MODE"] == "multi_operator_branch"
    assert seen["MAX_SEARCH_STEPS"] == "9"


def test_plan_env_overrides_survive_when_a_step_declares_nothing(tmp_path):
    seen = {}

    class Registry:
        def run_full_loop(self, task, env):
            seen.update(env)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0}

    plan = {"plan_id": "plan-a", "plan_revision": 1, "env_overrides": {"SEARCH_MODE": "single_branch"}}
    _executor(tmp_path, registry=Registry(), plan=plan).execute_step(_step("run_full_loop"))

    assert seen["SEARCH_MODE"] == "single_branch"


def test_snake_case_env_argument_is_accepted(tmp_path):
    seen = {}

    class Registry:
        def run_full_loop(self, task, env):
            seen.update(env)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0}

    _executor(tmp_path, registry=Registry()).execute_step(
        _step("run_full_loop", arguments={"search_max_depth": 3})
    )

    assert seen["SEARCH_MAX_DEPTH"] == "3"


def test_unexecutable_step_argument_is_rejected_before_the_tool_runs(tmp_path):
    calls = []

    class Registry:
        def run_full_loop(self, task, env):
            calls.append(env)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0}

    executor = _executor(tmp_path, registry=Registry())
    with pytest.raises(ExecutorError) as excinfo:
        executor.execute_step(_step("run_full_loop", arguments={"max_search_step": 5}))

    assert "not executable" in str(excinfo.value)
    assert calls == []


def test_step_arguments_project_onto_the_task_for_check_environment(tmp_path):
    seen = []

    class Registry:
        def check_environment(self, task):
            seen.append(task)
            return {"tool": "check_environment", "ok": True, "ready": True, "return_code": 0}

    task = _task(tmp_path)
    executor = _executor(tmp_path, registry=Registry(), task=task)
    result = executor.execute_step(
        _step("check_environment", arguments={"input_file": "data/other.jsonl"}, expected_outputs=["environment_checked"])
    )

    assert result["ok"] is True
    assert seen and seen[0].input_file != task.input_file
    assert seen[0].input_file.endswith("other.jsonl")


def test_positional_step_arguments_are_not_environment_overrides(tmp_path):
    seen = {}

    class Registry:
        def run_full_loop(self, task, env):
            seen.update(env)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0}

    _executor(tmp_path, registry=Registry()).execute_step(
        _step("run_full_loop", arguments={"experiment_dir": "experiments/day/exp", "input_file": "data/data.jsonl"})
    )

    assert "EXPERIMENT_DIR" not in seen
    assert seen["INPUT_FILE"] == "data/data.jsonl"


# --------------------------------------------------------------------------- T-2


def test_idempotency_key_is_stable_across_plan_revisions(tmp_path):
    experiment = tmp_path / "experiments" / "day" / "exp"
    experiment.mkdir(parents=True)
    calls = []

    class Registry:
        def run_full_loop(self, task, env):
            calls.append(env)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "experiment_dir": str(experiment)}

    step = _step("run_full_loop")
    first = _executor(tmp_path, registry=Registry(), plan={"plan_id": "plan-a", "plan_revision": 1, "env_overrides": {}}).execute_step(step)
    second = _executor(tmp_path, registry=Registry(), plan={"plan_id": "plan-b", "plan_revision": 2, "env_overrides": {}}).execute_step(step)

    assert first["ok"] is True
    assert second["reused"] is True
    assert len(calls) == 1
    assert second["produced_by_plan_id"] == "plan-a"


def test_idempotency_key_changes_when_an_env_input_changes(tmp_path):
    """A step-level env parameter must invalidate reuse, not silently hit it."""

    experiment = tmp_path / "experiments" / "day" / "exp"
    experiment.mkdir(parents=True)
    calls = []

    class Registry:
        def run_full_loop(self, task, env):
            calls.append(env)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "experiment_dir": str(experiment)}

    state = {"completed_step_ids": []}
    _executor(tmp_path, registry=Registry(), state=state).execute_step(
        _step("run_full_loop", arguments={"search_max_depth": 1})
    )
    second = _executor(tmp_path, registry=Registry(), state=state).execute_step(
        _step("run_full_loop", arguments={"search_max_depth": 6})
    )

    assert len(calls) == 2
    assert second.get("reused") is None


def test_idempotency_key_is_stable_for_the_same_env_contract(tmp_path):
    experiment = tmp_path / "experiments" / "day" / "exp"
    experiment.mkdir(parents=True)
    calls = []

    class Registry:
        def run_full_loop(self, task, env):
            calls.append(env)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "experiment_dir": str(experiment)}

    state = {"completed_step_ids": []}
    step = _step("run_full_loop", arguments={"search_max_depth": 2})
    _executor(tmp_path, registry=Registry(), state=state).execute_step(step)
    second = _executor(tmp_path, registry=Registry(), state=state).execute_step(step)

    assert len(calls) == 1
    assert second["reused"] is True


# --------------------------------------------------------------------------- T-3


class _FakeProcess:
    pid = 4242

    def __init__(self):
        self.alive = True

    def poll(self):
        return None if self.alive else 0

    def communicate(self, timeout=None):
        if timeout is not None:
            raise subprocess.TimeoutExpired("cmd", timeout)
        return "", ""

    def kill(self):
        self.alive = False

    def wait(self, timeout=None):
        self.alive = False
        return 0


def test_process_group_runner_isolates_the_child_process_group(monkeypatch):
    captured = {}

    class _Completing:
        returncode = 0
        pid = 1

        def communicate(self, timeout=None):
            return "ok", ""

        def poll(self):
            return 0

        def kill(self):
            pass

        def wait(self, timeout=None):
            return 0

    def fake_popen(command, **kwargs):
        captured.update(kwargs)
        return _Completing()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    completed = run_in_process_group(["python", "x.py"], cwd=".", env={}, capture_output=True, timeout=5)

    assert completed.returncode == 0
    if os.name == "nt":
        assert captured["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert captured["start_new_session"] is True


def test_timeout_kills_the_process_tree_and_reports_the_cleanup(monkeypatch):
    commands = []

    def fake_run(command, *args, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: _FakeProcess())
    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr("agent_runtime.tools._scan_orphan_descendants", lambda _pid: "clean")

    with pytest.raises(subprocess.TimeoutExpired) as excinfo:
        run_in_process_group(["bash", "run_loop.sh"], cwd=".", env={}, capture_output=True, timeout=0.01)

    assert "process_tree_cleanup" in (excinfo.value.stderr or "")
    assert '"orphan_scan": "clean"' in (excinfo.value.stderr or "")
    if os.name == "nt":
        assert commands and commands[0][0] == "taskkill"
        assert "/T" in commands[0]
    else:
        assert commands == []


def test_timeout_cleanup_evidence_reaches_the_tool_result(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(
            args[0], 1, output="", stderr='killed\nprocess_tree_cleanup={"terminated": true, "orphan_scan": "clean"}'
        )

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner, sleeper=lambda _: None)
    result = registry.run_full_loop(_task(tmp_path), {"EXP_ROOT": str(tmp_path / "experiments")})

    assert result["ok"] is False
    assert result["failure_category"] == "retryable_system_error"
    assert "process_tree_cleanup" in result["stderr_summary"]


# --------------------------------------------------------------------------- T-4


def test_experiment_dir_is_never_guessed_from_modification_time(tmp_path, monkeypatch):
    exp_root = tmp_path / "experiments"
    stale = exp_root / "day" / "stale"
    stale.mkdir(parents=True)
    (stale / "summary.txt").write_text("old experiment", encoding="utf-8")
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout="no declared directory in this output\n", stderr="")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner)
    result = registry.run_full_loop(_task(tmp_path), {"EXP_ROOT": str(exp_root)})

    assert result["experiment_dir"] is None
    assert (tmp_path / "run" / "experiment_dir.txt").read_text(encoding="utf-8").strip() == ""


def test_experiment_dir_resolves_from_the_new_directory_set(tmp_path, monkeypatch):
    exp_root = tmp_path / "experiments"
    stale = exp_root / "day" / "stale"
    stale.mkdir(parents=True)
    fresh = exp_root / "day" / "fresh"
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        fresh.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner)
    result = registry.run_full_loop(_task(tmp_path), {"EXP_ROOT": str(exp_root)})

    assert Path(result["experiment_dir"]) == fresh.resolve()
    assert (tmp_path / "run" / "experiment_dir.txt").read_text(encoding="utf-8").strip() == str(fresh.resolve())


def test_ambiguous_experiment_dir_set_stays_unresolved(tmp_path, monkeypatch):
    exp_root = tmp_path / "experiments"
    (exp_root / "day" / "stale").mkdir(parents=True)
    first = exp_root / "day" / "first"
    second = exp_root / "day" / "second"
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        first.mkdir(parents=True, exist_ok=True)
        second.mkdir(parents=True, exist_ok=True)
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner)
    result = registry.run_full_loop(_task(tmp_path), {"EXP_ROOT": str(exp_root)})

    assert result["experiment_dir"] is None


# --------------------------------------------------------------------------- T-5


def test_retry_backoff_is_exponential_and_capped():
    policy = RetryPolicy(max_attempts=5, backoff_seconds=0.25)

    assert policy.backoff_for(1) == 0.25
    assert policy.backoff_for(2) == 0.5
    assert policy.backoff_for(3) == 1.0
    assert policy.backoff_for(4) == 2.0
    assert policy.backoff_for(20) == policy.max_backoff_seconds
    assert RetryPolicy(max_attempts=1).backoff_for(1) == 0.0


def test_execute_uses_exponential_backoff_between_retries(tmp_path, monkeypatch):
    sleeps = []
    attempts = []
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        attempts.append(1)
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="request timed out")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner, sleeper=sleeps.append)
    result = registry.run_full_loop(_task(tmp_path), {"EXP_ROOT": str(tmp_path / "experiments")})

    assert result["ok"] is False
    assert result["retry_count"] == 1
    assert len(attempts) == 2
    assert sleeps == [0.25]


def test_zero_backoff_policy_still_honours_max_attempts(tmp_path, monkeypatch):
    sleeps = []
    attempts = []
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")
    monkeypatch.setitem(TOOL_SPECS, "run_full_loop", _spec_with_policy(RetryPolicy(max_attempts=3, backoff_seconds=0.0)))

    def runner(*args, **kwargs):
        attempts.append(1)
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="request timed out")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner, sleeper=sleeps.append)
    result = registry.run_full_loop(_task(tmp_path), {"EXP_ROOT": str(tmp_path / "experiments")})

    assert len(attempts) == 3
    assert sleeps == []
    assert result["retry_count"] == 2


def test_exhausted_retries_return_a_classified_failure(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="timeout while calling the model")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner, sleeper=lambda _: None)
    result = registry.run_full_loop(_task(tmp_path), {"EXP_ROOT": str(tmp_path / "experiments")})

    assert result["ok"] is False
    assert result["failure_category"] == "retryable_system_error"
    assert result["recoverable"] is True


# --------------------------------------------------------------------------- T-6


def test_structured_error_category_wins_over_prose_matching():
    assert classify_system_failure("boom\nERROR_CATEGORY=fatal_system_error") == ("fatal_system_error", False)
    assert classify_system_failure("ERROR_CATEGORY=retryable_system_error\ndone") == ("retryable_system_error", True)
    assert classify_system_failure("ERROR_CATEGORY=tool_execution_error") == ("tool_execution_error", False)


def test_bare_schema_word_is_no_longer_treated_as_fatal():
    assert classify_system_failure("the model mentioned the schema in its answer") == ("tool_execution_error", False)
    assert classify_system_failure("schema validation failed") == ("fatal_system_error", False)
    assert classify_system_failure("manifest corrupted") == ("fatal_system_error", False)
    assert classify_system_failure("anything", timed_out=True) == ("retryable_system_error", True)


# --------------------------------------------------------------------------- T-7


def test_tool_specs_declare_a_cost_policy_and_estimate():
    assert get_tool_spec("check_environment").cost_policy == "local"
    assert get_tool_spec("run_full_loop").cost_policy == "model_billed"
    assert cost_estimate(get_tool_spec("check_environment"))["known_cost"] == 0.0
    assert cost_estimate(get_tool_spec("run_full_loop"))["unit"] == "not_reported"


def test_executor_default_cost_follows_the_tool_policy(tmp_path):
    class Registry:
        def run_full_loop(self, task, env):
            # Deliberately omits ``cost`` so the executor default applies.
            return {"tool": "run_full_loop", "ok": True, "return_code": 0}

    result = _executor(tmp_path, registry=Registry()).execute_step(_step("run_full_loop"))

    assert result["cost"]["cost_policy"] == "model_billed"
    assert result["cost"]["known_cost"] is None


def test_cost_is_backfilled_from_experiment_statistics(tmp_path, monkeypatch):
    experiment = tmp_path / "experiments" / "day" / "exp"
    experiment.mkdir(parents=True)
    (experiment / "experiment_statistics.json").write_text(
        json.dumps({"total_cost": 1.25, "request_count": 40, "model_calls": 12}),
        encoding="utf-8",
    )
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=f"本次实验目录: {experiment}\n", stderr="")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner)
    result = registry.run_full_loop(_task(tmp_path), {"EXP_ROOT": str(tmp_path / "experiments")})

    assert result["cost"]["known_cost"] == 1.25
    assert result["cost"]["measurements"]["request_count"] == 40
    assert result["cost"]["source_ref"].endswith("experiment_statistics.json")


# --------------------------------------------------------------------------- T-8


def test_registry_kwargs_are_injected_per_declared_parameter(tmp_path):
    seen = {}

    class Registry:
        # Declares tool_call_id / idempotency_key but NOT record_events.
        def check_environment(self, task, *, tool_call_id="", idempotency_key=""):
            seen.update({"tool_call_id": tool_call_id, "idempotency_key": idempotency_key})
            return {"tool": "check_environment", "ok": True, "ready": True, "return_code": 0}

    result = _executor(tmp_path, registry=Registry()).execute_step(
        _step("check_environment", expected_outputs=["environment_checked"])
    )

    assert result["ok"] is True
    assert seen["idempotency_key"] == result["idempotency_key"]
    assert seen["tool_call_id"] == result["tool_call_id"]


def test_registry_without_runtime_kwargs_is_still_supported(tmp_path):
    class Registry:
        def check_environment(self, task):
            return {"tool": "check_environment", "ok": True, "ready": True, "return_code": 0}

    result = _executor(tmp_path, registry=Registry()).execute_step(
        _step("check_environment", expected_outputs=["environment_checked"])
    )

    assert result["ok"] is True
    assert result["tool_call_id"]
