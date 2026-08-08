import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.events import append_event
from agent_runtime.policy import PolicyViolation, validate_plan
from agent_runtime.state import create_run_dir, initialize_state, write_task
from agent_runtime.task import TaskValidationError, parse_agent_task


def task(**overrides):
    value = {
        "goal": "Find score-drop candidates",
        "input_file": "data/data.jsonl",
        "execution_scope": "full_iteration",
        "allowed_tools": ["check_environment", "run_full_loop", "observe_experiment", "write_agent_report"],
    }
    value.update(overrides)
    return value


def test_task_requires_goal_input_and_resume_fields(tmp_path):
    with pytest.raises(TaskValidationError, match="goal"):
        parse_agent_task({}, project_root=tmp_path)
    with pytest.raises(TaskValidationError, match="input_file"):
        parse_agent_task({"goal": "new run"}, project_root=tmp_path)
    with pytest.raises(TaskValidationError, match="resume_start_round"):
        parse_agent_task({"goal": "resume", "resume_exp_dir": "experiments/e", "allowed_tools": []}, project_root=tmp_path)


@pytest.mark.parametrize("field,value", [("search_mode", "invalid"), ("execution_scope", "report_only"), ("review_mode", "later")])
def test_task_rejects_illegal_enums(tmp_path, field, value):
    raw = task()
    raw[field] = value
    with pytest.raises(TaskValidationError, match=field):
        parse_agent_task(raw, project_root=tmp_path)


def test_state_and_events_are_created_without_api_access(tmp_path):
    run_dir = create_run_dir(tmp_path, run_id="agent_test")
    state = initialize_state(run_dir, run_id="agent_test", mode="dry-run")
    write_task(run_dir, task())
    event = append_event(
        run_dir / "agent_events.jsonl",
        "tool_completed",
        {"tool": "check_environment", "return_code": 0, "stdout_summary": "api_key=secret-value https://private.example/v1"},
    )
    assert state["status"] == "created"
    assert (run_dir / "agent_task.json").exists()
    assert "secret-value" not in json.dumps(event)
    assert "private.example" not in json.dumps(event)


def test_policy_rejects_prompt_mutation_and_unregistered_plan_tool(tmp_path):
    with pytest.raises(TaskValidationError, match="prompt mutation"):
        parse_agent_task(task(allow_prompt_mutation=True), project_root=tmp_path)
    parsed = parse_agent_task(task(), project_root=tmp_path)
    with pytest.raises(PolicyViolation, match="unregistered"):
        validate_plan(parsed, {"env_overrides": {}, "steps": [{"tool": "edit_prompt"}]})
