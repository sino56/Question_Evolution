"""Stage-4 regressions for the memory system.

Each test maps to one finding of
``docs/Agent改造方案/Agent_Harness_代码审查报告_2026-09-11.md`` §3:

- M-2 weighted retrieval, hard exclusion filtering, CJK-aware tokenization.
- M-3 card metrics are measured values or an explicit ``None``, never ``0.0``.
- M-4 ``source_rewritten`` reviews are deduplicated and can be closed.
- M-5 retrieval and projection no longer scale with the whole card table.
- M-6 card ids come from a sequence that tolerates a malformed legacy id.
- M-8 constructing the store no longer mutates the filesystem.
- M-10 control-plane observations are not L1 experiment facts.
"""

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.global_memory import (
    CONTROL_PLANE_SOURCES,
    LOCAL_SOURCES,
    GlobalMemoryError,
    GlobalMemoryStore,
    _tokenize,
)


def _record(**overrides):
    record = {
        "sample_id": "sample-1",
        "round": 2,
        "operator_used": "O16",
        "failure_type": "score_decreased",
        "failure_reason": "O16 reduced the score",
        "sample_signature": {"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "joint conditions"},
    }
    record.update(overrides)
    return record


def _write_jsonl(path: Path, records) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8")


def _store(tmp_path: Path, records, *, name="failure_memory_bank.jsonl") -> GlobalMemoryStore:
    experiment = tmp_path / "experiments" / "day" / "exp1"
    _write_jsonl(experiment / "memory" / name, records)
    store = GlobalMemoryStore(tmp_path)
    store.extract(experiment)
    store.integrate()
    return store


def _log_rows(store: GlobalMemoryStore) -> list[dict]:
    path = store.root / "global_memory_admission_log.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- M-2


def test_retrieval_weights_reasoning_mechanism_above_scene_family(tmp_path):
    store = _store(
        tmp_path,
        [
            _record(operator_used="O16"),
            _record(
                operator_used="O17",
                sample_id="sample-2",
                sample_signature={"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "counterfactual chain"},
            ),
        ],
    )
    snapshot = store.create_snapshot()
    result = store.retrieve(snapshot_id=snapshot["memory_snapshot_id"], query="joint conditions", top_k=2)

    assert len(result["cards"]) == 2
    assert "joint conditions" in result["cards"][0]["summary"]
    assert result["cards"][0]["retrieval_score"] > result["cards"][1]["retrieval_score"]
    assert result["retrieval_config_version"] == "global-memory-retrieval-v2"


def test_exclusion_condition_match_hard_filters_the_card(tmp_path):
    store = _store(tmp_path, [_record(exclusion_conditions=["traffic"])])
    snapshot = store.create_snapshot()
    result = store.retrieve(snapshot_id=snapshot["memory_snapshot_id"], query="traffic necessity", top_k=3)

    assert result["cards"] == []
    excluded = result["retrieval_diagnostics"]["excluded"]
    assert [entry["reason"] for entry in excluded] == ["exclusion_condition_matched"]


def test_tokenizer_is_cjk_aware_and_ranking_uses_it(tmp_path):
    assert _tokenize("交通场景") == {"交", "通", "场", "景"}
    assert _tokenize("Traffic Necessity") == {"traffic", "necessity"}
    # ``"交通场景".split()`` yields one token, so the old substring counter
    # could not tell "交通 场景" from "交通".
    assert len(_tokenize("交通 场景")) == 4

    store = _store(
        tmp_path,
        [
            _record(operator_used="O16", sample_signature={"scene_family": "交通 场景", "question_form": "necessity", "reasoning_mechanism": "m1"}),
            _record(operator_used="O17", sample_id="sample-2", sample_signature={"scene_family": "交通", "question_form": "necessity", "reasoning_mechanism": "m2"}),
        ],
    )
    snapshot = store.create_snapshot()
    result = store.retrieve(snapshot_id=snapshot["memory_snapshot_id"], query="交通场景", top_k=2)

    assert len(result["cards"]) == 2
    assert result["cards"][0]["retrieval_score"] > result["cards"][1]["retrieval_score"]


def test_retrieval_reports_a_bounded_context_budget(tmp_path):
    store = _store(tmp_path, [_record()])
    snapshot = store.create_snapshot()
    result = store.retrieve(snapshot_id=snapshot["memory_snapshot_id"], query="traffic necessity", top_k=3)

    diagnostics = result["retrieval_diagnostics"]
    assert diagnostics["context_char_budget"] > 0
    assert 0 <= diagnostics["context_chars_remaining"] <= diagnostics["context_char_budget"]


# --------------------------------------------------------------------------- M-3


def test_card_metrics_are_measured_or_explicitly_unknown(tmp_path):
    store = _store(
        tmp_path,
        [
            _record(
                operator_used="O12",
                effect_analysis={"score_decreased_after_evolution": True, "score_increased_after_evolution": False},
            ),
            _record(
                operator_used="O13",
                sample_id="sample-2",
                failure_type="unclassified_observation",
                failure_reason="unspecified observation",
                sample_signature={"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "other"},
            ),
        ],
    )
    cards = {card["reasoning_mechanism"]: card for card in store._cards()}

    measured = cards["joint conditions"]["evidence_summary"]
    assert measured["effective_rate"] == 1.0
    assert measured["score_increased_rate"] == 0.0
    assert measured["invalid_generation_rate"] == 0.0
    assert measured["measured_facts"] == 1

    unknown = cards["other"]["evidence_summary"]
    assert unknown["effective_rate"] is None
    assert unknown["score_increased_rate"] is None
    assert unknown["invalid_generation_rate"] is None
    assert unknown["measured_facts"] == 0


def test_score_increase_is_detected_from_structured_signals(tmp_path):
    store = _store(
        tmp_path,
        [
            _record(
                operator_used="O20",
                failure_type="score_increased",
                failure_reason="O20 made the question easier",
                effect_analysis={"score_increased_after_evolution": True},
            )
        ],
    )
    card = store._cards()[0]

    assert card["evidence_summary"]["score_increased_rate"] == 1.0
    assert card["status"] == "needs_human_review"
    assert "score_increase_observed" in card["risk_labels"]
    assert card["claim_level"] == "single_observation"


def test_health_report_aggregates_measured_cards(tmp_path):
    store = _store(tmp_path, [_record(effect_analysis={"score_decreased_after_evolution": True})])
    health = store.write_health_report()

    assert health["measured_cards"] == 1
    assert health["memory_hit_score_increased_rate"] == 0.0


# --------------------------------------------------------------------------- M-4


def test_repeated_source_rewrites_collapse_into_one_review_item(tmp_path):
    experiment = tmp_path / "experiments" / "day" / "exp1"
    source = experiment / "memory" / "failure_memory_bank.jsonl"
    _write_jsonl(source, [_record()])
    store = GlobalMemoryStore(tmp_path)
    store.extract(experiment)

    for index in range(4):
        _write_jsonl(source, [_record(failure_reason=f"rewritten-{index}")])
        assert store.extract(experiment)["source_rewritten"] == 1

    reviews = [row for row in _log_rows(store) if row["decision"] == "needs_human_review"]
    assert len(reviews) == 1
    assert "prefix changed" in reviews[0]["reason"]


def test_bless_source_closes_the_review_and_restores_incremental_extraction(tmp_path):
    experiment = tmp_path / "experiments" / "day" / "exp1"
    source = experiment / "memory" / "failure_memory_bank.jsonl"
    _write_jsonl(source, [_record()])
    store = GlobalMemoryStore(tmp_path)
    store.extract(experiment)
    _write_jsonl(source, [_record(failure_reason="rewritten")])
    assert store.extract(experiment)["source_rewritten"] == 1

    blessed = store.bless_source(str(source))
    assert blessed["status"] == "ok"
    rows = _log_rows(store)
    assert not [row for row in rows if row["decision"] == "needs_human_review"]
    assert any(row["decision"] == "blessed" for row in rows)

    _write_jsonl(source, [_record(failure_reason="rewritten"), _record(sample_id="sample-2", failure_reason="new evidence")])
    result = store.extract(experiment)
    assert result["source_rewritten"] == 0
    assert result["included"] == 1


def test_reset_watermark_forces_a_full_reread(tmp_path):
    experiment = tmp_path / "experiments" / "day" / "exp1"
    source = experiment / "memory" / "failure_memory_bank.jsonl"
    _write_jsonl(source, [_record()])
    store = GlobalMemoryStore(tmp_path)
    store.extract(experiment)
    assert store.extract(experiment)["included"] == 0

    store.reset_watermark(str(source))
    after = store.extract(experiment)
    assert after["excluded"] == 1  # re-read, then deduplicated by content hash


# --------------------------------------------------------------------------- M-5


def test_retrieve_does_not_load_the_whole_card_table(tmp_path, monkeypatch):
    store = _store(tmp_path, [_record()])
    snapshot = store.create_snapshot()

    def _boom(_self):
        raise AssertionError("retrieve must not load every card")

    monkeypatch.setattr(GlobalMemoryStore, "_cards", _boom)
    result = store.retrieve(snapshot_id=snapshot["memory_snapshot_id"], query="traffic necessity", top_k=3)

    assert result["cards"]


def test_unchanged_projections_are_not_rewritten(tmp_path):
    store = _store(tmp_path, [_record()])
    cards_path = store.root / "global_memory_cards.jsonl"
    before = cards_path.read_text(encoding="utf-8")

    unchanged = store.publish_projections()
    assert unchanged["rewritten"] == []
    assert unchanged["card_count"] == 1
    assert set(unchanged["unchanged"]) == {
        "global_memory_cards.jsonl",
        "global_memory_index.json",
        "global_memory_admission_log.jsonl",
        "global_memory_watermarks.jsonl",
    }
    assert cards_path.read_text(encoding="utf-8") == before

    # A real change must still be published.
    with store._connect() as con:
        con.execute("UPDATE cards SET status = 'retired'")
    changed = store.publish_projections()
    assert "global_memory_cards.jsonl" in changed["rewritten"]
    assert changed["card_count"] == 1
    assert '"retired"' in cards_path.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- M-6


def test_card_id_sequence_tolerates_a_malformed_existing_id(tmp_path):
    store = GlobalMemoryStore(tmp_path)
    stamp = "2026-01-01T00:00:00+00:00"
    with store._connect() as con:
        con.execute(
            """INSERT INTO cards(card_id,card_type,status,version,fingerprint,body,evidence_refs,created_at,updated_at)
               VALUES ('legacy-weird-id','system_diagnosis','proposed',1,'fp-x','{}','[]',?,?)""",
            (stamp, stamp),
        )
        first = store._next_card_id(con)
        second = store._next_card_id(con)

    assert first == "GMEM-000001"
    assert second == "GMEM-000002"


def test_card_id_sequence_is_seeded_from_existing_gmem_ids(tmp_path):
    store = GlobalMemoryStore(tmp_path)
    stamp = "2026-01-01T00:00:00+00:00"
    with store._connect() as con:
        con.execute(
            """INSERT INTO cards(card_id,card_type,status,version,fingerprint,body,evidence_refs,created_at,updated_at)
               VALUES ('GMEM-000007','system_diagnosis','proposed',1,'fp-y','{}','[]',?,?)""",
            (stamp, stamp),
        )
        assert store._next_card_id(con) == "GMEM-000008"


# --------------------------------------------------------------------------- M-8


def test_uninitialized_construction_has_no_filesystem_side_effects(tmp_path):
    store = GlobalMemoryStore(tmp_path, initialize=False)

    assert store.db_path.exists() is False
    assert not (tmp_path / "memory_global").exists()
    with pytest.raises(GlobalMemoryError, match="not initialized"):
        store._cards()

    store.initialize()
    assert store.db_path.exists() is True
    assert store._cards() == []


def test_read_only_store_refuses_to_write(tmp_path):
    GlobalMemoryStore(tmp_path)

    read_only = GlobalMemoryStore(tmp_path, read_only=True)
    assert read_only._cards() == []
    with pytest.raises(GlobalMemoryError, match="read-only"):
        read_only.publish_projections()
    with pytest.raises(GlobalMemoryError, match="read-only"):
        read_only.extract(tmp_path / "experiments")
    with pytest.raises(GlobalMemoryError, match="unavailable"):
        GlobalMemoryStore(tmp_path / "missing", read_only=True)


# --------------------------------------------------------------------------- M-10


def test_control_plane_observations_are_not_l1_facts(tmp_path):
    experiment = tmp_path / "experiments" / "day" / "exp1"
    target = experiment / "agent_observation.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"status": "ok", "observation_types": ["score_decreased"]}), encoding="utf-8")

    store = GlobalMemoryStore(tmp_path)
    result = store.extract(experiment)

    assert "agent_observation.json" not in LOCAL_SOURCES
    assert "agent_observation.json" in CONTROL_PLANE_SOURCES
    assert result["included"] == 0
    watermarks = (store.root / "global_memory_watermarks.jsonl").read_text(encoding="utf-8")
    assert "agent_observation.json" not in watermarks


def test_unparseable_lines_are_quarantined_instead_of_blocking_extraction(tmp_path):
    experiment = tmp_path / "experiments" / "day" / "exp1"
    source = experiment / "memory" / "failure_memory_bank.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    good = {"sample_id": "sample-q", "round": 1, "operator_used": "O16", "failure_type": "score_increased", "failure_reason": "quarantine case"}
    source.write_text(json.dumps(good, ensure_ascii=False) + "\n" + "{broken json\n", encoding="utf-8")

    store = GlobalMemoryStore(tmp_path)
    result = store.extract(experiment)

    assert result["included"] == 1
    assert result["quarantined_lines"] == 1
    rows = _log_rows(store)
    assert any("unparseable line quarantined" in str(row["reason"]) for row in rows if row["decision"] == "needs_human_review")

    # The watermark advanced past the bad line: no reprocessing on the next run.
    again = store.extract(experiment)
    assert again["included"] == 0
    assert again["quarantined_lines"] == 0
