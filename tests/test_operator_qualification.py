import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from operator_qualification import (
    CONFIRMED,
    INSUFFICIENT,
    REFUTED,
    evaluate_forced_qualification,
    evaluate_natural_routing,
)


OPERATOR = "O13_minimal_disqualifier"


def qualification_record(
    index,
    *,
    contract_pass=True,
    answer_consistent=True,
    no_leakage=True,
    parent_preserved=True,
    observable=True,
    non_isomorphic=True,
    taxonomy_hit=True,
    manual=True,
    score_drop=True,
):
    return {
        "sample_id": f"q-{index}",
        "candidate_operator": OPERATOR,
        "qualification_manifest": {"human_confirmed": True},
        "validation_result": {"passed": contract_pass},
        "qualification": {
            "answer_unique_and_rubric_consistent": answer_consistent,
            "no_surface_leakage": no_leakage,
            "parent_obligations_preserved": parent_preserved,
            "required_reasoning_observable": observable,
            "non_isomorphic_to_adjacent": non_isomorphic,
            "neighbor_attribution_correct": True,
            "target_error_taxonomy_hit": taxonomy_hit,
            "manual_boundary_confirmed": manual,
            "semantic_direction": "local_link_broken_overall_supported",
        },
        "effect_analysis": {
            "score_rate_before": 1.0,
            "score_rate_after": 0.5 if score_drop else 1.0,
        },
    }


def test_forced_qualification_confirms_only_multimetric_evidence():
    records = [qualification_record(index) for index in range(5)]
    report = evaluate_forced_qualification(
        records,
        OPERATOR,
        min_records=5,
        qualification_run_id="forced-o13",
    )
    assert report["qualification_decision"] == CONFIRMED
    assert report["router_results_used_for_decision"] is False
    assert report["score_drop_is_sole_success_metric"] is False
    assert report["recommended_contract_status"] == "eligible_for_natural_routing_holdout"
    assert report["memory_isolated"] is True


def test_score_drop_cannot_rescue_contract_or_parent_obligation_failure():
    records = [
        qualification_record(
            index,
            contract_pass=index >= 2,
            parent_preserved=False,
            score_drop=True,
        )
        for index in range(5)
    ]
    report = evaluate_forced_qualification(records, OPERATOR, min_records=5)
    assert report["metrics"]["score_drop_rate"] == 1.0
    assert report["qualification_decision"] == REFUTED
    assert report["recommended_contract_status"] == "remain_disabled_or_validation_only"


def test_o11_o17_o18_reports_use_evidence_insufficient_not_generic_fixed():
    for operator_id in (
        "O11_unobserved_state_attribution",
        "O17_action_vs_fact_threshold",
        "O18_baseline_scope_mismatch",
    ):
        report = evaluate_forced_qualification([], operator_id, min_records=5)
        assert report["qualification_decision"] == INSUFFICIENT
        assert report["evidence_status_before"] == "qualification_hypothesis"


def test_natural_routing_holdout_reports_wrong_missed_and_not_applicable():
    records = [
        {
            "expected_operator_id": OPERATOR,
            "expected_applicability": "eligible",
            "operator_route": {"primary_operator": OPERATOR},
        },
        {
            "expected_operator_id": OPERATOR,
            "expected_applicability": "eligible",
            "operator_route": {"primary_operator": "O15_counterfactual_threshold_shift"},
        },
        {
            "expected_operator_id": OPERATOR,
            "expected_applicability": "eligible",
            "operator_route": {"primary_operator": None},
        },
        {
            "expected_operator_id": "O17_action_vs_fact_threshold",
            "expected_applicability": "not_applicable",
            "operator_route": {"primary_operator": None},
        },
    ]
    report = evaluate_natural_routing(records, qualification_run_id="holdout-1")
    assert report["counts"]["correct_route"] == 1
    assert report["counts"]["wrong_route"] == 1
    assert report["counts"]["missed_route"] == 1
    assert report["counts"]["not_applicable_blocked"] == 1
    assert report["routing_accuracy"] == 0.3333
    assert report["not_applicable_interception_rate"] == 1.0
