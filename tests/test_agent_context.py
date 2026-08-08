import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.context import build_context_pack
from agent_runtime.context_prompt import cached_prompt_prefix
from agent_runtime.task import parse_agent_task


def test_context_pack_is_short_and_does_not_include_sensitive_values(tmp_path):
    task = parse_agent_task({"goal": "review", "input_file": "data/data.jsonl", "allowed_tools": []}, project_root=tmp_path)
    context = build_context_pack(task, observation={"memory_summary": {"token": "private", "large": "x" * 10000}}, max_chars=300)
    assert "private" not in str(context)
    assert len(str(context)) < 1000


def test_default_context_pack_exposes_a_cache_safe_v2_prompt(tmp_path):
    task = parse_agent_task({"goal": "review", "input_file": "data/data.jsonl", "allowed_tools": []}, project_root=tmp_path)
    context = build_context_pack(task, runtime_state={"agent_run_dir": "runs/one", "current_step_id": "observe"})
    assert context["context_cache"]["context_cache_key"].startswith("sha256:")
    assert "runs/one" not in cached_prompt_prefix(context)
