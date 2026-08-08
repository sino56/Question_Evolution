import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.policy import PolicyViolation, validate_plan
from agent_runtime.task import parse_agent_task


def test_report_only_cannot_start_pipeline(tmp_path):
    task = parse_agent_task(
        {
            "goal": "review an experiment",
            "review_mode": "report_only",
            "resume_exp_dir": "experiments/example",
            "allowed_tools": ["run_full_loop", "observe_experiment", "write_agent_report"],
        },
        project_root=tmp_path,
    )
    with pytest.raises(PolicyViolation, match="report_only"):
        validate_plan(task, {"env_overrides": {}, "steps": [{"tool": "run_full_loop"}]})


def test_non_full_scope_cannot_invoke_full_loop(tmp_path):
    task = parse_agent_task(
        {
            "goal": "debug generation",
            "input_file": "data/data.jsonl",
            "execution_scope": "debug_generation_only",
            "allowed_tools": ["run_full_loop"],
        },
        project_root=tmp_path,
    )
    with pytest.raises(PolicyViolation, match="full_iteration"):
        validate_plan(task, {"env_overrides": {}, "steps": [{"tool": "run_full_loop"}]})
