from agent_runtime.multi_agent.advice_merge import merge_advice
from agent_runtime.multi_agent.evidence_pack import build_evidence_pack


def _pack(tmp_path):
    return build_evidence_pack(tmp_path, task={"goal": "review"}, state={"agent_run_id": "run", "memory_snapshot_id": "m"}, observation={"experiment_dir": "x", "evidence_refs": [{"path": "x"}]})


def _advice(pack, advisor_id, action, *, refs=True):
    return {"advisor_id": advisor_id, "status": "completed", "summary": "advice", "input_hash": pack["evidence_pack_hash"], "snapshot_ids": pack["snapshot_ids"], "forbidden_actions_requested": [], "findings": [{"type": "router_risk", "severity": "medium", "claim": "claim", "evidence_refs": [{"path": "x"}] if refs else [], "recommended_action": action}]}


def test_forbidden_formal_mutations_are_rejected_and_logged(tmp_path):
    pack = _pack(tmp_path)
    merged = merge_advice(tmp_path, advice_items=[_advice(pack, "router_diagnosis", "modify_prompt")], evidence_pack=pack)
    assert not merged["accepted_advice"]
    assert merged["policy_rejections"][0]["reason"] == "policy_rejected"
    assert "advisor_policy_rejected" in (tmp_path / "multi_agent" / "advisor_events.jsonl").read_text(encoding="utf-8")


def test_missing_evidence_downgrades_and_conflicting_advice_is_listed(tmp_path):
    pack = _pack(tmp_path)
    merged = merge_advice(tmp_path, advice_items=[_advice(pack, "router_diagnosis", "proposed", refs=False), _advice(pack, "validation_diagnosis", "shadow")], evidence_pack=pack)
    finding = merged["accepted_advice"][0]["findings"][0]
    assert finding["recommended_action"] == "needs_human_review"
    assert merged["conflicts"]


def test_different_snapshot_advice_cannot_merge(tmp_path):
    pack = _pack(tmp_path)
    stale = _advice(pack, "router_diagnosis", "report_only")
    stale["snapshot_ids"] = {"memory": "different"}
    merged = merge_advice(tmp_path, advice_items=[stale], evidence_pack=pack)
    assert merged["policy_rejections"][0]["reason"] == "input_hash_or_snapshot_mismatch"
