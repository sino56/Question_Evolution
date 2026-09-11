"""Stage-2 tests: observation/decision contracts, truncation, and redaction."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.context_layers import _bounded
from agent_runtime.decisions import decide_next_action
from agent_runtime.events import redact
from agent_runtime.executor import Executor
from agent_runtime.global_memory import GlobalMemoryStore
from agent_runtime.observer import BUDGET_TERMINAL_REASONS, _artifact_integrity, normalize_tool_result, observe_experiment
from agent_runtime.planner import build_plan
from agent_runtime.task import parse_agent_task


def _task(tmp_path, **changes):
    raw = {
        "goal": "find boundaries", "input_file": "data/data.jsonl",
        "allowed_tools": ["check_environment", "run_full_loop", "observe_experiment", "write_agent_report"],
    }
    raw.update(changes)
    return parse_agent_task(raw, project_root=tmp_path)


def _update(_run_dir, state, **changes):
    state.update(changes)
    return state


# --------------------------------------------------------------------------
# C-1: bounded context fields stay valid, traceable JSON.
# --------------------------------------------------------------------------


def test_bounded_truncation_is_legal_json_with_overflow_pointer():
    value = {"kept": "x", "huge": "y" * 5000, "tail": ["z"] * 100}
    bounded = _bounded(value, 200)

    rendered = json.dumps(bounded, ensure_ascii=False, sort_keys=True)
    assert len(rendered) <= 200 + 400
    overflow = bounded["__overflow__"]
    assert overflow["sha256"].startswith("sha256:")
    assert overflow["original_chars"] > 200
    # Every retained value is a whole field, never a half-cut JSON token.
    assert bounded.get("kept") == "x"
    assert "..." not in rendered


def test_bounded_keeps_small_values_untouched():
    value = {"a": 1}
    assert _bounded(value, 1000) == value


# --------------------------------------------------------------------------
# C-4: redaction targets credentials without destroying audit identifiers.
# --------------------------------------------------------------------------


def test_redact_keeps_audit_keys_and_redacts_credentials():
    payload = {
        "idempotency_key": "abc123",
        "key_findings": ["a", "b"],
        "tool_call_id": "call_1",
        "api_key": "sk-live-should-not-survive",
        "authorization": "Bearer xyz",
        "base_url": "http://internal",
        "artifact_url": "https://example.com/artifact/final.jsonl",
    }
    safe = redact(payload)

    assert safe["idempotency_key"] == "abc123"
    assert safe["key_findings"] == ["a", "b"]
    assert safe["tool_call_id"] == "call_1"
    assert safe["api_key"] == "[REDACTED]"
    assert safe["authorization"] == "[REDACTED]"
    assert safe["base_url"] == "[REDACTED]"
    assert safe["artifact_url"] == "https://example.com/artifact/final.jsonl"


def test_redact_still_strips_credential_bearing_urls_and_bearer_tokens():
    text = "endpoint https://user:pass@internal.example/api and Bearer deadbeefcafe"
    safe = redact(text)
    assert "user:pass@" not in safe
    assert "Bearer deadbeefcafe" not in safe


# --------------------------------------------------------------------------
# V-4: manifest integrity is a three-state contract.
# --------------------------------------------------------------------------


def test_artifact_integrity_reports_ok_damaged_and_not_checked(tmp_path, monkeypatch):
    exp = tmp_path / "exp"
    exp.mkdir()

    status, damaged = _artifact_integrity(exp)
    assert (status, damaged) == ("not_checked", [])

    (exp / "final_scored.jsonl").write_text("{}\n", encoding="utf-8")
    (exp / "final_scored.jsonl.manifest.json").write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr("agent_runtime.observer.validate_published_artifact", lambda *_a, **_k: (True, "ok"))
    assert _artifact_integrity(exp) == ("ok", [])

    monkeypatch.setattr("agent_runtime.observer.validate_published_artifact", lambda *_a, **_k: (False, "sha mismatch"))
    status, damaged = _artifact_integrity(exp)
    assert status == "damaged"
    assert "sha mismatch" in damaged[0]


def test_observe_step_treats_not_checked_manifest_as_unmet(tmp_path):
    exp = tmp_path / "experiments" / "day" / "exp"
    exp.mkdir(parents=True)

    class Registry:
        pass

    def fake_observe(_exp_dir, *, run_dir, **_kwargs):
        (Path(run_dir) / "agent_observation.json").write_text("{}\n", encoding="utf-8")
        return {"status": "observed", "manifest_status": "not_checked", "evidence_refs": []}

    state = {"completed_step_ids": []}
    task = _task(
        tmp_path, input_file="", review_mode="report_only",
        resume_exp_dir="experiments/day/exp", allowed_tools=["observe_experiment", "write_agent_report"],
    )
    executor = Executor(
        task=task, plan={"plan_id": "p", "env_overrides": {}}, registry=Registry(),
        run_dir=tmp_path / "run", state=state, observe=fake_observe, update_state=_update,
    )
    step = {
        "step_id": "observe_experiment", "tool_name": "observe_experiment", "tool": "observe_experiment",
        "arguments": {"experiment_dir": str(exp)}, "preconditions": ["published_manifest_validation_required"],
        "expected_outputs": ["agent_observation.json"], "budget_limit": {}, "depends_on": [], "stop_if_failed": True,
    }
    result = executor.execute_step(step)

    assert result["ok"] is False
    assert result["recoverable"] is False
    assert "published_manifest_not_checked" in result["artifact_validation"]


# --------------------------------------------------------------------------
# V-3: the design's reflector observations are reachable and drive decisions.
# --------------------------------------------------------------------------


def _aggregate(**changes):
    value = {
        "status": "observed", "manifest_status": "not_checked", "experiment_dir": "/tmp/exp",
        "final_records_count": 3, "pending_count": 0, "boundary_candidate_count": 0,
        "score_increased_count": 0, "not_applicable_count": 0, "validation_failed_count": 0,
        "branch_error_count": 0, "target_reached": False, "status_counts": {},
        "memory_summary": {"banks": {}}, "evidence_refs": [],
    }
    value.update(changes)
    return value


def test_effective_boundary_found_observation_and_decision(tmp_path):
    observations = normalize_tool_result(
        {"tool": "observe_experiment", "ok": True, "observation": _aggregate(target_reached=True, boundary_candidate_count=2)},
        experiment_observation=_aggregate(target_reached=True, boundary_candidate_count=2),
    )
    types = [item["type"] for item in observations]
    assert "effective_boundary_found" in types

    decision = decide_next_action(_task(tmp_path), {"observations": observations, "status": "observed"})
    assert decision["action"] == "stop_and_report"
    assert decision["terminal_reason"] == "effective_boundary_found"
    assert decision["requires_human_review"] is True


def test_score_increase_still_outranks_effective_boundary(tmp_path):
    aggregate = _aggregate(target_reached=True, boundary_candidate_count=1, score_increased_count=1)
    observations = normalize_tool_result(
        {"tool": "observe_experiment", "ok": True, "observation": aggregate},
        experiment_observation=aggregate,
    )
    decision = decide_next_action(_task(tmp_path), {"observations": observations, "status": "observed"})
    assert decision["action"] == "stop_and_report"
    assert decision["terminal_reason"] == "manual_review_required"


def test_judge_instability_observation_and_decision(tmp_path):
    aggregate = _aggregate(judge_stability={"status": "unstable", "judge_instability_rate": 0.25})
    observations = normalize_tool_result(
        {"tool": "observe_experiment", "ok": True, "observation": aggregate},
        experiment_observation=aggregate,
    )
    assert "judge_instability_detected" in [item["type"] for item in observations]

    decision = decide_next_action(_task(tmp_path), {"observations": observations, "status": "observed"})
    assert decision["action"] == "suspend"
    assert decision["terminal_reason"] == "judge_instability"


def test_memory_written_observation(tmp_path):
    aggregate = _aggregate(memory_summary={"banks": {"operator_memory_bank.jsonl": {"record_count": 4, "missing": False}}})
    observations = normalize_tool_result(
        {"tool": "observe_experiment", "ok": True, "observation": aggregate},
        experiment_observation=aggregate,
    )
    assert "memory_written" in [item["type"] for item in observations]


def test_new_observation_types_are_produced_by_observe_experiment(tmp_path):
    exp = tmp_path / "exp"
    (exp / "memory").mkdir(parents=True)
    (exp / "experiment_statistics.json").write_text(
        json.dumps({"termination_reason": "boundary_target_reached", "judge_instability_rate": 0.5}), encoding="utf-8"
    )
    observation = observe_experiment(exp, boundary_target=1)

    assert observation["judge_stability"]["status"] == "unstable"
    assert observation["manifest_status"] == "not_checked"
    types = {item["type"] for item in observation["observations"]}
    assert "judge_instability_detected" in types


# --------------------------------------------------------------------------
# O-6: budget exhaustion must be an exact terminal reason.
# --------------------------------------------------------------------------


def test_budget_exhaustion_requires_an_exact_terminal_reason(tmp_path):
    noisy = decide_next_action(_task(tmp_path), {"status": "observed", "termination_reason": "budget_observation_ready"})
    assert noisy["terminal_reason"] != "budget_observation_ready"

    real = decide_next_action(_task(tmp_path), {"status": "observed", "termination_reason": "evaluation_budget_exhausted"})
    assert real["action"] == "stop_and_report"
    assert real["terminal_reason"] == "evaluation_budget_exhausted"
    assert real["requires_human_review"] is False

    for reason in BUDGET_TERMINAL_REASONS:
        assert decide_next_action(_task(tmp_path), {"status": "observed", "termination_reason": reason})["terminal_reason"] == reason


# --------------------------------------------------------------------------
# O-4: decisions carry the links needed to audit them.
# --------------------------------------------------------------------------


def test_decision_carries_correlation_keys(tmp_path):
    decision = decide_next_action(
        _task(tmp_path),
        {"status": "observed", "termination_reason": "evaluation_budget_exhausted"},
        session_id="agent_1", plan_id="plan_1", plan_revision=3, observation_id="obs-deadbeef",
    )
    assert decision["session_id"] == "agent_1"
    assert decision["plan_id"] == "plan_1"
    assert decision["plan_revision"] == 3
    assert decision["observation_id"] == "obs-deadbeef"


# --------------------------------------------------------------------------
# O-3: model-assisted planning may only edit prose.
# --------------------------------------------------------------------------


def test_model_plan_cannot_change_step_arguments_or_budget(tmp_path):
    from agent_runtime.planner import _deterministic_plan

    task = _task(tmp_path, planning_mode="model_assisted")

    def client(_context):
        plan = deepcopy(_deterministic_plan(task, command="run"))
        plan["steps"][1]["budget_limit"] = {"max_tool_calls": 999}
        plan["steps"][1]["stop_if_failed"] = False
        return plan

    result = build_plan(task, command="run", context_pack={}, model_client=client)

    assert result["planner_source"] == "deterministic"
    assert result.get("model_fallback_reason") == "PolicyViolation"
    assert result["steps"][1]["stop_if_failed"] is True
    assert result["steps"][1]["budget_limit"] != {"max_tool_calls": 999}


def test_model_plan_prose_edits_are_merged_and_authoritative_fields_kept(tmp_path):
    from agent_runtime.planner import _deterministic_plan

    task = _task(tmp_path, planning_mode="model_assisted")

    def client(_context):
        baseline = _deterministic_plan(task, command="run")
        plan = deepcopy(baseline)
        plan["goal_summary"] = "model-refined goal"
        plan["assumptions"] = ["refined assumption"]
        plan["steps"][0]["intent"] = "refined intent"
        plan["plan_id"] = "model-invented-id"
        return plan

    result = build_plan(task, command="run", context_pack={}, model_client=client)

    assert result["planner_source"] == "model_assisted"
    assert result["goal_summary"] == "model-refined goal"
    assert result["assumptions"] == ["refined assumption"]
    assert result["steps"][0]["intent"] == "refined intent"
    # Protected fields always come from the deterministic baseline.
    baseline = _deterministic_plan(task, command="run")
    assert result["steps"][0]["budget_limit"] == baseline["steps"][0]["budget_limit"]
    assert result["env_overrides"] == baseline["env_overrides"]
    assert result["plan_id"].startswith("plan_")
    assert result["plan_id"] != "model-invented-id"
