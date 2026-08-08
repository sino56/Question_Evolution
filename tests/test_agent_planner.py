import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.planner import build_plan
from agent_runtime.policy import PolicyViolation, validate_plan
from agent_runtime.task import parse_agent_task


def make_task(tmp_path, **overrides):
    raw = {
        "goal": "find score-drop boundaries",
        "input_file": "data/data.jsonl",
        "allowed_tools": ["check_environment", "run_full_loop", "observe_experiment", "write_agent_report"],
    }
    raw.update(overrides)
    return parse_agent_task(raw, project_root=tmp_path)


def test_dry_run_plan_selects_branch_search_and_safe_steps(tmp_path):
    task = make_task(tmp_path)
    plan = build_plan(task, command="dry-run")
    assert plan["selected_search_mode"] == "multi_operator_branch"
    assert [step["tool"] for step in plan["steps"]] == ["check_environment", "run_full_loop", "observe_experiment", "write_agent_report"]
    assert plan["env_overrides"]["INPUT_FILE"].endswith("data.jsonl")
    validate_plan(task, plan)


def test_auto_mode_selects_vertical_for_composition_goal(tmp_path):
    task = make_task(tmp_path, goal="尝试两算子叠加后的二次进化")
    assert build_plan(task, command="dry-run")["selected_search_mode"] == "multi_operator_vertical_stack"


def test_review_plan_never_contains_execution_tools(tmp_path):
    task = make_task(
        tmp_path,
        goal="复盘已有实验",
        input_file="",
        review_mode="report_only",
        resume_exp_dir="experiments/example",
        allowed_tools=["observe_experiment", "write_agent_report"],
    )
    plan = build_plan(task, command="review")
    assert [step["tool"] for step in plan["steps"]] == ["observe_experiment", "write_agent_report"]
    validate_plan(task, plan)


def test_plan_without_required_tool_is_explicitly_blocked(tmp_path):
    task = make_task(tmp_path, allowed_tools=["observe_experiment", "write_agent_report"])
    plan = build_plan(task, command="run")
    assert plan["blocked_reasons"]
    with pytest.raises(PolicyViolation, match="at least one step"):
        validate_plan(task, {**plan, "steps": []})


def test_model_assisted_plan_uses_schema_checked_result_or_fallback(tmp_path):
    task = make_task(tmp_path, planning_mode="model_assisted")
    deterministic = build_plan(make_task(tmp_path), command="dry-run")
    accepted = build_plan(task, command="dry-run", context_pack={"goal": "short"}, model_client=lambda _: deterministic)
    assert accepted["planner_source"] == "model_assisted"
    fallback = build_plan(task, command="dry-run", model_client=lambda _: {"not": "a plan"})
    assert fallback["planner_source"] == "deterministic"
    assert fallback["model_fallback_reason"] == "SchemaValidationError"
