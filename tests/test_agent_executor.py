import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.executor import Executor
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
