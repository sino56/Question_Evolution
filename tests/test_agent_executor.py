import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.executor import Executor, ExecutorError
from agent_runtime.task import parse_agent_task
from schema_validation import load_schema, validate_instance


def _task(tmp_path):
    return parse_agent_task(
        {"goal": "find boundaries", "input_file": "data/data.jsonl", "allowed_tools": ["check_environment", "run_full_loop"]},
        project_root=tmp_path,
    )


def _step(tool, **changes):
    value = {
        "step_id": f"step_{tool}", "tool_name": tool, "tool": tool, "arguments": {},
        "preconditions": [], "expected_outputs": ["environment_checked"] if tool == "check_environment" else ["experiment_dir", "final/final_scored.jsonl"],
        "budget_limit": {}, "depends_on": [], "stop_if_failed": True,
    }
    value.update(changes)
    return value


def _update(_run_dir, state, **changes):
    state.update(changes)
    return state


def test_executor_records_checkpoint_and_reuses_a_completed_idempotent_call(tmp_path):
    calls = []

    class Registry:
        def check_environment(self, task):
            calls.append(task.input_file)
            return {"tool": "check_environment", "ok": True, "ready": True, "return_code": 0}

    state = {"completed_step_ids": []}
    plan = {"plan_id": "plan-1", "env_overrides": {}}
    executor = Executor(task=_task(tmp_path), plan=plan, registry=Registry(), run_dir=tmp_path / "run", state=state, observe=lambda *_args, **_kwargs: {}, update_state=_update)
    first = executor.execute_step(_step("check_environment"))
    assert first["ok"] is True
    assert state["completed_step_ids"] == ["step_check_environment"]

    second = Executor(task=_task(tmp_path), plan=plan, registry=Registry(), run_dir=tmp_path / "run", state=state, observe=lambda *_args, **_kwargs: {}, update_state=_update).execute_step(_step("check_environment"))
    assert second["reused"] is True
    assert len(calls) == 1
    events = (tmp_path / "run" / "agent_events.jsonl").read_text(encoding="utf-8")
    assert "checkpoint_confirmed" in events and "tool_reused" in events and "observation_created" in events
    assert (tmp_path / "run" / "agent_observation_timeline.jsonl").exists()
    schema_path = ROOT / "schemas" / "agent_tool_result.schema.json"
    validate_instance(first, load_schema(schema_path), schema_dir=schema_path.parent)


def test_executor_marks_missing_formal_artifact_as_fatal_failure(tmp_path):
    exp = tmp_path / "experiments" / "day" / "exp"
    exp.mkdir(parents=True)

    class Registry:
        def run_full_loop(self, task, env):
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "experiment_dir": str(exp)}

    state = {"completed_step_ids": []}
    result = Executor(task=_task(tmp_path), plan={"plan_id": "plan-1", "env_overrides": {}}, registry=Registry(), run_dir=tmp_path / "run", state=state, observe=lambda *_args, **_kwargs: {}, update_state=_update).execute_step(_step("run_full_loop"))
    assert result["ok"] is False
    assert result["failure_category"] == "fatal_system_error"
    assert "artifact_missing" in result["artifact_validation"]
    assert state["completed_step_ids"] == []


def test_local_tools_do_not_consume_the_model_call_budget(tmp_path):
    calls = []

    class Registry:
        def check_environment(self, task):
            calls.append(task.input_file)
            return {"tool": "check_environment", "ok": True, "ready": True, "return_code": 0}

    state = {"completed_step_ids": []}
    task = parse_agent_task(
        {"goal": "find boundaries", "input_file": "data/data.jsonl", "budget_limits": {"model_calls": 1}, "allowed_tools": ["check_environment"]},
        project_root=tmp_path,
    )
    executor = Executor(task=task, plan={"plan_id": "plan-1", "env_overrides": {}}, registry=Registry(), run_dir=tmp_path / "run", state=state, observe=lambda *_args, **_kwargs: {}, update_state=_update)
    executor.execute_step(_step("check_environment"))
    ledger = json.loads((tmp_path / "run" / "budget_ledger.json").read_text(encoding="utf-8"))
    # ``model_calls`` counts model-billed invocations only; a local preflight
    # must not consume it (report R-2).
    assert ledger["consumed"].get("model_calls", {}) == {}
    assert any(event["event_type"] == "tool_call_observed" for event in ledger["events"])


def test_model_billed_tools_consume_and_enforce_the_model_call_budget(tmp_path):
    calls = []

    class Registry:
        def run_full_loop(self, task, env):
            calls.append(task.input_file)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0}

    state = {"completed_step_ids": []}
    task = parse_agent_task(
        {"goal": "find boundaries", "input_file": "data/data.jsonl", "budget_limits": {"model_calls": 1}, "allowed_tools": ["run_full_loop"]},
        project_root=tmp_path,
    )
    executor = Executor(task=task, plan={"plan_id": "plan-1", "env_overrides": {}}, registry=Registry(), run_dir=tmp_path / "run", state=state, observe=lambda *_args, **_kwargs: {}, update_state=_update)
    executor.execute_step(_step("run_full_loop", expected_outputs=[]))
    ledger = json.loads((tmp_path / "run" / "budget_ledger.json").read_text(encoding="utf-8"))
    assert ledger["consumed"]["model_calls"]["pool:unallocated"] == 1

    # The hard limit is enforced for a *different* logical invocation.
    with pytest.raises(ExecutorError):
        executor.execute_step(_step("run_full_loop", arguments={"search_max_depth": 2}, expected_outputs=[]))
    assert len(calls) == 1


def test_model_calls_are_reconciled_with_the_pipeline_measurement(tmp_path):
    """The pre-run unit charge is replaced by the pipeline's measured spend."""

    class Registry:
        def run_full_loop(self, task, env):
            experiment_dir = tmp_path / "experiments" / "day" / "exp"
            experiment_dir.mkdir(parents=True, exist_ok=True)
            (experiment_dir / "experiment_statistics.json").write_text(
                json.dumps({"model_calls": 7, "total_cost": 0.5}), encoding="utf-8"
            )
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "experiment_dir": str(experiment_dir)}

    task = parse_agent_task(
        {"goal": "find boundaries", "input_file": "data/data.jsonl", "budget_limits": {"model_calls": 100}, "allowed_tools": ["run_full_loop"]},
        project_root=tmp_path,
    )
    executor = Executor(task=task, plan={"plan_id": "plan-1", "env_overrides": {}}, registry=Registry(), run_dir=tmp_path / "run", state={"completed_step_ids": []}, observe=lambda *_args, **_kwargs: {}, update_state=_update)
    executor.execute_step(_step("run_full_loop", expected_outputs=[]))

    ledger = json.loads((tmp_path / "run" / "budget_ledger.json").read_text(encoding="utf-8"))
    assert ledger["consumed"]["model_calls"]["pool:unallocated"] == 7
    events = (tmp_path / "run" / "agent_events.jsonl").read_text(encoding="utf-8")
    assert "model_calls_reconciled" in events


def test_model_calls_reconciliation_never_fails_the_completed_tool(tmp_path):
    """A ledger refusal after a successful run must not flip the result."""

    class Registry:
        def run_full_loop(self, task, env):
            experiment_dir = tmp_path / "experiments" / "day" / "exp"
            experiment_dir.mkdir(parents=True, exist_ok=True)
            (experiment_dir / "experiment_statistics.json").write_text(json.dumps({"model_calls": 9}), encoding="utf-8")
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "experiment_dir": str(experiment_dir)}

    task = parse_agent_task(
        {"goal": "find boundaries", "input_file": "data/data.jsonl", "budget_limits": {"model_calls": 2}, "allowed_tools": ["run_full_loop"]},
        project_root=tmp_path,
    )
    executor = Executor(task=task, plan={"plan_id": "plan-1", "env_overrides": {}}, registry=Registry(), run_dir=tmp_path / "run", state={"completed_step_ids": []}, observe=lambda *_args, **_kwargs: {}, update_state=_update)
    result = executor.execute_step(_step("run_full_loop", expected_outputs=[]))

    assert result["ok"] is True
    assert "model_calls_reconciliation_refused" in (tmp_path / "run" / "agent_events.jsonl").read_text(encoding="utf-8")
