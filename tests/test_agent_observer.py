import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.observer import observe_experiment
from agent_runtime.reporter import write_agent_report, write_global_review_artifacts


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def test_observer_summarizes_branch_statuses_and_pending(tmp_path):
    exp = tmp_path / "experiments" / "day" / "exp"
    write_jsonl(exp / "round_1" / "search" / "branch_results.jsonl", [
        {"branch_status": "boundary_candidate", "branch_id": "b1", "operator_id": "O10"},
        {"branch_status": "score_increased", "branch_id": "b2", "operator_id": "O11"},
        {"branch_status": "validation_failed", "branch_id": "b3", "operator_id": "O12"},
    ])
    write_jsonl(exp / "round_1" / "state_updated.jsonl", [{"search_state": {"operator_plan": [{"status": "pending"}, {"status": "completed"}], "termination_reason": "candidate_list_exhausted"}}])
    (exp / "memory").mkdir(parents=True)
    (exp / "memory" / "operator_memory_bank.jsonl").write_text("{}\n", encoding="utf-8")
    observation = observe_experiment(exp, run_dir=tmp_path / "agent-run", boundary_target=1)
    assert observation["status"] == "observed"
    assert observation["boundary_candidate_count"] == 1
    assert observation["score_increased_count"] == 1
    assert observation["validation_failed_count"] == 1
    assert observation["pending_count"] == 1
    assert observation["target_reached"] is True
    assert observation["termination_reason"] == "candidate_list_exhausted"
    assert (tmp_path / "agent-run" / "agent_observation.json").exists()


def test_observer_reports_missing_files_and_blocks_corrupt_json(tmp_path):
    exp = tmp_path / "experiment"
    exp.mkdir()
    observation = observe_experiment(exp)
    assert observation["status"] == "observed"
    assert "final/final_scored.jsonl" in observation["missing_artifacts"]
    broken = exp / "round_1" / "state_updated.jsonl"
    broken.parent.mkdir()
    broken.write_text("{bad json}\n", encoding="utf-8")
    assert observe_experiment(exp)["status"] == "blocked"


def test_reports_remain_proposal_only(tmp_path):
    observation = {"boundary_candidate_count": 1, "score_increased_count": 2, "not_applicable_count": 3, "validation_failed_count": 0, "branch_error_count": 0, "target_reached": False, "main_issue": "score_increased", "missing_artifacts": [], "evidence_refs": [{"path": "round_1/branch_results.jsonl"}]}
    report = write_agent_report(tmp_path, task={"goal": "review"}, state={}, plan={"selected_search_mode": "multi_operator_branch", "selected_execution_scope": "full_iteration", "budget": {}}, observation=observation, tool_results=[], decision={"action": "stop_and_report", "reason": "done"})
    assert "确认有效边界" not in report.read_text(encoding="utf-8")
    artifacts = write_global_review_artifacts(tmp_path, observation)
    proposal = json.loads(artifacts["optimization_proposals"].read_text(encoding="utf-8"))
    assert proposal["status"] == "needs_human_review"
