from agent_runtime.multi_agent.coordinator import run_post_experiment_review
from agent_runtime.multi_agent.planning_advisors import validate_candidate_plan
from agent_runtime.multi_agent.memory_advisors import build_strategy_card_draft
from agent_runtime.reporter import write_agent_report
from agent_runtime.task import parse_agent_task


def test_candidate_plan_must_pass_normal_plan_validator(tmp_path):
    task = parse_agent_task({"goal": "run", "input_file": "data/data.jsonl"}, project_root=__import__("pathlib").Path.cwd())
    ok, reason = validate_candidate_plan(task, {"plan_id": "bad", "steps": []})
    assert not ok
    assert reason


def test_memory_draft_cannot_become_active_and_missing_evidence_needs_review():
    import pytest

    with pytest.raises(ValueError):
        build_strategy_card_draft(strategy_id="s", evidence_refs=[], status="active")
    draft = build_strategy_card_draft(strategy_id="s", evidence_refs=[])
    assert draft["status"] == "needs_human_review"


def test_post_experiment_advisors_are_advisory_and_reported(tmp_path):
    observation = {"experiment_dir": "exp", "status": "observed", "main_issue": "score_increased", "status_counts": {"score_increased": 1}, "score_increased_count": 1, "not_applicable_count": 0, "validation_failed_count": 0, "branch_error_count": 0, "boundary_candidate_count": 0, "target_reached": False, "missing_artifacts": [], "evidence_refs": [{"path": "round_1/effect_analysis.jsonl"}], "observations": []}
    review = run_post_experiment_review(tmp_path, task={"goal": "review"}, state={"agent_run_id": "run", "memory_snapshot_id": "mem"}, plan={"plan_id": "plan", "budget": {}}, observation=observation)
    path = write_agent_report(tmp_path, task={"goal": "review"}, state={"status": "completed"}, plan={"budget": {}}, observation=observation, tool_results=[], decision={"action": "stop_and_report", "reason": "done"}, multi_agent_review=review)
    content = path.read_text(encoding="utf-8")
    assert "Multi-agent review advice" in content
    assert review["merge"]["advisory_only"] is True
