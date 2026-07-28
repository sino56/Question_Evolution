import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from question_evolution import resolve_candidate_operator_ids
from search_coordinator import (
    ASSIGNMENT_MODE_LIVE,
    claim_branches,
    initialize_search_state,
    merge_decision_result,
    upgrade_search_state,
)


OPERATORS = [
    "O20_multistage_event_breakpoint",
    "O19_multi_entity_role_binding",
    "O29_entity_identity_conflict_resolution",
]


def live_record():
    return {
        "sample_id": "live-parent",
        "prompt": "parent prompt",
        "score_rate": 1.0,
        "evolution_action": "evolve_high_score_overscore",
        "operator_route": {
            "routing_mode": "hybrid",
            "assignment_mode": "live",
            "route_revision": "hybrid-live-route-v1",
            "selected_operator_ids": list(OPERATORS),
            "primary_operator": OPERATORS[0],
            "backup_operators": OPERATORS[1:],
            "avoid_operators": [],
        },
    }


def decision(claim, score_rate):
    return {
        "branch_id": claim["branch_id"],
        "parent_score_rate": 1.0,
        "score_rate": score_rate,
        "experimental_evaluation_status": "pending",
    }


def test_live_scheduler_exhausts_frozen_candidates_after_boundary_target():
    state = initialize_search_state(
        live_record(),
        branch_window=1,
        boundary_target=1,
        assignment_mode=ASSIGNMENT_MODE_LIVE,
    )
    assert state["assignment_mode"] == "live"
    assert state["selected_operator_ids"] == OPERATORS

    for expected_operator, score_rate in zip(OPERATORS, (0.5, 0.4, 1.0)):
        state, claimed = claim_branches(state)
        assert [claim["operator_id"] for claim in claimed] == [expected_operator]
        state = merge_decision_result(state, decision(claimed[0], score_rate))

    assert state["boundary_candidate_count"] == 2
    assert state["termination_reason"] == "candidate_list_exhausted"
    assert state["status"] == "completed"
    assert state["attempted_selected_operator_ids"] == OPERATORS
    assert claim_branches(state)[1] == []


def test_live_scheduler_refuses_statistical_reordering():
    with pytest.raises(ValueError, match="preserves Router rank"):
        initialize_search_state(
            live_record(),
            assignment_mode=ASSIGNMENT_MODE_LIVE,
            operator_sort_mode="yield_per_time",
        )


def test_legacy_search_state_cannot_be_silently_promoted_to_live():
    legacy_state = {
        "parent_node_id": "live-parent::root",
        "selected_operator_ids": [OPERATORS[0]],
        "operator_plan": [],
    }
    with pytest.raises(ValueError, match="start a new live experiment"):
        upgrade_search_state(legacy_state, record=live_record())


def test_live_direct_generator_does_not_truncate_frozen_operator_list():
    assert resolve_candidate_operator_ids(live_record(), max_candidates=1) == OPERATORS
