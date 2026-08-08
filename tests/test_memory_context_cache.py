from agent_runtime.context import build_context_pack
from agent_runtime.context_cache import memory_context_key
from agent_runtime.context_layers import normalize_memory_context
from agent_runtime.task import parse_agent_task


def test_memory_context_key_normalizes_query_and_invalidates_snapshot():
    first = memory_context_key(memory_snapshot_id="MSNAP-one", normalized_query="  Traffic   Necessity ", retrieval_config_version="v1", top_k=3)
    second = memory_context_key(memory_snapshot_id="MSNAP-one", normalized_query="traffic necessity", retrieval_config_version="v1", top_k=3)
    changed = memory_context_key(memory_snapshot_id="MSNAP-two", normalized_query="traffic necessity", retrieval_config_version="v1", top_k=3)
    assert first == second
    assert changed != first


def test_memory_cards_are_reproducibly_sorted_and_keep_audit_references():
    raw = {
        "memory_snapshot_id": "MSNAP-one",
        "retrieval_config_version": "v1",
        "top_k": 3,
        "cards": [
            {"card_id": "z", "version": 1, "retrieval_score": 2, "evidence_refs": [{"artifact_ref": "z#1"}]},
            {"card_id": "a", "version": 2, "retrieval_score": 2, "evidence_refs": [{"artifact_ref": "a#1"}]},
            {"card_id": "b", "version": 1, "retrieval_score": 3, "evidence_refs": [{"artifact_ref": "b#1"}]},
        ],
    }
    normalized = normalize_memory_context(raw, query="traffic necessity")
    assert [card["card_id"] for card in normalized["cards"]] == ["b", "a", "z"]
    assert all(card["evidence_refs"] for card in normalized["cards"])
    assert normalized["memory_context_key"] == memory_context_key(memory_snapshot_id="MSNAP-one", normalized_query="traffic necessity", retrieval_config_version="v1", top_k=3)


def test_memory_snapshot_changes_the_context_cache_key(tmp_path):
    task = parse_agent_task({"goal": "review", "input_file": "data/data.jsonl"}, project_root=tmp_path)
    first = build_context_pack(task, memory_context={"memory_snapshot_id": "MSNAP-one", "top_k": 1, "cards": []})
    second = build_context_pack(task, memory_context={"memory_snapshot_id": "MSNAP-two", "top_k": 1, "cards": []})
    assert first["memory_context"]["memory_context_key"] != second["memory_context"]["memory_context_key"]
    assert first["context_cache"]["context_cache_key"] != second["context_cache"]["context_cache_key"]
