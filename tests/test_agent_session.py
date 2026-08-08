import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import question_evolution_agent as cli
from agent_runtime.planner import build_plan
from agent_runtime.policy import PolicyViolation, validate_plan
from agent_runtime.decisions import decide_next_action
from agent_runtime.state import initialize_state, load_state, update_state, write_plan_revision
from agent_runtime.task import parse_agent_task
from agent_runtime.tools import ToolRegistry


def make_task(tmp_path, **changes):
    raw = {
        "goal": "find score-drop boundaries",
        "input_file": "data/data.jsonl",
        "allowed_tools": ["check_environment", "run_full_loop", "resume_full_loop", "observe_experiment", "write_agent_report"],
    }
    raw.update(changes)
    return parse_agent_task(raw, project_root=tmp_path)


def test_session_manifest_is_backward_compatible_and_revisions_are_immutable(tmp_path):
    run_dir = tmp_path / "agent_runs" / "2026-08-08" / "agent_session"
    run_dir.mkdir(parents=True)
    state = initialize_state(run_dir, run_id="agent_session", mode="run", root_goal="find boundaries", budgets={"max_search_steps": 25})
    update_state(run_dir, state, status="context_ready")

    first = write_plan_revision(run_dir, state, {"plan_id": "first", "steps": []})
    second = write_plan_revision(run_dir, state, {"plan_id": "second", "steps": []}, trigger_reason="new observation")
    manifest = load_state(run_dir)

    assert manifest["agent_run_id"] == manifest["session_id"] == "agent_session"
    assert manifest["root_goal"] == "find boundaries"
    assert manifest["plan_revision"] == 2
    assert Path(manifest["current_plan_path"]).name == "plan_r002.json"
    assert (run_dir / "agent_run_state.json").exists()
    assert (run_dir / "session_manifest.json").exists()
    assert first["replan_context"]["replaces_plan_path"] is None
    assert second["replan_context"]["replaces_plan_path"].endswith("plan_r001.json")
    events = (run_dir / "agent_events.jsonl").read_text(encoding="utf-8")
    assert "session_status_changed" in events
    assert events.count("plan_revision_created") == 2


def test_planner_emits_task_round_recovery_and_review_plan_kinds(tmp_path):
    new_plan = build_plan(make_task(tmp_path), command="dry-run")
    assert new_plan["plan_kind"] == "task_plan"
    assert new_plan["plan_layers"] == ["task_plan", "round_plan"]
    assert {"intent", "tool_name", "arguments", "preconditions", "expected_outputs", "success_condition", "business_failure_action", "system_failure_action", "budget_limit", "depends_on"}.issubset(new_plan["steps"][0])

    resumed = build_plan(make_task(tmp_path, input_file="", resume_exp_dir="experiments/day/exp", resume_start_round=1), command="resume")
    assert resumed["plan_kind"] == "recovery_plan"
    assert resumed["plan_layers"] == ["recovery_plan"]

    reviewed = build_plan(make_task(tmp_path, input_file="", review_mode="report_only", resume_exp_dir="experiments/day/exp", allowed_tools=["observe_experiment", "write_agent_report"]), command="review")
    assert reviewed["plan_kind"] == "review_plan"
    assert reviewed["plan_layers"] == ["review_plan"]


def test_plan_validator_rejects_bypass_retry_and_protected_mutation(tmp_path):
    task = make_task(tmp_path)
    plan = build_plan(task, command="run")
    validate_plan(task, plan)

    bypass = deepcopy(plan)
    bypass["steps"] = [step for step in bypass["steps"] if step["tool"] != "check_environment"]
    with pytest.raises(PolicyViolation, match="missing step|environment check"):
        validate_plan(task, bypass)

    retry = deepcopy(plan)
    retry["steps"][1]["business_failure_action"] = "retry_run_full_loop"
    with pytest.raises(PolicyViolation, match="business failures"):
        validate_plan(task, retry)

    missing_output = deepcopy(plan)
    missing_output["steps"][1]["expected_outputs"] = ["experiment_dir"]
    with pytest.raises(PolicyViolation, match="required output"):
        validate_plan(task, missing_output)

    protected = deepcopy(plan)
    protected["steps"][1]["arguments"]["prompt_path"] = "prompts/unsafe.md"
    with pytest.raises(PolicyViolation, match="protected formal assets"):
        validate_plan(task, protected)


def test_budget_exhaustion_has_an_explicit_non_success_terminal_reason(tmp_path):
    decision = decide_next_action(
        make_task(tmp_path),
        {"status": "observed", "manifest_status": "not_checked", "termination_reason": "max_search_steps_budget_exhausted"},
    )
    assert decision["action"] == "stop_and_report"
    assert decision["terminal_reason"] == "max_search_steps_budget_exhausted"
    assert decision["requires_human_review"] is False


def test_automatic_boundary_result_suspends_session_for_manual_review(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    task = make_task(tmp_path)

    class FakeRegistry(ToolRegistry):
        def __init__(self):
            pass

        def check_environment(self, _task):
            return {"tool": "check_environment", "ok": True, "ready": True, "return_code": 0, "recoverable": False}

        def run_full_loop(self, _task, _env):
            exp_dir = tmp_path / "experiments" / "day" / "exp"
            exp_dir.mkdir(parents=True, exist_ok=True)
            return {"tool": "run_full_loop", "ok": True, "return_code": 0, "recoverable": False, "experiment_dir": str(exp_dir)}

    monkeypatch.setattr(
        cli,
        "observe_experiment",
        lambda *_args, **_kwargs: {
            "status": "observed", "manifest_status": "not_checked", "target_reached": True,
            "boundary_candidate_count": 1, "pending_count": 0, "final_records_count": 1,
            "score_increased_count": 0, "evidence_refs": [],
        },
    )
    code, run_dir = cli.run_agent("run", task, registry=FakeRegistry())
    manifest = json.loads((run_dir / "session_manifest.json").read_text(encoding="utf-8"))

    assert code == 0
    assert manifest["status"] == "suspended"
    assert manifest["terminal_reason"] == "manual_review_required"
    assert manifest["requires_manual_review"] is True
    assert manifest["manual_review_status"] == "pending"
