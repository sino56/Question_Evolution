import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import question_evolution_agent as cli
from agent_runtime.task import parse_agent_task
from agent_runtime.tools import ToolRegistry


def test_dry_run_writes_task_plan_and_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps({"goal": "find boundaries", "input_file": "data/data.jsonl", "allowed_tools": ["check_environment", "run_full_loop", "observe_experiment", "write_agent_report"]}), encoding="utf-8")
    assert cli.main(["dry-run", "--task", str(task_path)]) == 0
    run_dir = Path(capsys.readouterr().out.strip().split(": ")[-1])
    assert (run_dir / "agent_task.json").exists()
    assert (run_dir / "agent_plan.json").exists()
    assert json.loads((run_dir / "agent_run_state.json").read_text(encoding="utf-8"))["status"] == "completed"


def test_review_never_invokes_subprocess_tools(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    exp = tmp_path / "experiments" / "day" / "exp"
    exp.mkdir(parents=True)
    task = parse_agent_task({"goal": "review", "review_mode": "report_only", "resume_exp_dir": "experiments/day/exp", "allowed_tools": ["observe_experiment", "write_agent_report"]}, project_root=tmp_path)
    code, run_dir = cli.run_agent("review", task)
    assert code == 0
    assert (run_dir / "global_review_report.md").exists()
    assert (run_dir / "optimization_proposals.jsonl").exists()


def test_run_stops_after_preflight_failure_without_starting_loop(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    task = parse_agent_task({"goal": "run", "input_file": "data/data.jsonl", "allowed_tools": ["check_environment", "run_full_loop", "observe_experiment", "write_agent_report"]}, project_root=tmp_path)

    class FakeRegistry(ToolRegistry):
        def __init__(self):
            pass
        def check_environment(self, task):
            return {"tool": "check_environment", "ok": False, "ready": False, "return_code": 2, "recoverable": False}
        def run_full_loop(self, task, env):
            raise AssertionError("run_loop must not start after failed preflight")

    code, run_dir = cli.run_agent("run", task, registry=FakeRegistry())
    assert code == 2
    assert json.loads((run_dir / "agent_run_state.json").read_text(encoding="utf-8"))["status"] == "blocked"
