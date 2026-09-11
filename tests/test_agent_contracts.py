"""Stage-1 contract gates, failure semantics, and frozen-snapshot guarantees."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime import policy
from agent_runtime.contracts import ContractViolation, load_contract
from agent_runtime.decisions import decide_next_action, write_decision
from agent_runtime.executor import Executor
from agent_runtime.global_memory import GlobalMemoryStore, SnapshotUnavailable
from agent_runtime.observer import OBSERVATION_TYPES, normalize_tool_result
from agent_runtime.planner import plan_env_overrides
from agent_runtime.state import initialize_state, save_state
from agent_runtime.task import REGISTERED_TOOLS, parse_agent_task
from agent_runtime.tools import TOOL_SPECS


def _task(tmp_path, **changes):
    raw = {"goal": "find boundaries", "input_file": "data/data.jsonl", "allowed_tools": ["check_environment"]}
    raw.update(changes)
    return parse_agent_task(raw, project_root=tmp_path)


def _step(tool, **changes):
    value = {
        "step_id": f"step_{tool}", "tool_name": tool, "tool": tool, "arguments": {},
        "preconditions": [], "expected_outputs": ["environment_checked"], "budget_limit": {},
        "depends_on": [], "stop_if_failed": True,
    }
    value.update(changes)
    return value


def _update(_run_dir, state, **changes):
    state.update(changes)
    return state


# --------------------------------------------------------------------------
# R-1: retryable system failures keep their recoverability.
# --------------------------------------------------------------------------


def test_retryable_tool_failure_is_not_promoted_to_fatal(tmp_path):
    class Registry:
        def check_environment(self, task):
            return {
                "tool": "check_environment", "ok": False, "return_code": -1,
                "recoverable": True, "failure_category": "retryable_system_error",
                "stderr_summary": "connection reset by peer",
            }

    state = {"completed_step_ids": []}
    executor = Executor(
        task=_task(tmp_path), plan={"plan_id": "plan-1", "env_overrides": {}}, registry=Registry(),
        run_dir=tmp_path / "run", state=state, observe=lambda *_a, **_k: {}, update_state=_update,
    )
    result = executor.execute_step(_step("check_environment"))

    assert result["ok"] is False
    assert result["recoverable"] is True
    assert result["failure_category"] == "retryable_system_error"

    observations = normalize_tool_result(result)
    assert [item["type"] for item in observations] == ["tool_retryable_failure"]

    decision = decide_next_action(_task(tmp_path), {"observations": observations, "status": "observed"}, tool_results=[result])
    assert decision["action"] == "suspend"
    assert decision["requires_human_review"] is False


def test_artifact_gate_still_escalates_to_fatal(tmp_path):
    exp = tmp_path / "experiments" / "day" / "exp"
    exp.mkdir(parents=True)

    class Registry:
        def check_environment(self, task):
            return {"tool": "check_environment", "ok": True, "ready": False, "return_code": 0}

    state = {"completed_step_ids": []}
    executor = Executor(
        task=_task(tmp_path), plan={"plan_id": "plan-1", "env_overrides": {}}, registry=Registry(),
        run_dir=tmp_path / "run", state=state, observe=lambda *_a, **_k: {}, update_state=_update,
    )
    result = executor.execute_step(_step("check_environment"))

    assert result["ok"] is False
    assert result["recoverable"] is False
    assert result["failure_category"] == "fatal_system_error"
    assert result["artifact_validation"] == "environment_not_ready"


# --------------------------------------------------------------------------
# M-1: the frozen snapshot freezes card content, not only card ids.
# --------------------------------------------------------------------------


def _seed_card(store):
    exp = store.project_root / "experiments" / "day" / "exp1"
    exp.mkdir(parents=True, exist_ok=True)
    source = exp / "memory" / "failure_memory_bank.jsonl"
    source.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "sample_id": "sample-1", "round": 2, "operator_used": "O16",
        "failure_type": "score_increased", "failure_reason": "O16 score_increased repeatedly",
        "sample_signature": {"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "joint conditions"},
    }
    source.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
    store.extract(exp)
    store.integrate()


def test_snapshot_refuses_a_card_whose_body_changed(tmp_path):
    store = GlobalMemoryStore(tmp_path)
    _seed_card(store)
    snapshot = store.create_snapshot()
    snapshot_id = snapshot["memory_snapshot_id"]

    assert "card_fingerprints" in snapshot
    assert store.verify_snapshot(snapshot_id)["verified"] is True
    assert store.retrieve(snapshot_id=snapshot_id, query="traffic necessity", top_k=3)["cards"]

    con = sqlite3.connect(store.db_path)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT card_id, body FROM cards").fetchone()
    body = json.loads(row["body"])
    body["reasoning_mechanism"] = "MUTATED-AFTER-SNAPSHOT"
    con.execute("UPDATE cards SET body = ? WHERE card_id = ?", (json.dumps(body), row["card_id"]))
    con.commit()
    con.close()

    report = store.verify_snapshot(snapshot_id)
    assert report["verified"] is False
    assert report["mismatches"][0]["reason"] == "card_body_or_version_changed_after_freeze"
    with pytest.raises(SnapshotUnavailable):
        store.retrieve(snapshot_id=snapshot_id, query="traffic necessity", top_k=3)

    degraded = store.retrieve(snapshot_id=snapshot_id, query="traffic necessity", top_k=3, strict_frozen=False)
    assert degraded["cards"] == []
    assert degraded["snapshot_integrity"]["status"] == "mismatch"


def test_snapshot_refuses_a_card_whose_version_advanced(tmp_path):
    store = GlobalMemoryStore(tmp_path)
    _seed_card(store)
    snapshot = store.create_snapshot()
    snapshot_id = snapshot["memory_snapshot_id"]

    exp = store.project_root / "experiments" / "day" / "exp1"
    source = exp / "memory" / "failure_memory_bank.jsonl"
    source.write_text(
        source.read_text(encoding="utf-8")
        + json.dumps({
            "sample_id": "sample-2", "round": 3, "operator_used": "O16",
            "failure_type": "score_decreased", "failure_reason": "O16 improved on the second sample",
            "sample_signature": {"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "joint conditions"},
        }, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    store.extract(exp)
    store.integrate()

    with pytest.raises(SnapshotUnavailable):
        store.retrieve(snapshot_id=snapshot_id, query="traffic necessity", top_k=3)


# --------------------------------------------------------------------------
# V-1 / V-2: contracts are enforced and cannot drift from the implementation.
# --------------------------------------------------------------------------


def test_decision_contract_enum_matches_policy():
    schema = load_contract("agent_decision.schema.json")
    assert set(schema["properties"]["action"]["enum"]) == policy.DECISIONS


def test_observation_contract_enum_matches_observer():
    schema = load_contract("agent_normalized_observation.schema.json")
    assert set(schema["properties"]["type"]["enum"]) == OBSERVATION_TYPES


def test_tool_registry_contract_matches_tool_specs():
    assert set(REGISTERED_TOOLS) == set(TOOL_SPECS)


def test_write_decision_rejects_an_unknown_action(tmp_path):
    with pytest.raises(ContractViolation):
        write_decision(tmp_path / "run", {"action": "explode", "reason": "x", "requires_human_review": False, "created_at": "t"})


def test_write_decision_accepts_replan_and_suspend(tmp_path):
    for action in ("replan", "suspend"):
        write_decision(tmp_path / "run", {"action": action, "reason": "x", "requires_human_review": False, "created_at": "t"})
    rows = (tmp_path / "run" / "agent_decisions.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(row)["action"] for row in rows] == ["replan", "suspend"]


def test_save_state_rejects_a_drifted_manifest(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True)
    state = initialize_state(run_dir, run_id="run", mode="run", root_goal="g")
    state["plan_revision"] = -1
    with pytest.raises(ContractViolation):
        save_state(run_dir, state)


# --------------------------------------------------------------------------
# O-1: plan env overrides come from one source and keep the snapshot identity.
# --------------------------------------------------------------------------


def test_plan_env_overrides_is_the_single_source_of_the_snapshot_id(tmp_path):
    task = _task(tmp_path)
    without = plan_env_overrides(task, command="run")
    assert "MEMORY_SNAPSHOT_ID" not in without

    with_snapshot = plan_env_overrides(task, command="run", memory_snapshot_id="MSNAP-frozen")
    assert with_snapshot["MEMORY_SNAPSHOT_ID"] == "MSNAP-frozen"
    assert with_snapshot["SEARCH_MODE"]
