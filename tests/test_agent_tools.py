import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.task import parse_agent_task
from agent_runtime.tools import TOOL_SPECS, ToolRegistry, classify_system_failure, get_tool_spec


def task(tmp_path, **changes):
    raw = {
        "goal": "find score-drop candidates",
        "input_file": "data/data.jsonl",
        "allowed_tools": ["check_environment", "run_full_loop", "resume_full_loop", "observe_experiment", "write_agent_report"],
    }
    raw.update(changes)
    return parse_agent_task(raw, project_root=tmp_path)


def test_check_environment_parses_ready_json_and_records_event(tmp_path):
    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=json.dumps({"ready_for_real_stage06_e2e": True}), stderr="")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner)
    result = registry.check_environment(task(tmp_path))
    assert result["ok"] is True
    assert result["ready"] is True
    events = (tmp_path / "run" / "agent_events.jsonl").read_text(encoding="utf-8")
    assert '"tool_started"' in events and '"tool_completed"' in events


def test_run_full_loop_locates_experiment_and_redacts_streams(tmp_path, monkeypatch):
    experiment = tmp_path / "experiments" / "2026-08-08" / "exp"
    experiment.mkdir(parents=True)
    (experiment / "summary.txt").write_text("summary", encoding="utf-8")
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=f"本次实验目录: {experiment}\nkey=top-secret", stderr="")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner)
    result = registry.run_full_loop(task(tmp_path), {"EXP_ROOT": str(tmp_path / "experiments"), "SEARCH_MODE": "multi_operator_branch"})
    assert Path(result["experiment_dir"]) == experiment.resolve()
    assert "top-secret" not in (tmp_path / "run" / "agent_events.jsonl").read_text(encoding="utf-8")


def test_resume_uses_current_registered_resume_entrypoint(tmp_path, monkeypatch):
    experiment = tmp_path / "experiments" / "day" / "exp"
    experiment.mkdir(parents=True)
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")
    calls = []

    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner)
    result = registry.resume_full_loop(task(tmp_path, input_file="", resume_exp_dir="experiments/day/exp", resume_start_round=2), {"EXP_ROOT": str(tmp_path / "experiments")})
    assert calls[0][-2:] == ["--resume-exp-dir", str(experiment.resolve())]
    assert result["resume_start_round"] == 2


def test_registered_tools_publish_phase3_contract_metadata():
    assert set(TOOL_SPECS) == {"check_environment", "run_full_loop", "resume_full_loop", "observe_experiment", "write_agent_report"}
    spec = get_tool_spec("run_full_loop")
    assert spec.kind == "composite"
    assert spec.version
    assert spec.input_schema and spec.output_schema
    assert spec.idempotency_key_fields
    assert spec.timeout_seconds > 0
    assert spec.retry_policy.max_attempts >= 1
    assert "final/final_scored.jsonl" in spec.expected_artifacts
    assert "pipeline_completed" in spec.observation_types


def test_tool_result_is_redacted_and_retryable_failures_are_classified(tmp_path, monkeypatch):
    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")

    def runner(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 1, stdout="", stderr="rate limit api_key=secret-value")

    registry = ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner, sleeper=lambda _: None)
    result = registry.run_full_loop(task(tmp_path), {"EXP_ROOT": str(tmp_path / "experiments")})
    assert result["recoverable"] is True
    assert result["failure_category"] == "retryable_system_error"
    assert "secret-value" not in json.dumps(result)
    assert classify_system_failure("manifest corrupted") == ("fatal_system_error", False)
