import json
import sys
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from update_sample_state import build_failure_memory_entry
from schema_validation import validate_records_against_schema
from vertical_operator_search import VerticalOperatorSearchRunner


O10 = "O10_evidence_sufficiency_ladder"
O11 = "O11_unobserved_state_attribution"
O15 = "O15_counterfactual_threshold_shift"


class FakeVerticalRunner(VerticalOperatorSearchRunner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.route_calls = []
        self.execute_calls = []
        self.seen_hash_snapshots = []

    def _route_frontier(self, parent_record, parent_node, state):
        self.route_calls.append(parent_node["node_id"])
        plan = [O10, O11] if parent_node["depth"] == 1 else [O15]
        routed = deepcopy(dict(parent_record))
        routed["operator_route"] = {
            "primary_operator": plan[0],
            "backup_operators": plan[1:],
            "avoid_operators": [],
            "selected_operator_ids": plan,
        }
        return routed, plan, {"operator_memory_sha256": "memory-v1"}

    def _execute_parent(self, prepared, parent_node, *, local_boundary_target):
        self.execute_calls.append(parent_node["node_id"])
        state = deepcopy(prepared["search_state"])
        self.seen_hash_snapshots.append(list(state["seen_prompt_hashes"]))
        artifacts = []
        boundary_count = 0
        for index, entry in enumerate(state["operator_plan"], start=1):
            if boundary_count >= local_boundary_target:
                break
            operator = entry["operator_id"]
            parent_score = float(parent_node["score_rate"])
            if parent_node["depth"] == 1 and operator == O10:
                child_score = 0.8
            elif parent_node["depth"] == 1:
                child_score = parent_score
            else:
                child_score = 0.6
            branch_status = (
                "boundary_candidate"
                if child_score < parent_score
                else "no_score_change"
            )
            entry["status"] = "completed"
            entry["branch_stage"] = "decision_completed"
            entry["generation_attempt_count"] = 1
            state["branch_summaries"][entry["branch_id"]] = {
                "branch_id": entry["branch_id"],
                "operator_id": operator,
                "branch_status": branch_status,
            }
            if branch_status == "boundary_candidate":
                boundary_count += 1
            record = {
                key: deepcopy(value)
                for key, value in prepared.items()
                if key != "search_state"
            }
            record.update(
                {
                    "sample_id": "runner-sample",
                    "branch_id": entry["branch_id"],
                    "candidate_id": entry["branch_id"],
                    "parent_node_id": parent_node["node_id"],
                    "operator_id": operator,
                    "candidate_operator": operator,
                    "prompt": f"prompt-depth-{int(parent_node['depth']) + 1}-{operator}",
                    "score_rate": child_score,
                    "parent_score_rate": parent_score,
                    "branch_status": branch_status,
                    "question_evolved": True,
                    "validation_result": {"passed": True},
                    "effect_analysis": {
                        "effect_label": "effective_boundary_probe"
                        if branch_status == "boundary_candidate"
                        else "no_clear_effect",
                        "complexity_passed": True,
                        "operator_used": operator,
                    },
                }
            )
            artifacts.append(
                {
                    "artifact_type": "complete_branch",
                    "branch_id": entry["branch_id"],
                    "parent_node_id": parent_node["node_id"],
                    "record": record,
                }
            )
        state["boundary_candidate_count"] = boundary_count
        state["status"] = "completed"
        state["termination_reason"] = (
            "boundary_target_reached"
            if boundary_count >= local_boundary_target
            else "candidate_list_exhausted"
        )
        return state, artifacts


def make_runner(tmp_path, **overrides):
    options = {
        "project_dir": ROOT,
        "work_dir": tmp_path / "vertical",
        "memory_dir": tmp_path / "memory",
        "branch_window": 1,
        "boundary_target": 5,
        "max_depth": 3,
        "allow_operator_repeat_in_path": False,
        "pipeline_mode": "step",
        "max_iterations": 100,
        "rule_only_difficulty": True,
        "defer_gpt_experimental_evaluation": False,
        "artifact_retention": "compact",
    }
    options.update(overrides)
    return FakeVerticalRunner(**options)


def sample():
    return {
        "sample_id": "runner-sample",
        "prompt": "root prompt",
        "score_rate": 1.0,
        "evolution_action": "evolve_high_score_overscore",
    }


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_runner_reuses_full_parent_closure_and_builds_depth_three_path(tmp_path):
    runner = make_runner(tmp_path)
    result = runner.run([sample()])
    state = result[0]["vertical_search_state"]
    assert state["termination_reason"] == "operator_space_exhausted"
    assert state["boundary_candidate_count"] == 2
    assert len(runner.route_calls) == len(runner.execute_calls) == 2
    assert validate_records_against_schema(
        result, ROOT / "schemas" / "pipeline_record.schema.json"
    ) == []

    nodes = read_jsonl(tmp_path / "vertical" / "vertical_nodes.jsonl")
    edges = read_jsonl(tmp_path / "vertical" / "boundary_edges.jsonl")
    paths = read_jsonl(tmp_path / "vertical" / "boundary_paths.jsonl")
    assert len(nodes) == 4
    assert validate_records_against_schema(
        nodes, ROOT / "schemas" / "vertical_node.schema.json"
    ) == []
    assert len(edges) == len(paths) == 2
    deepest = max(nodes, key=lambda row: row["depth"])
    assert deepest["depth"] == 3
    assert deepest["operator_stack"] == [O10, O15]
    assert deepest["frontier_status"] == "depth_limit"
    assert paths[-1]["operator_stack"] == [O10, O15]
    assert len(runner.seen_hash_snapshots[1]) > len(runner.seen_hash_snapshots[0])


def test_completed_checkpoint_does_not_repeat_nodes_or_parent_execution(tmp_path):
    first = make_runner(tmp_path)
    first.run([sample()])
    node_count = len(read_jsonl(tmp_path / "vertical" / "vertical_nodes.jsonl"))

    resumed = make_runner(tmp_path)
    result = resumed.run([sample()])
    assert result[0]["vertical_search_state"]["status"] == "completed"
    assert resumed.execute_calls == []
    assert len(read_jsonl(tmp_path / "vertical" / "vertical_nodes.jsonl")) == node_count


def test_request_budget_is_reported_as_system_protection_not_business_completion(tmp_path):
    runner = make_runner(tmp_path, max_request_attempts_per_sample=1)
    result = runner.run([sample()])
    state = result[0]["vertical_search_state"]
    assert state["status"] == "partial"
    assert state["termination_reason"] == "request_budget_exhausted"

    resumed = make_runner(tmp_path, max_request_attempts_per_sample=0)
    resumed_result = resumed.run([sample()])
    assert resumed_result[0]["vertical_search_state"]["status"] == "completed"
    assert "runner-sample::root" not in resumed.route_calls
    assert resumed.execute_calls[0] == "runner-sample::root"


def test_non_evolution_record_is_passed_through_without_vertical_state(tmp_path):
    runner = make_runner(tmp_path)
    record = sample()
    record["evolution_action"] = "stop_evolution"
    result = runner.run([record])
    assert "vertical_search_state" not in result[0]
    assert runner.execute_calls == []


def test_vertical_node_id_is_the_existing_memory_idempotency_scope(tmp_path):
    runner = make_runner(tmp_path)
    runner.run([sample()])
    nodes = read_jsonl(tmp_path / "vertical" / "vertical_nodes.jsonl")
    child = next(node for node in nodes if node["depth"] == 2)
    child["branch_id"] = child["node_id"]
    child["effect_analysis"] = {
        "operator_used": child["operator_from_parent"],
        "effect_label": "no_clear_effect",
        "score_rate_before": child["parent_score_rate"],
        "score_rate_after": child["score_rate"],
    }
    entry = build_failure_memory_entry(child)
    assert entry["memory_idempotency_key"] == f"{child['node_id']}::failure"


def test_summary_contains_operator_combination_and_budget_metrics(tmp_path):
    runner = make_runner(tmp_path)
    runner.run([sample()])
    summary = json.loads(
        (tmp_path / "vertical" / "vertical_search_summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["operator_metrics"][O10]["boundary_edge_hit_rate"] == 1.0
    pair = f"{O10}>{O15}"
    assert summary["combination_metrics"]["ordered_pair_occurrences"][pair] == 1
    assert summary["combination_metrics"]["ordered_pair_boundary_hit_rates"][pair] == 1.0
    assert summary["budget_metrics"]["evaluation_count"] == 3
    assert summary["budget_metrics"]["api_request_count"] >= 3
