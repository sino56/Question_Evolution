from agent_runtime.context import build_context_pack
from agent_runtime.context_prompt import assemble_context_prompt, cached_prompt_prefix
from agent_runtime.task import parse_agent_task


def _task(tmp_path):
    return parse_agent_task({"goal": "plan a bounded review", "input_file": "data/data.jsonl"}, project_root=tmp_path)


def test_prompt_prefix_is_stable_when_dynamic_tail_changes(tmp_path):
    task = _task(tmp_path)
    first = build_context_pack(task, runtime_state={"agent_run_dir": "runs/one", "current_step_id": "one"})
    second = build_context_pack(task, runtime_state={"agent_run_dir": "runs/two", "current_step_id": "two", "stderr_summary": "transient"})
    assert cached_prompt_prefix(first) == cached_prompt_prefix(second)
    assert assemble_context_prompt(first).index("[stable_prefix]") < assemble_context_prompt(first).index("[dynamic_tail]")
    assert "runs/two" in assemble_context_prompt(second)
