"""Stage-6b regressions: report genre, judge mounting, advisor independence.

Each test maps to one finding of
``docs/Agent改造方案/Agent_Harness_代码审查报告_2026-09-11.md``:

- V-6 the offline Global Judge is mounted on the review path, proposal-only.
- V-8 the realised advisor independence is recorded and labelled.
- X-5 the report carries the design §20 output contract and content-derived ids.
"""

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.global_judge import mount_judge_for_review
from agent_runtime.multi_agent.coordinator import advisor_independence
from agent_runtime.observer import final_output_contract
from agent_runtime.reporter import write_agent_report, write_global_review_artifacts


def _final_record(**overrides):
    record = {
        "sample_id": "sample-1",
        "question_evolved": "题目在引入联合条件后更难。",
        "score_rate": 0.4,
        "round0_score_summary": {"score_rate": 0.8},
        "operator_route": {"selected_operator": "O12", "operator_candidates": ["O12", "O10"]},
        "meta_info": {"question_evolution_metadata": {"operator_used": "O12"}},
    }
    record.update(overrides)
    return record


def _experiment(tmp_path, records):
    experiment = tmp_path / "experiments" / "day" / "exp"
    target = experiment / "final" / "final_scored.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records), encoding="utf-8")
    (experiment / "experiment_statistics.json").write_text(
        json.dumps({"total_cost": 1.5, "request_count": 12, "judge_instability_rate": 0.0}), encoding="utf-8"
    )
    # A branch record with an attributable failure signal is what makes the
    # Judge emit a real proposal (an "evidence_insufficient" row is excluded by
    # ``proposals_from_diagnoses`` by design).
    branch = experiment / "round_1" / "branch_results.jsonl"
    branch.parent.mkdir(parents=True, exist_ok=True)
    branch.write_text(
        json.dumps({
            "sample_id": "sample-1", "branch_status": "score_increased", "operator_used": "O12",
            "score_rate": 0.9, "sample_signature": {"scene_family": "traffic", "question_form": "necessity"},
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return experiment


# --------------------------------------------------------------------------- X-5


def test_final_output_contract_is_derived_from_published_records(tmp_path):
    records = [
        _final_record(),
        _final_record(sample_id="sample-2", score_rate=0.9, round0_score_summary={"score_rate": 0.7}),
    ]
    contract = final_output_contract(records, statistics={"total_cost": 1.5, "request_count": 12})

    assert contract["available"] is True
    assert contract["best_sample_id"] == "sample-1"  # the largest score drop
    assert contract["score_before"] == 0.8
    assert contract["score_after"] == 0.4
    assert contract["score_delta"] == pytest.approx(-0.4)
    assert contract["direction"] == "score_drop"
    assert contract["operator_path"] == ["O12"]
    assert contract["cost_summary"] == {"total_cost": 1.5, "request_count": 12}
    # No judge metric was published, so stability must be reported as unknown
    # rather than silently claimed as "stable".
    assert contract["judge_stability"]["status"] == "not_reported"
    assert contract["best_question"].startswith("题目在引入联合条件后更难")

    reported = final_output_contract(
        records, statistics={"judge_instability_rate": 0.0}
    )
    assert reported["judge_stability"] == {"status": "stable", "judge_instability_rate": 0.0}
    unstable = final_output_contract(
        records, statistics={"judge_instability_count": 2}
    )
    assert unstable["judge_stability"]["status"] == "unstable"


def test_final_output_contract_reports_absence_instead_of_guessing():
    assert final_output_contract([])["available"] is False
    assert final_output_contract([{"sample_id": "x"}])["available"] is False
    assert "no final scored record was published" in final_output_contract([])["reason"]
    assert "no published final record" in final_output_contract([{"sample_id": "x"}])["reason"]


def test_final_output_contract_recovers_a_round0_baseline_from_trials(tmp_path):
    record = {
        "sample_id": "sample-9",
        "score_rate": 0.5,
        "round0_score_trials": [{"score_rate": 0.9}, {"score_rate": 0.7}],
    }
    contract = final_output_contract([record], statistics={})

    assert contract["score_before"] == pytest.approx(0.8)
    assert contract["score_delta"] == pytest.approx(-0.3)


def test_agent_report_renders_the_output_contract(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    observation = {
        "final_output_contract": final_output_contract([_final_record()], statistics={"total_cost": 1.5}),
        "evidence_refs": [{"artifact_ref": "x#1"}],
    }
    write_agent_report(
        run_dir,
        task={"goal": "g", "input_file": "data/d.jsonl"},
        state={"status": "suspended", "plan_revision": 1, "memory_mode": "no_global_memory"},
        plan={"selected_search_mode": "single_branch", "selected_execution_scope": "full_iteration", "budget": {}},
        observation=observation,
        tool_results=[],
        decision={"action": "stop_and_report", "reason": "r"},
    )
    report = (run_dir / "agent_report.md").read_text(encoding="utf-8")

    assert "## Final output contract" in report
    assert "Score delta: -0.4 (score_drop)" in report
    assert "Operator path: O12" in report
    assert "Cost summary:" in report


def test_proposal_id_is_derived_from_content(tmp_path):
    first_dir = tmp_path / "run-a"
    second_dir = tmp_path / "run-b"
    for directory in (first_dir, second_dir):
        directory.mkdir(parents=True, exist_ok=True)

    write_global_review_artifacts(first_dir, {"main_issue": "issue-a", "evidence_refs": [{"artifact_ref": "x"}]})
    write_global_review_artifacts(second_dir, {"main_issue": "issue-b", "evidence_refs": [{"artifact_ref": "x"}]})
    first = json.loads((first_dir / "optimization_proposals.jsonl").read_text(encoding="utf-8"))
    second = json.loads((second_dir / "optimization_proposals.jsonl").read_text(encoding="utf-8"))

    assert first["proposal_id"].startswith("proposal_")
    assert first["proposal_id"] != "proposal_001"
    assert first["proposal_id"] != second["proposal_id"]

    # The same content reproduces the same identity.
    write_global_review_artifacts(first_dir, {"main_issue": "issue-a", "evidence_refs": [{"artifact_ref": "x"}]})
    repeated = json.loads((first_dir / "optimization_proposals.jsonl").read_text(encoding="utf-8"))
    assert repeated["proposal_id"] == first["proposal_id"]


# --------------------------------------------------------------------------- V-8


def test_advisor_independence_labels_a_deterministic_only_review():
    records = [
        {"advisor_id": "A1", "status": "completed", "selected_model": "local-deterministic-advisor", "model_tier": "extract_low_cost"},
        {"advisor_id": "A2", "status": "completed", "selected_model": "local-deterministic-advisor", "model_tier": "reasoning_high"},
    ]
    summary = advisor_independence(records)

    assert summary["interpretation"] == "deterministic_checklist"
    assert summary["model_backed_advisors"] == 0
    assert summary["deterministic_advisors"] == 2
    assert "must not be cited as cross-validation" in summary["statement"]


def test_advisor_independence_labels_a_model_backed_review():
    records = [
        {"advisor_id": "A1", "status": "completed", "selected_model": "gpt-x", "model_tier": "reasoning_high"},
        {"advisor_id": "A2", "status": "completed", "selected_model": "local-deterministic-advisor"},
    ]
    summary = advisor_independence(records)

    assert summary["interpretation"] == "partially_model_backed"
    assert summary["model_backed_advisors"] == 1
    assert "checklist only" in summary["statement"]


def test_advisor_independence_handles_an_empty_review():
    summary = advisor_independence([])
    assert summary["interpretation"] == "not_run"
    assert summary["advisor_runs"] == 0


def test_agent_report_renders_the_independence_section(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_agent_report(
        run_dir,
        task={"goal": "g", "input_file": "data/d.jsonl"},
        state={"status": "completed"},
        plan={"selected_search_mode": "single_branch", "selected_execution_scope": "full_iteration", "budget": {}},
        observation={},
        tool_results=[],
        multi_agent_review={
            "evidence_pack_hash": "sha256:x",
            "advisor_records": [{"advisor_id": "A1", "selected_model": "local-deterministic-advisor"}],
            "merge": {"accepted_advice": [], "policy_rejections": [], "conflicts": []},
            "independence": advisor_independence([{"advisor_id": "A1", "status": "completed", "selected_model": "local-deterministic-advisor"}]),
        },
    )
    report = (run_dir / "agent_report.md").read_text(encoding="utf-8")

    assert "## Advisor independence" in report
    assert "Interpretation: deterministic_checklist" in report


# --------------------------------------------------------------------------- V-6


def test_review_mounts_the_global_judge_as_proposal_only(tmp_path):
    experiment = _experiment(tmp_path, [_final_record()])
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = mount_judge_for_review(
        run_dir, experiment_dir=experiment, snapshot_id="MSNAP-test", project_root=tmp_path
    )

    assert summary["status"] == "completed"
    assert summary["proposal_count"] >= 1
    assert "Proposal-only" in summary["action_limit"]
    assert Path(summary["report_path"]).is_file()
    assert Path(summary["evidence_pack_path"]).is_file()
    # Outputs stay inside the governed workspace.
    governance = (tmp_path / "memory_global" / "global_judge").resolve()
    assert Path(summary["report_path"]).resolve().relative_to(governance)
    assert (governance / "judge_runs.jsonl").is_file()
    # The experiment artifacts are untouched by the advisory run.
    assert not (experiment / "optimization_proposals.jsonl").exists()


def test_a_rejected_evidence_pack_degrades_without_raising(tmp_path):
    empty = tmp_path / "experiments" / "day" / "empty"
    empty.mkdir(parents=True, exist_ok=True)
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)

    summary = mount_judge_for_review(run_dir, experiment_dir=empty, snapshot_id="MSNAP-test", project_root=tmp_path)

    assert summary["status"] == "degraded"
    assert "evidence pack rejected" in summary["reason"]
    assert not (tmp_path / "memory_global" / "global_judge" / "judge_runs.jsonl").exists()


def test_review_report_records_the_judge_summary(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_agent_report(
        run_dir,
        task={"goal": "g", "input_file": "data/d.jsonl"},
        state={"status": "suspended"},
        plan={"selected_search_mode": "single_branch", "selected_execution_scope": "full_iteration", "budget": {}},
        observation={},
        tool_results=[],
        global_judge={"status": "degraded", "reason": "evidence pack rejected: no published artifact"},
    )
    report = (run_dir / "agent_report.md").read_text(encoding="utf-8")

    assert "## Global Judge (offline, proposal-only)" in report
    assert "evidence pack rejected" in report
