import json

from agent_runtime.context import build_context_pack
from agent_runtime.context_layers import TOOL_REGISTRY_ORDER
from agent_runtime.task import parse_agent_task
from schema_validation import load_schema, validate_instance


def _task(tmp_path):
    return parse_agent_task(
        {
            "goal": "find a stable reasoning boundary",
            "input_file": "data/data.jsonl",
            "allowed_tools": list(reversed(TOOL_REGISTRY_ORDER)),
            "allow_global_memory_read": True,
        },
        project_root=tmp_path,
    )


def _pack(tmp_path, **runtime):
    return build_context_pack(
        _task(tmp_path),
        observation={"memory_summary": {"observed": 1}, "evidence_refs": [{"artifact_ref": "round_1/effect.jsonl#1"}]},
        memory_context={
            "memory_snapshot_id": "MSNAP-one",
            "retrieval_config_version": "retrieval-v1",
            "top_k": 2,
            "cards": [
                {"card_id": "b", "version": 1, "retrieval_score": 1, "evidence_refs": [{"artifact_ref": "b#1"}]},
                {"card_id": "a", "version": 2, "retrieval_score": 1, "evidence_refs": [{"artifact_ref": "a#1"}]},
            ],
        },
        runtime_state=runtime,
    )


def test_context_pack_v2_classifies_dynamic_fields_and_keeps_legacy_fields(tmp_path):
    pack = _pack(
        tmp_path,
        agent_run_id="run-1",
        agent_run_dir="C:/runs/run-1",
        experiment_dir="C:/runs/exp-1",
        current_step_id="observe",
        generated_at="2026-08-08T00:00:00Z",
        stderr_summary="temporary failure detail",
    )
    assert pack["context_schema_version"] == "context-pack-v2"
    assert pack["goal"] == pack["task_context"]["goal"]
    assert pack["available_tools"] == list(TOOL_REGISTRY_ORDER)
    assert [card["card_id"] for card in pack["memory_context"]["cards"]] == ["a", "b"]
    assert "agent_run_dir" not in json.dumps(pack["stable_prefix"], ensure_ascii=False)
    assert pack["dynamic_tail"]["agent_run_dir"] == "C:/runs/run-1"
    assert pack["dynamic_tail"]["stderr_summary"] == "temporary failure detail"
    # Tests run from a temporary project root, so load the repository schema.
    import pathlib

    schema_path = pathlib.Path(__file__).resolve().parents[1] / "schemas" / "context_pack_v2.schema.json"
    validate_instance(pack, load_schema(schema_path), schema_dir=schema_path.parent)


def test_observations_and_task_tool_input_order_do_not_change_prefixes(tmp_path):
    first_task = _task(tmp_path)
    second_task = parse_agent_task(
        {"goal": first_task.goal, "input_file": "data/data.jsonl", "allowed_tools": list(TOOL_REGISTRY_ORDER)},
        project_root=tmp_path,
    )
    first = build_context_pack(first_task, observation={"observation": "first"})
    second = build_context_pack(second_task, observation={"observation": "second", "stderr_summary": "transient"})
    assert first["stable_prefix"] == second["stable_prefix"]
    assert first["context_cache"]["stable_prefix_hash"] == second["context_cache"]["stable_prefix_hash"]
    assert first["context_cache"]["snapshot_prefix_hash"] == second["context_cache"]["snapshot_prefix_hash"]
    assert first["dynamic_tail"]["observation_summary"] != second["dynamic_tail"]["observation_summary"]


def test_large_dynamic_observation_keeps_the_v2_contract(tmp_path):
    pack = build_context_pack(_task(tmp_path), observation={"large": "x" * 100000})
    assert pack["context_schema_version"] == "context-pack-v2"
    assert pack["dynamic_tail"]["observation_summary"]["truncated"] is True
