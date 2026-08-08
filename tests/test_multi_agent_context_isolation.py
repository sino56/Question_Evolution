import json

import pytest

from agent_runtime.multi_agent.advisor_context import build_advisor_context
from agent_runtime.multi_agent.advisor_registry import get_advisor
from agent_runtime.multi_agent.evidence_pack import build_evidence_pack


def _pack(tmp_path, *, snapshot="mem-1"):
    return build_evidence_pack(
        tmp_path,
        task={"goal": "review", "api_key": "secret", "base_url": "https://hidden"},
        state={"agent_run_id": "run-1", "memory_snapshot_id": snapshot},
        observation={"experiment_dir": "x", "main_issue": "score_increased", "score_increased_count": 1, "observations": [{"raw_response": "private"}], "evidence_refs": [{"path": "round_1/effect_analysis.jsonl"}]},
        plan={"plan_id": "p1"},
    )


def test_evidence_pack_redacts_secrets_and_full_model_responses(tmp_path):
    pack = _pack(tmp_path)
    serialized = json.dumps(pack, ensure_ascii=False)
    assert "secret" not in serialized
    assert "https://hidden" not in serialized
    assert "private" not in serialized
    assert (tmp_path / "multi_agent" / "evidence_pack.json").is_file()


def test_context_uses_slice_and_cache_key_binds_snapshot(tmp_path):
    spec = get_advisor("router_diagnosis")
    first = build_advisor_context(spec, _pack(tmp_path, snapshot="one"))
    second = build_advisor_context(spec, _pack(tmp_path / "other", snapshot="one"))
    changed = build_advisor_context(spec, _pack(tmp_path / "third", snapshot="two"))
    assert first["context_cache_key"] == second["context_cache_key"]
    assert first["context_cache_key"] != changed["context_cache_key"]
    assert "experiment_dir" not in first["evidence_pack_slice"]["allowed_inputs"]
    assert "experiment_dir" not in json.dumps(first["evidence_pack_slice"], ensure_ascii=False)
    assert first["advisor_context_cache"]["evidence_pack_slice_hash"] == first["evidence_pack_slice"]["evidence_pack_slice_hash"]
    assert first["advisor_context_cache"]["advisor_static_prefix_hash"].startswith("sha256:")


def test_review_advisors_cannot_continue_old_context(tmp_path):
    with pytest.raises(ValueError, match="new context"):
        build_advisor_context(get_advisor("scoring_stability"), _pack(tmp_path), mode="continue", parent_advisor_task_id="adv-old")


def test_dynamic_instruction_does_not_change_advisor_cache_key(tmp_path):
    spec = get_advisor("router_diagnosis")
    first = build_advisor_context(spec, _pack(tmp_path), dynamic_instruction="first concern")
    second = build_advisor_context(spec, _pack(tmp_path), dynamic_instruction="second concern")
    assert first["context_cache_key"] == second["context_cache_key"]
    assert first["input_hash"] != second["input_hash"]
