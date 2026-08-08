from agent_runtime.context import build_context_pack
from agent_runtime.context_cache import canonical_json, context_cache_key
from agent_runtime.task import parse_agent_task


def _task(tmp_path):
    return parse_agent_task({"goal": "plan", "input_file": "data/data.jsonl"}, project_root=tmp_path)


def _context(tmp_path, **runtime):
    return build_context_pack(
        _task(tmp_path),
        memory_context={"memory_snapshot_id": "mem-1", "top_k": 0, "cards": []},
        runtime_state=runtime,
    )


def test_canonical_json_is_key_order_stable():
    assert canonical_json({"b": [2, 1], "a": {"y": 2, "x": 1}}) == canonical_json({"a": {"x": 1, "y": 2}, "b": [2, 1]})


def test_dynamic_state_does_not_change_stable_or_snapshot_hash(tmp_path):
    first = _context(tmp_path, agent_run_dir="runs/a", current_step_id="one", observation_summary="first")
    second = _context(tmp_path, agent_run_dir="runs/b", current_step_id="two", observation_summary="second")
    assert first["context_cache"]["stable_prefix_hash"] == second["context_cache"]["stable_prefix_hash"]
    assert first["context_cache"]["snapshot_prefix_hash"] == second["context_cache"]["snapshot_prefix_hash"]
    assert first["context_cache"]["context_cache_key"] == second["context_cache"]["context_cache_key"]
    assert first["context_cache"]["dynamic_tail_hash"] != second["context_cache"]["dynamic_tail_hash"]
    for field in ("agent_run_dir", "current_step_id", "stderr_summary", "paths"):
        assert field not in first["stable_prefix"]


def test_snapshot_and_registry_changes_invalidate_context_cache_key():
    kwargs = {
        "context_schema_version": "context-pack-v2",
        "prompt_template_version": "context-prompt-v2",
        "skill_registry_version": "skills-1",
        "tool_registry_version": "tools-1",
        "policy_snapshot_id": "policy-1",
        "prompt_snapshot_id": "prompt-1",
        "operator_snapshot_id": "operator-1",
        "memory_snapshot_id": "memory-1",
        "selected_search_mode": "single_branch",
        "selected_execution_scope": "full_iteration",
    }
    baseline = context_cache_key(**kwargs)
    for field in ("policy_snapshot_id", "operator_snapshot_id", "skill_registry_version"):
        changed = dict(kwargs)
        changed[field] = changed[field] + "-changed"
        assert context_cache_key(**changed) != baseline
