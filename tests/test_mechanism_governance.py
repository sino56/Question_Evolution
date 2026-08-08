import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import question_behavior_analysis as behavior
from agent_runtime.global_memory import GlobalMemoryStore
from mechanism_governance import induce_candidates, publish_facts, route_audit, route_replay, validate_effects


def _judge(rate, items):
    return {"score_rate": rate, "item_scores": items}


def _analysis(sample_id, mechanism, *, operator="O12", risk=None):
    rubric = [{"title": "core", "weight": 4}, {"title": "penalty", "weight": -2}]
    high = [{"title": "core", "awarded": 4}, {"title": "penalty", "awarded": 0}]
    low = [{"title": "core", "awarded": 0}, {"title": "penalty", "awarded": -2}]
    source = {
        "sample_id": sample_id, "prompt": "Which conclusion follows?", "reference_answer": "A supported conclusion.",
        "rubric": rubric, "candidate_operator": operator, "decision_evaluation_status": "completed",
        "sample_signature": {"scene_family": "traffic", "question_form": "necessity", "overscore_pattern": "overclaim"},
        "scoring_result": {"answer_trials": [
            {"trial_index": 1, "candidate_answer": "complete answer", "qwen_score_rate_mean": 0.9, "qwen_judge_results": [_judge(0.9, high), _judge(0.9, high)], "gpt_judge_results": []},
            {"trial_index": 2, "candidate_answer": "incomplete answer", "qwen_score_rate_mean": 0.2, "qwen_judge_results": [_judge(0.2, low), _judge(0.2, low)], "gpt_judge_results": []},
        ]},
    }
    record = behavior.analyze_item(source)
    record["observer_status"] = "completed"
    record["observer_result"] = {
        "analysis_status": "completed", "confidence": "medium", "behavior_labels": ["informative_answer_gap"],
        "difference_summary": "The high answer satisfies the core item while the low answer misses it.",
        "high_answer_strengths": ["core"], "low_answer_failures": ["core"],
        "candidate_mechanisms": [mechanism], "question_or_rubric_risk": risk,
        "rubric_evidence": [{"trial_index": 1, "rubric_title": "core", "answer_fragment_id": "trial_1_full"}],
    }
    return record, source


def _frozen_config():
    return {
        "question_pool": "holdout-20260808", "answer_model": "qwen-fixed", "answer_parameters": {"temperature": 0},
        "qwen_judge_config": {"model": "qwen-judge"}, "gpt_recheck_config": {"model": "gpt-review"},
        "rubric_version": "r1", "memory_snapshot_id": "MSNAP-frozen", "thresholds": {"min_score_drop": 0.15},
        "manual_review_rules": "two-reviewer", "split": {"kind": "root_sample_holdout", "name": "holdout-a"}, "experiment_kind": "retrospective",
    }


def _qualified_candidate():
    first, source_first = _analysis("root-1", "joint-condition omission")
    second, source_second = _analysis("root-2", "joint-condition omission")
    contrast, source_contrast = _analysis("root-3", "premature conclusion", operator="O10")
    candidates, rejected = induce_candidates(
        [first, second, contrast], analysis_path="behavior_observed_analysis.jsonl",
        source_records=[source_first, source_second, source_contrast],
    )
    assert any(row["reason"] == "independent_evidence_or_counterexample_insufficient" for row in rejected)
    return next(row for row in candidates if row["card_type"] == "capability_mechanism")


def test_induction_requires_independent_evidence_and_preserves_risks_as_risks():
    candidate = _qualified_candidate()
    assert candidate["status"] == "proposed"
    assert candidate["root_sample_ids"] == ["root-1", "root-2"]
    assert candidate["linked_operator_ids"] == ["O12_conjunctive_necessity"]
    assert candidate["counterexamples"]

    risk, risk_source = _analysis("root-risk", "not a capability", risk="ambiguous wording")
    outputs, _ = induce_candidates([risk], analysis_path="behavior.jsonl", source_records=[risk_source])
    assert outputs[0]["card_type"] == "risk_pattern"
    assert outputs[0]["linked_operator_ids"] == []


def test_induction_does_not_merge_same_label_across_taxonomy_scopes():
    first, source_first = _analysis("scope-1", "joint-condition omission")
    second, source_second = _analysis("scope-2", "joint-condition omission")
    source_second["sample_signature"]["question_form"] = "attribution"
    candidates, rejected = induce_candidates([first, second], analysis_path="behavior.jsonl", source_records=[source_first, source_second])
    assert candidates == []
    assert len([row for row in rejected if row["reason"] == "independent_evidence_or_counterexample_insufficient"]) == 2


def test_effect_validation_freezes_config_excludes_sources_and_requires_human_approval():
    candidate = _qualified_candidate()
    effects = [
        {"sample_id": "root-1", "candidate_operator": "O12", "previous_score_rate": 0.9, "score_rate": 0.5, "target_mechanism_hit": True, "high_answer_passes": True, "validation_result": {"passed": True}},
        {"sample_id": "holdout-1", "candidate_operator": "O12", "previous_score_rate": 0.9, "score_rate": 0.6, "answer_volatility": 0.05, "target_mechanism_hit": True, "high_answer_passes": True, "validation_result": {"passed": True}},
        {"sample_id": "holdout-2", "candidate_operator": "O12", "previous_score_rate": 0.85, "score_rate": 0.55, "judge_volatility": 0.05, "target_mechanism_hit": True, "high_answer_passes": True, "validation_result": {"passed": True}},
    ]
    review = {"mechanism_id": candidate["mechanism_id"], "status": "approved", "reviewer": "reviewer-1"}
    validations, matrix, report = validate_effects([candidate], effects, config=_frozen_config(), reviews=[review])

    validation = validations[0]
    assert validation["holdout_root_sample_ids"] == ["holdout-1", "holdout-2"]
    assert validation["qualification_status"] == "qualified"
    assert matrix[0]["stable_drop_rate"] == 1.0
    assert report["qualified_count"] == 1
    published = publish_facts([candidate], qualification={candidate["mechanism_id"]: validation})[0]
    assert published["requested_status"] == "qualified"
    assert published["target_card_type"] == "positive_strategy"

    no_review, _, _ = validate_effects([candidate], effects, config=_frozen_config())
    assert no_review[0]["qualification_status"] == "proposed"


def _stable_snapshot(tmp_path, candidate=None):
    exp = tmp_path / "experiments" / "day" / "exp"
    memory = exp / "memory" / "failure_memory_bank.jsonl"
    memory.parent.mkdir(parents=True)
    if candidate is None:
        rows = [
            {"sample_id": "x1", "operator_used": "O12", "failure_reason": "not applicable", "sample_signature": {"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "joint-condition omission"}},
            {"sample_id": "x2", "operator_used": "O12", "failure_reason": "not applicable", "sample_signature": {"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "joint-condition omission"}},
        ]
        memory.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    else:
        fact = publish_facts([candidate], qualification={candidate["mechanism_id"]: {"validation_status": "validated", "qualification_status": "qualified", "manual_review": {"status": "approved"}}})[0]
        target = exp / "round_1" / "mechanism_publish_candidates.jsonl"
        target.parent.mkdir(parents=True)
        target.write_text(json.dumps(fact) + "\n", encoding="utf-8")
    store = GlobalMemoryStore(tmp_path)
    store.extract(exp)
    store.integrate()
    return store.create_snapshot()["memory_snapshot_id"]


def test_route_audit_is_sidecar_only_and_limited_mode_is_gated(tmp_path):
    candidate = _qualified_candidate()
    validation = {
        "mechanism_id": candidate["mechanism_id"], "validation_status": "validated", "qualification_status": "qualified",
        "manual_review": {"status": "approved"},
    }
    route = {"sample_id": "route-root", "sample_signature": {"scene_family": "traffic", "question_form": "necessity"}, "operator_route": {"selected_operator": "O12", "operator_candidates": ["O12", "O10"]}}
    original = copy.deepcopy(route)
    snapshot = _stable_snapshot(tmp_path, candidate)
    audit = route_audit([route], [candidate], [validation], project_root=tmp_path, snapshot_id=snapshot)
    assert route == original
    assert audit[0]["disposition"] == "audit_only"
    assert audit[0]["suggestion_matches_existing_route"]
    assert "operator candidates" in audit[0]["action_limit"]

    limited = route_audit([route], [candidate], [validation], project_root=tmp_path, snapshot_id=snapshot, mode="limited", approval={"status": "approved"})
    assert limited[0]["disposition"] == "limited_integration_eligible"
    rolled_back = route_audit([route], [candidate], [validation], project_root=tmp_path, snapshot_id=snapshot, mode="limited", approval={"status": "approved"}, rollback=True)
    assert rolled_back[0]["disposition"] == "rollback_to_audit"
    assert rolled_back[0]["rollback_requested"] is True

    replay = route_replay(audit, [{"sample_id": "route-root", "previous_score_rate": 0.9, "score_rate": 0.6, "answer_volatility": 0.05, "target_mechanism_hit": True, "validation_result": {"passed": True}}], frozen_config=_frozen_config())
    assert replay["sample_count"] == 1
    assert replay["invalid_generation_rate"] == 0.0


def test_global_memory_accepts_mechanism_publish_facts_but_never_auto_qualifies(tmp_path):
    candidate = _qualified_candidate()
    exp = tmp_path / "experiments" / "day" / "mechanism-exp"
    publish_path = exp / "round_1" / "mechanism_publish_candidates.jsonl"
    publish_path.parent.mkdir(parents=True)
    proposed = publish_facts([candidate])[0]
    publish_path.write_text(json.dumps(proposed) + "\n", encoding="utf-8")
    store = GlobalMemoryStore(tmp_path)
    store.extract(exp)
    store.integrate()
    first = [json.loads(line) for line in (store.root / "global_memory_cards.jsonl").read_text(encoding="utf-8").splitlines()]
    assert first[0]["status"] == "proposed"

    validation = {
        "mechanism_id": candidate["mechanism_id"], "validation_status": "validated", "qualification_status": "qualified",
        "manual_review": {"status": "approved"},
    }
    qualified = publish_facts([candidate], qualification={candidate["mechanism_id"]: validation})[0]
    with publish_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(qualified) + "\n")
    store.extract(exp)
    store.integrate()
    second = [json.loads(line) for line in (store.root / "global_memory_cards.jsonl").read_text(encoding="utf-8").splitlines()]
    assert second[0]["status"] == "qualified"
