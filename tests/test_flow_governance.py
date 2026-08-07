from __future__ import annotations

from candidate_selection import candidate_flow_info
from governance import (
    CONTROLLED_HYPOTHETICAL_CASE,
    analyze_source,
    resolve_evolution_mode,
    writer_context,
)
from rebuild_reference_answer import (
    active_verified_reference,
    attach_execution_scope,
    attach_rebuilt_reference,
)
from update_sample_state import classify_memory_entries


def test_source_analysis_does_not_turn_full_parent_or_conclusion_into_fact():
    record = {"sample_id": "s1", "prompt": "画面记录甲在东门出现。现有材料不能直接确认甲随后到达北门。"}
    analysis = analyze_source(record)
    facts = analysis["source_observation_ledger"]
    assert [fact["text"] for fact in facts] == ["画面记录甲在东门出现。"]
    assert all(fact["text"] != record["prompt"] for fact in facts)
    assert analysis["answer_direction_ledger"]
    assert {"fact_id", "world_id", "global_fact_key", "origin_type", "source_locator"} <= set(facts[0])


def test_sparse_source_defaults_to_authorized_in_question_hypothetical_case():
    record = {"sample_id": "s2", "prompt": "如何判断一段记录是否可靠？"}
    decision = resolve_evolution_mode(record, analyze_source(record))
    assert decision["evolution_mode"] == CONTROLLED_HYPOTHETICAL_CASE
    assert decision["authorization_checked"] is True


def test_writer_context_has_no_answer_or_scoring_side_material():
    record = {
        "prompt": "记录显示一辆车进入园区。",
        "reference_answer": "正确答案是不能确认。",
        "rubric": [{"title": "评分", "description": "不能确认", "weight": 1}],
    }
    context = writer_context(record)
    encoded = str(context)
    assert "正确答案" not in encoded
    assert "评分" not in encoded
    assert "target_error" not in encoded


def test_nontechnical_validation_failure_remains_exploration_but_technical_block_does_not():
    common = {"question_evolved": True, "difficulty_gain_validation": {"difficulty_gain_label": "weak_gain"}}
    nontechnical = {
        **common,
        "validation_result": {"passed": False, "invalid_type": "multi_axis", "reject_reason": "multi axis"},
    }
    technical = {
        **common,
        "validation_result": {"passed": False, "invalid_type": "empty_prompt", "reject_reason": "empty"},
    }
    assert candidate_flow_info(nontechnical)["candidate_flow"] == "exploration_candidate"
    assert candidate_flow_info(technical)["candidate_flow"] == "hard_reject"


def test_reference_rebuild_uses_matching_question_version_only():
    record = {
        "question_evolved": True,
        "prompt": "最终题面：甲是否可确认？",
        "candidate_selection": {"selected": True},
        "meta_info": {},
    }
    scoped = attach_execution_scope(record, "full_iteration")
    rebuilt = attach_rebuilt_reference(scoped, "根据题内记录，不能确认。")
    assert active_verified_reference(rebuilt) == "根据题内记录，不能确认。"
    rebuilt["meta_info"]["reference_rebuild"]["reference_answer_version"] = "qv_wrong"
    try:
        active_verified_reference(rebuilt)
    except ValueError as exc:
        assert "version" in str(exc)
    else:
        raise AssertionError("mismatched reference version must fail")


def test_provisional_effect_never_writes_operator_success_memory():
    record = {
        "sample_id": "s3",
        "question_evolved": True,
        "effect_analysis": {
            "effect_label": "effective_boundary_probe",
            "complexity_passed": True,
            "effect_confirmation": {"status": "provisional"},
        },
    }
    success, failures, invalid = classify_memory_entries([record])
    assert success == []
