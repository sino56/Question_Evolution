import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.global_memory import AdmissionRejected, GlobalMemoryError, GlobalMemoryStore, LeaseUnavailable, SnapshotUnavailable, validate_stage4_card


def _write_jsonl(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def _failure(sample_id="sample-1", reason="score_increased"):
    return {
        "sample_id": sample_id,
        "round": 2,
        "operator_used": "O16",
        "failure_type": reason,
        "failure_reason": "O16 score_increased repeatedly",
        "sample_signature": {"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "joint conditions"},
    }


def test_admission_incremental_watermark_and_rewrite_protection(tmp_path):
    exp = tmp_path / "experiments" / "day" / "exp1"
    source = exp / "memory" / "failure_memory_bank.jsonl"
    _write_jsonl(source, [_failure()])
    store = GlobalMemoryStore(tmp_path)

    first = store.extract(exp)
    assert first["included"] == 1
    assert store.extract(exp)["included"] == 0

    _write_jsonl(source, [_failure(), _failure("sample-2")])
    assert store.extract(exp)["included"] == 1

    _write_jsonl(source, [_failure("sample-1", "different")])
    rewritten = store.extract(exp)
    assert rewritten["source_rewritten"] == 1
    assert "source_rewritten" in (store.root / "global_memory_admission_log.jsonl").read_text(encoding="utf-8")


def test_complete_sample_and_missing_evidence_are_rejected(tmp_path):
    store = GlobalMemoryStore(tmp_path)
    with store._connect() as con:
        assert not store._admit(con, {"source_ref": "x", "evidence_refs": [], "prompt": "secret"})
    log = (store.root / "global_memory_admission_log.jsonl")
    store.publish_projections()
    assert "lacks a traceable evidence" in log.read_text(encoding="utf-8")


def test_phase2_lease_and_stage4_active_gate(tmp_path):
    store = GlobalMemoryStore(tmp_path)
    store.acquire_lease(job_type="phase2_integrate", source_exp_dir="", seconds=300)
    with pytest.raises(LeaseUnavailable):
        store.acquire_lease(job_type="phase2_integrate", source_exp_dir="", seconds=300)
    store.finish_lease(job_type="phase2_integrate", source_exp_dir="", success=True)
    with pytest.raises(AdmissionRejected, match="active"):
        validate_stage4_card({"card_type": "positive_strategy", "status": "active", "applicability_conditions": ["x"], "exclusion_conditions": ["y"], "evidence_refs": [{"artifact_ref": "z"}]})
    with pytest.raises(AdmissionRejected, match="applicability"):
        validate_stage4_card({"card_type": "positive_strategy", "status": "shadow", "applicability_conditions": [], "exclusion_conditions": ["y"], "evidence_refs": [{"artifact_ref": "z"}]})


def test_cards_projections_snapshots_and_bounded_deterministic_context(tmp_path):
    exp = tmp_path / "experiments" / "day" / "exp1"
    _write_jsonl(exp / "memory" / "failure_memory_bank.jsonl", [_failure(), _failure("sample-2")])
    store = GlobalMemoryStore(tmp_path)
    store.extract(exp)
    result = store.integrate()
    assert result["added"] == 1
    cards = (store.root / "global_memory_cards.jsonl").read_text(encoding="utf-8")
    assert '"status":"needs_human_review"' in cards
    assert (store.root / "global_memory_health_report.md").exists()

    snapshot = store.create_snapshot()
    first = store.retrieve(snapshot_id=snapshot["memory_snapshot_id"], query="traffic necessity", top_k=1)
    second = store.retrieve(snapshot_id=snapshot["memory_snapshot_id"], query="traffic necessity", top_k=1)
    assert first == second
    assert len(first["cards"]) <= 1
    assert first["cards"][0]["card_id"].startswith("GMEM-")
    assert "full" not in first["cards"][0]["summary"].lower()
    with pytest.raises(SnapshotUnavailable):
        store.load_snapshot("MSNAP-missing")
    assert store.load_snapshot("MSNAP-missing", allow_no_global_memory=True)["mode"] == "no_global_memory"

    with store._connect() as con:
        con.execute("UPDATE cards SET status = 'retired'")
    store.publish_projections()
    retired_snapshot = store.create_snapshot()
    retired_context = store.retrieve(snapshot_id=retired_snapshot["memory_snapshot_id"], query="traffic necessity", top_k=3)
    assert retired_context["cards"] == []
    assert "GMEM-" not in (store.root / "global_memory_index.json").read_text(encoding="utf-8")


def test_historical_import_requires_manifest(tmp_path):
    exp = tmp_path / "experiments" / "day" / "exp1"
    _write_jsonl(exp / "memory" / "failure_memory_bank.jsonl", [_failure()])
    with pytest.raises(GlobalMemoryError, match="manifest"):
        GlobalMemoryStore(tmp_path).import_trace(exp)


def test_historical_import_stays_shadow_or_proposed(tmp_path):
    exp = tmp_path / "experiments" / "day" / "exp1"
    _write_jsonl(exp / "memory" / "failure_memory_bank.jsonl", [_failure()])
    manifest = exp / "round_1" / "state_updated.jsonl.manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"schema_version": "pipeline-v1"}), encoding="utf-8")
    store = GlobalMemoryStore(tmp_path)
    store.import_trace(exp)
    cards = [json.loads(line) for line in (store.root / "global_memory_cards.jsonl").read_text(encoding="utf-8").splitlines()]
    assert cards and all(card["status"] != "active" for card in cards)


def test_router_cache_identity_changes_with_frozen_memory_snapshot(monkeypatch):
    from operator_router import RouterSettings, _cache_key

    settings = RouterSettings.from_values(routing_mode="rule", assignment_mode="natural", model=None, base_url=None, timeout_seconds=1, retries=0, concurrency=1)
    payload = {"memory_operator_ids": [], "avoid_operator_ids": [], "recommended_operator_ids": []}
    monkeypatch.setenv("MEMORY_SNAPSHOT_ID", "MSNAP-one")
    first = _cache_key(payload, settings)
    monkeypatch.setenv("MEMORY_SNAPSHOT_ID", "MSNAP-two")
    assert _cache_key(payload, settings) != first
