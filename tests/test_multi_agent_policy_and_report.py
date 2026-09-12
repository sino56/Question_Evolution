from agent_runtime.multi_agent.coordinator import run_post_experiment_review
from agent_runtime.multi_agent.memory_advisors import build_strategy_card_draft
from agent_runtime.reporter import write_agent_report
from agent_runtime.task import parse_agent_task


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


def test_report_renders_the_human_review_precheck_aid(tmp_path):
    precheck = {
        "evidence_pack_hash": "sha256:abc",
        "advisor_records": [{"advisor_id": "boundary_quality", "status": "completed"}, {"advisor_id": "review_synthesis", "status": "completed"}],
        "merge": {"accepted_advice": [{"advisor_id": "boundary_quality"}], "policy_rejections": [], "conflicts": []},
    }
    path = write_agent_report(
        tmp_path, task={"goal": "g"}, state={"status": "suspended", "requires_manual_review": True},
        plan={"budget": {}}, observation={"status": "observed", "evidence_refs": []}, tool_results=[],
        decision={"action": "stop_and_report", "reason": "review needed"}, human_review_precheck=precheck,
    )
    content = path.read_text(encoding="utf-8")
    assert "Human review precheck (advisory aid)" in content
    assert "boundary_quality:completed" in content
    assert "no candidate is confirmed" in content


def test_human_review_precheck_stage_fails_open(tmp_path):
    from agent_runtime.multi_agent.coordinator import run_human_review_precheck

    common = {"task": {"goal": "review"}, "state": {"agent_run_id": "run", "memory_snapshot_id": "m"}, "plan": {"plan_id": "p"}, "observation": {"experiment_dir": "x", "status": "observed", "evidence_refs": [{"path": "x"}]}}
    result = run_human_review_precheck(tmp_path, **common)
    assert result["advisor_records"], "the wired precheck stage must actually run advisors"
    assert result["advisor_records"][-1]["advisor_id"] == "review_synthesis"
    degraded = run_human_review_precheck(
        tmp_path, task={"goal": "review"}, state={"agent_run_id": "run", "memory_snapshot_id": "m"},
        plan={"plan_id": "p"}, observation={"status": "blocked", "blocked_reason": "no artifacts"},
    )
    # Fail-open: a degraded run returns an explicit, empty advisory result.
    assert isinstance(degraded, dict)
