import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from schema_validation import validate_records_against_schema
from vertical_search import (
    attach_vertical_node,
    build_boundary_edge,
    build_boundary_path,
    build_child_node,
    build_root_node,
    build_vertical_operator_plan,
    initialize_vertical_search_state,
    make_node_id,
    make_path_id,
)


O10 = "O10_evidence_sufficiency_ladder"
O11 = "O11_unobserved_state_attribution"
O15 = "O15_counterfactual_threshold_shift"
O17 = "O17_action_vs_fact_threshold"


def sample(action="evolve_high_score_overscore"):
    return {
        "sample_id": "sample-1",
        "prompt": "root prompt",
        "score_rate": 1.0,
        "evolution_action": action,
        "operator_route": {
            "primary_operator": O10,
            "backup_operators": [O11],
            "avoid_operators": [O17],
        },
    }


def test_non_evolution_sample_does_not_enter_vertical_search():
    assert initialize_vertical_search_state(sample("stop_evolution")) is None


def test_vertical_state_is_lightweight_and_schema_valid():
    state = initialize_vertical_search_state(sample(), max_depth=3)
    assert state is not None
    assert state["root_node_id"] == "sample-1::root"
    assert state["frontier_node_ids"] == ["sample-1::root"]
    assert "nodes" not in state
    assert validate_records_against_schema(
        [state], ROOT / "schemas" / "vertical_search_state.schema.json"
    ) == []


def test_ordered_paths_have_stable_distinct_ids():
    root_id = "sample-1::root"
    assert make_node_id(root_id, [O10, O15]) != make_node_id(root_id, [O15, O10])
    assert make_node_id(root_id, [O10, O10]).endswith(f"{O10}::{O10}")
    assert make_path_id("sample-1", [O10, O15]) != make_path_id(
        "sample-1", [O15, O10]
    )


def test_operator_plan_repeats_are_filtered_and_avoid_is_only_downranked():
    record = sample()
    plan = build_vertical_operator_plan(
        record,
        operator_stack=[O10],
        registered_operator_ids=[O10, O11, O15, O17],
    )
    assert O10 not in plan
    assert plan[:2] == [O11, O15]
    assert plan[-1] == O17

    repeat_plan = build_vertical_operator_plan(
        record,
        operator_stack=[O10],
        allow_operator_repeat_in_path=True,
        registered_operator_ids=[O10, O11, O15, O17],
    )
    assert O10 in repeat_plan
    assert repeat_plan.index(O10) > repeat_plan.index(O15)


def test_child_uses_direct_parent_delta_and_root_cumulative_delta():
    root = build_root_node(sample(), max_depth=3)
    first = build_child_node(
        root,
        {"operator_id": O10, "score_rate": 0.8},
        max_depth=3,
        generation_sequence=1,
    )
    second = build_child_node(
        first,
        {"operator_id": O15, "score_rate": 0.7},
        max_depth=3,
        generation_sequence=2,
    )
    assert first["frontier_status"] == "eligible"
    assert second["node_status"] == "boundary_candidate"
    assert second["frontier_status"] == "depth_limit"
    assert abs(second["edge_delta_score_rate"] - (-0.1)) < 1e-9
    assert abs(second["root_delta_score_rate"] - (-0.3)) < 1e-9

    nodes = {root["node_id"]: root, first["node_id"]: first, second["node_id"]: second}
    edge = build_boundary_edge(second)
    path = build_boundary_path(second, nodes)
    assert path["operator_stack"] == [O10, O15]
    assert path["edge_deltas"] == [first["edge_delta_score_rate"], second["edge_delta_score_rate"]]
    assert validate_records_against_schema(
        [second], ROOT / "schemas" / "vertical_node.schema.json"
    ) == []
    assert validate_records_against_schema(
        [edge], ROOT / "schemas" / "boundary_edge.schema.json"
    ) == []
    assert validate_records_against_schema(
        [path], ROOT / "schemas" / "boundary_path.schema.json"
    ) == []

    attached = attach_vertical_node({"prompt": "child", "score_rate": 0.7}, second)
    assert attached["node_id"] == second["node_id"]
    assert attached["vertical_node"]["parent_node_id"] == first["node_id"]


def test_non_decreasing_children_never_enter_frontier():
    root = build_root_node(sample(), max_depth=3)
    equal = build_child_node(
        root,
        {"operator_id": O10, "score_rate": 1.0},
        max_depth=3,
        generation_sequence=1,
    )
    increased = build_child_node(
        root,
        {"operator_id": O11, "score_rate": 1.1},
        max_depth=3,
        generation_sequence=2,
    )
    assert equal["node_status"] == "no_score_change"
    assert increased["node_status"] == "score_increased"
    assert equal["frontier_status"] == increased["frontier_status"] == "ineligible"
