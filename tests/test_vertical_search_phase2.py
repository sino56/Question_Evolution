import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from vertical_artifacts import VerticalArtifactStore
from vertical_search import (
    attach_vertical_node,
    build_child_node,
    build_root_node,
    claim_next_frontier,
    complete_frontier,
    initialize_vertical_search_state,
    upgrade_vertical_search_state,
)


O10 = "O10_evidence_sufficiency_ladder"
O11 = "O11_unobserved_state_attribution"
O15 = "O15_counterfactual_threshold_shift"


def sample():
    return {
        "sample_id": "sample-vertical",
        "prompt": "root prompt",
        "score_rate": 1.0,
        "evolution_action": "evolve_high_score_overscore",
    }


def child_record(parent, operator, score, prompt, sequence, max_depth=3):
    node = build_child_node(
        parent,
        {"operator_id": operator, "score_rate": score},
        max_depth=max_depth,
        generation_sequence=sequence,
    )
    return attach_vertical_node({"prompt": prompt, "score_rate": score}, node)


def test_frontier_is_breadth_first_and_non_decreasing_nodes_are_not_queued():
    root = build_root_node(sample(), max_depth=3)
    state = initialize_vertical_search_state(sample(), max_depth=3)
    assert state is not None
    state, claimed = claim_next_frontier(state, {root["node_id"]: root})
    assert claimed == root["node_id"]

    decreasing = child_record(root, O10, 0.8, "decreasing", 1)
    unchanged = child_record(root, O11, 1.0, "unchanged", 2)
    state = complete_frontier(
        state,
        root,
        [decreasing, unchanged],
        completed_attempt_count=2,
        operator_plan=[O10, O11],
    )
    assert state["frontier_node_ids"] == [decreasing["node_id"]]
    assert state["boundary_candidate_count"] == 1

    nodes = {
        root["node_id"]: root,
        decreasing["node_id"]: decreasing["vertical_node"],
        unchanged["node_id"]: unchanged["vertical_node"],
    }
    state, claimed = claim_next_frontier(state, nodes)
    assert claimed == decreasing["node_id"]

    # Review is offline evidence only and must not control online scheduling.
    nodes[decreasing["node_id"]]["review_status"] = "rejected"
    state, reclaimed = claim_next_frontier(state, nodes)
    assert reclaimed == decreasing["node_id"]


def test_single_operator_target_stops_root_but_keeps_registered_frontier():
    root = build_root_node(sample(), max_depth=3)
    state = initialize_vertical_search_state(sample(), max_depth=3, boundary_target=1)
    assert state is not None
    state, _ = claim_next_frontier(state, {root["node_id"]: root})
    decreasing = child_record(root, O10, 0.8, "decreasing", 1)
    state = complete_frontier(
        state,
        root,
        [decreasing],
        completed_attempt_count=1,
        operator_plan=[O10],
    )
    assert state["status"] == "running"
    assert state["single_operator_boundary_count"] == 1
    assert state["stacked_operator_boundary_count"] == 0
    assert state["single_operator_root_expansion_stopped"] is True
    assert state["frontier_node_ids"] == [decreasing["node_id"]]


def test_layered_targets_are_counted_separately_and_stack_terminates_at_depth_three():
    root = build_root_node(sample(), max_depth=3)
    state = initialize_vertical_search_state(
        sample(),
        max_depth=3,
        single_operator_boundary_target=1,
        stacked_operator_boundary_target=1,
        total_boundary_hard_cap=3,
    )
    assert state is not None
    state, _ = claim_next_frontier(state, {root["node_id"]: root})
    first = child_record(root, O10, 0.8, "first", 1)
    state = complete_frontier(
        state, root, [first], completed_attempt_count=1, operator_plan=[O10]
    )
    state, parent_id = claim_next_frontier(
        state, {root["node_id"]: root, first["node_id"]: first["vertical_node"]}
    )
    assert parent_id == first["node_id"]
    stacked = child_record(first["vertical_node"], O15, 0.6, "stacked", 1)
    state = complete_frontier(
        state,
        first["vertical_node"],
        [stacked],
        completed_attempt_count=1,
        operator_plan=[O15],
    )
    assert state["single_operator_boundary_count"] == 1
    assert state["stacked_operator_boundary_count"] == 1
    assert state["total_boundary_count"] == 2
    assert state["status"] == "completed"
    assert state["termination_reason"] == "stacked_operator_boundary_target_reached"


def test_total_hard_cap_has_its_own_termination_reason():
    root = build_root_node(sample(), max_depth=3)
    state = initialize_vertical_search_state(
        sample(),
        max_depth=3,
        single_operator_boundary_target=1,
        stacked_operator_boundary_target=2,
        total_boundary_hard_cap=2,
    )
    assert state is not None
    state, _ = claim_next_frontier(state, {root["node_id"]: root})
    first = child_record(root, O10, 0.8, "first", 1)
    state = complete_frontier(
        state, root, [first], completed_attempt_count=1, operator_plan=[O10]
    )
    state, _ = claim_next_frontier(
        state, {root["node_id"]: root, first["node_id"]: first["vertical_node"]}
    )
    stacked = child_record(first["vertical_node"], O15, 0.6, "stacked", 1)
    state = complete_frontier(
        state,
        first["vertical_node"],
        [stacked],
        completed_attempt_count=1,
        operator_plan=[O15],
    )
    assert state["status"] == "completed"
    assert state["termination_reason"] == "total_boundary_hard_cap_reached"


def test_max_depth_is_node_limit_not_sample_termination_reason():
    root = build_root_node(sample(), max_depth=2)
    state = initialize_vertical_search_state(sample(), max_depth=2)
    assert state is not None
    state, _ = claim_next_frontier(state, {root["node_id"]: root})
    decreasing = child_record(root, O10, 0.8, "decreasing", 1, max_depth=2)
    assert decreasing["vertical_node"]["frontier_status"] == "depth_limit"
    state = complete_frontier(
        state,
        root,
        [decreasing],
        completed_attempt_count=1,
        operator_plan=[O10],
    )
    assert state["termination_reason"] == "operator_space_exhausted"


def test_active_frontier_is_reclaimed_after_restart_without_duplicate_execution_entry():
    root = build_root_node(sample(), max_depth=3)
    state = initialize_vertical_search_state(sample(), max_depth=3)
    assert state is not None
    state, first = claim_next_frontier(state, {root["node_id"]: root})
    state, second = claim_next_frontier(state, {root["node_id"]: root})
    assert first == second == root["node_id"]
    assert len(state["execution_sequence"]) == 1


def test_missing_frontier_artifact_is_a_recovery_failure_not_business_completion():
    state = initialize_vertical_search_state(sample(), max_depth=3)
    assert state is not None
    state, claimed = claim_next_frontier(state, {})
    assert claimed is None
    assert state["status"] == "partial"
    assert state["termination_reason"] == "state_recovery_failed"


def test_artifact_store_is_append_only_and_idempotent(tmp_path):
    store = VerticalArtifactStore(tmp_path / "vertical")
    root = build_root_node(sample(), max_depth=3)
    assert store.append("node", root) is True
    assert store.append("node", root) is False
    assert store.count("node") == 1

    resumed = VerticalArtifactStore(tmp_path / "vertical")
    assert resumed.count("node") == 1
    assert [row["node_id"] for row in resumed.iter_records("node")] == [root["node_id"]]


def test_version_one_checkpoint_upgrades_to_layered_quota_state():
    legacy = {
        "vertical_search_state_version": 1,
        "search_mode": "multi_operator_vertical_stack",
        "root_node_id": "sample-vertical::root",
        "status": "running",
        "current_depth": 1,
        "max_depth": 3,
        "boundary_target": 5,
        "boundary_candidate_count": 1,
        "completed_operator_attempt_count": 1,
        "frontier_node_ids": ["sample-vertical::root"],
        "completed_parent_node_ids": [],
        "registered_node_ids": ["sample-vertical::root"],
        "registered_prompt_hashes": [],
    }
    upgraded = upgrade_vertical_search_state(legacy)
    assert upgraded["vertical_search_state_version"] == 2
    assert upgraded["single_operator_boundary_target"] == 5
    assert upgraded["stacked_operator_boundary_target"] == 5
    assert upgraded["total_boundary_hard_cap"] == 10
    assert upgraded["single_operator_boundary_count"] == 1
    assert upgraded["total_boundary_count"] == 1
