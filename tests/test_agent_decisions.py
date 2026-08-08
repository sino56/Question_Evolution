import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.decisions import decide_next_action, write_decision
from agent_runtime.task import parse_agent_task


def task(tmp_path, **changes):
    raw = {"goal": "find boundaries", "input_file": "data/data.jsonl", "allowed_tools": []}
    raw.update(changes)
    return parse_agent_task(raw, project_root=tmp_path)


def observation(**changes):
    value = {"status": "observed", "manifest_status": "not_checked", "target_reached": False, "pending_count": 1, "final_records_count": 0, "score_increased_count": 0, "evidence_refs": []}
    value.update(changes)
    return value


def test_target_and_pending_stop_conditions(tmp_path):
    assert decide_next_action(task(tmp_path), observation(target_reached=True))["action"] == "stop_and_report"
    result = decide_next_action(task(tmp_path), observation(pending_count=0))
    assert result["action"] == "stop_and_report"
    assert result["reason"] == "no pending branches remain"


def test_damaged_manifest_and_unrecoverable_tool_failure_block(tmp_path):
    assert decide_next_action(task(tmp_path), observation(manifest_status="damaged"))["action"] == "blocked"
    failed = decide_next_action(task(tmp_path), observation(), tool_results=[{"tool": "run_full_loop", "ok": False, "recoverable": False}])
    assert failed["action"] == "blocked"


def test_review_stops_and_decision_is_written(tmp_path):
    reviewed = task(tmp_path, input_file="", review_mode="report_only", resume_exp_dir="experiments/exp")
    decision = decide_next_action(reviewed, observation(evidence_refs=[{"path": "x"}]))
    assert decision["action"] == "stop_and_report"
    write_decision(tmp_path / "run", decision)
    row = json.loads((tmp_path / "run" / "agent_decisions.jsonl").read_text(encoding="utf-8"))
    assert row["action"] == "stop_and_report"
