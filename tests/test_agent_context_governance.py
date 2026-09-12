"""Stage-6a regressions: context confluence and L3 procedural memory.

Each test maps to one finding of
``docs/Agent改造方案/Agent_Harness_代码审查报告_2026-09-11.md``:

- C-2 the cache identity is reproducible between the plan-less and persisted build.
- C-3 legacy aliases are derived views of the v2 layers, so they cannot diverge.
- C-5 a structured ``world_state`` layer is derived from the Session manifest.
- C-6 a token budget accompanies the character bound.
- C-7 raw run logs are no longer injected into the control layer.
- M-7 the L3 procedural rule library is versioned and verified against runtime.
- M-9 snapshots are reused by content and can be pruned.
- V-1 the published context_pack_v2 schema is enforced before writing.
"""

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import question_evolution_agent as cli
from agent_runtime.context import build_context_pack
from agent_runtime.context_layers import (
    CONTEXT_TOKEN_BUDGET,
    TOOL_REGISTRY_ORDER,
    estimate_tokens,
    token_budget,
)
from agent_runtime.contracts import ContractViolation
from agent_runtime.decisions import decide_next_action
from agent_runtime.global_memory import GlobalMemoryStore
from agent_runtime.planner import build_plan
from agent_runtime.policy import _REQUIRED_TOOL_OUTPUTS
from agent_runtime.procedural import (
    PROCEDURAL_DIR,
    ProceduralMemoryError,
    load_procedural_memory,
    verify_against_runtime,
)
from agent_runtime.recovery import RECIPES, system_failure_categories
from agent_runtime.state import initialize_state, write_context, write_plan_revision
from agent_runtime.task import parse_agent_task
from agent_runtime.tools import RetryPolicy, classify_system_failure, get_tool_spec


def _task(tmp_path, **changes):
    raw = {
        "goal": "find a stable reasoning boundary",
        "input_file": "data/data.jsonl",
        "allowed_tools": list(TOOL_REGISTRY_ORDER),
    }
    raw.update(changes)
    return parse_agent_task(raw, project_root=tmp_path)


def _auto_task(tmp_path):
    """An ``auto`` task is the only case where the search mode is *resolved*."""

    return _task(tmp_path, search_mode="auto", goal="自动选择搜索模式")


# --------------------------------------------------------------------------- C-2


def test_cache_identity_is_reproducible_before_and_after_planning(tmp_path):
    task = _auto_task(tmp_path)
    plan = build_plan(task, command="run")

    plan_less = build_context_pack(task)
    persisted = build_context_pack(task, plan=plan)

    assert plan_less["task_context"]["selected_search_mode"] == plan["selected_search_mode"]
    assert plan_less["context_cache"]["context_cache_key"] == persisted["context_cache"]["context_cache_key"]


def test_explicit_search_mode_is_untouched(tmp_path):
    task = _task(tmp_path, search_mode="single_branch")
    pack = build_context_pack(task)
    assert pack["task_context"]["selected_search_mode"] == "single_branch"


# --------------------------------------------------------------------------- C-3


def test_legacy_aliases_are_derived_from_the_v2_layers(tmp_path):
    task = _task(tmp_path)
    plan = build_plan(task, command="run")
    observation = {"memory_summary": {"banks": {}}, "pending_count": 3}
    decision = {"action": "stop_and_report", "reason": "x"}

    pack = build_context_pack(task, plan=plan, observation=observation, previous_decision=decision)

    assert pack["selected_plan"] == pack["dynamic_tail"]["selected_plan"]
    assert pack["observation_summary"] == pack["dynamic_tail"]["observation_summary"]
    assert pack["previous_decision"] == pack["dynamic_tail"]["last_decision"]
    assert pack["memory_summary"] == pack["world_state"]["candidate_state"]


def test_aliases_still_agree_after_compaction(tmp_path):
    task = _task(tmp_path)
    # Many mid-size fields survive the per-layer bound, so the whole pack
    # exceeds ``max_chars`` and the compaction branch runs.
    bulky = {f"field_{index:03d}": "y" * 500 for index in range(60)}
    bulky["pending_count"] = 2

    pack = build_context_pack(task, observation=bulky, max_chars=10000)

    assert pack["token_budget"]["compacted"] is True
    assert pack["observation_summary"] == pack["dynamic_tail"]["observation_summary"]
    assert pack["selected_plan"] == pack["dynamic_tail"]["selected_plan"]
    assert pack["previous_decision"] == pack["dynamic_tail"]["last_decision"]
    assert len(json.dumps(pack, ensure_ascii=False)) <= 60000


# --------------------------------------------------------------------------- C-5


def test_world_state_is_derived_from_the_session_manifest(tmp_path):
    run_dir = tmp_path / "run"
    state = initialize_state(run_dir, run_id="agent_world", mode="run")
    first = write_plan_revision(run_dir, state, {"plan_id": "plan_first", "steps": []})
    second = write_plan_revision(run_dir, state, {"plan_id": "plan_second", "steps": []})
    state["budgets"] = {"max_search_steps": 10, "remaining": {"max_search_steps": 4}}
    state["memory_snapshot_id"] = "MSNAP-x"

    pack = build_context_pack(
        _task(tmp_path), plan=second, runtime_state=state,
        observation={"pending_count": 3, "boundary_candidate_count": 1, "target_reached": False},
        run_dir=run_dir,
    )
    world = pack["world_state"]

    assert world["derived_from"] == "session_manifest+published_observation"
    assert world["plan_revision"] == 2
    assert world["current_plan_id"] == "plan_second"
    assert world["frozen_memory"]["memory_snapshot_id"] == "MSNAP-x"
    assert world["budget"]["remaining"] == {"max_search_steps": 4}
    assert world["candidate_state"]["pending_count"] == 3
    # Only the superseded revisions are rollback candidates.
    assert [point["plan_id"] for point in world["rollback_points"]] == ["plan_first"]
    assert world["rollback_points"][0]["path"].endswith("plan_r001.json")
    assert first["plan_revision"] == 1


# --------------------------------------------------------------------------- C-6


def test_token_estimate_distinguishes_cjk_from_ascii():
    assert estimate_tokens("中文上下文字符") == 7
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("") == 0


def test_token_budget_is_reported_and_enforced(tmp_path):
    pack = build_context_pack(_task(tmp_path))
    assert pack["token_budget"]["budget_tokens"] == CONTEXT_TOKEN_BUDGET
    assert pack["token_budget"]["within_budget"] is True
    assert set(pack["token_budget"]["per_layer"]) >= {"stable_prefix", "world_state", "dynamic_tail"}

    tiny = build_context_pack(_task(tmp_path), max_tokens=50)
    assert tiny["token_budget"]["budget_tokens"] == 50
    assert tiny["token_budget"]["within_budget"] is False
    assert token_budget(tiny, budget=1)["within_budget"] is False


# --------------------------------------------------------------------------- C-7


def test_raw_run_logs_are_projected_not_injected(tmp_path):
    pack = build_context_pack(
        _task(tmp_path),
        runtime_state={"stdout_summary": "raw stdout line", "stderr_summary": "raw stderr line", "parse_errors": ["a", "b"]},
    )
    dynamic = pack["dynamic_tail"]

    for removed in ("stdout_summary", "stderr_summary", "parse_errors"):
        assert removed not in dynamic
    diagnostics = dynamic["runtime_diagnostics"]
    assert diagnostics["parse_error_count"] == 2
    assert diagnostics["stderr_summary"]["chars"] == len("raw stderr line")
    assert diagnostics["stderr_summary"]["sha256"].startswith("sha256:")
    assert diagnostics["stderr_summary"]["excerpt"] == "raw stderr line"
    # The hard constraint says logs must not be injected: keep the layers clean.
    assert "raw stdout line" not in json.dumps(pack["stable_prefix"], ensure_ascii=False)


# --------------------------------------------------------------------------- M-7


def test_procedural_rule_library_is_versioned_and_loadable():
    memory = load_procedural_memory(ROOT)

    assert memory.version == "procedural-v2"
    assert memory.content_hash.startswith("sha256:")
    assert memory.source_files == ("agent_procedural_rules.json",)
    assert (ROOT / PROCEDURAL_DIR / "agent_procedural_rules.json").is_file()
    assert set(memory.rules) >= {"tool_order", "retry_and_fail_fast", "budget", "rollback", "publish_gate", "approval"}


def test_procedural_rules_are_verified_against_the_runtime(tmp_path):
    memory = load_procedural_memory(ROOT)

    assert memory.rule("tool_order")["sequence"] == list(TOOL_REGISTRY_ORDER)

    retry = memory.rule("retry_and_fail_fast")
    assert set(retry["failure_categories"]) == set(system_failure_categories())
    assert retry["multiplier"] == RetryPolicy().multiplier
    assert retry["max_backoff_seconds"] == RetryPolicy().max_backoff_seconds
    for name, declared in retry["per_tool"].items():
        spec = get_tool_spec(name)
        assert declared["max_attempts"] == spec.retry_policy.max_attempts
        assert declared["backoff_seconds"] == spec.retry_policy.backoff_seconds
    declared_retryable = {
        category for category in retry["failure_categories"]
        if classify_system_failure(f"ERROR_CATEGORY={category}")[1]
    }
    assert sorted(declared_retryable) == sorted(retry["retryable_categories"])

    budget = memory.rule("budget")
    assert budget["session_max_rounds"] == cli.MAX_SESSION_ROUNDS
    assert budget["session_max_rollbacks"] == cli.MAX_SESSION_ROLLBACKS
    assert budget["step_max_tool_calls"] == build_plan(_task(tmp_path), command="run")["steps"][0]["budget_limit"]["max_tool_calls"]

    rollback = memory.rule("rollback")
    derived = sorted({item for recipe in RECIPES if recipe.recovery_action == "rollback_and_retry" for item in recipe.observation_types})
    assert sorted(rollback["allowed_when"]) == derived
    assert rollback["max_per_session"] == cli.MAX_SESSION_ROLLBACKS

    gate = memory.rule("publish_gate")
    assert gate["required_outputs"] == dict(_REQUIRED_TOOL_OUTPUTS)
    run_step = next(step for step in build_plan(_task(tmp_path), command="run")["steps"] if step["tool_name"] == "run_full_loop")
    assert sorted(gate["run_full_loop_preconditions"]) == sorted(run_step["preconditions"])

    approval = memory.rule("approval")
    for observation_type in approval["human_required_for"]:
        observation = {
            "status": "observed",
            "observations": [{"type": observation_type}],
            "score_increased_count": 1 if observation_type == "score_increased" else 0,
            "target_reached": observation_type == "effective_boundary_found",
            "boundary_candidate_count": 1 if observation_type == "effective_boundary_found" else 0,
        }
        assert decide_next_action(_task(tmp_path), observation)["requires_human_review"] is True

    assert verify_against_runtime(memory, runtime={"tool_order": memory.rule("tool_order")}) == []
    assert verify_against_runtime(memory, runtime={"tool_order": {"sequence": ["wrong"]}}) != []


def test_procedural_library_rejects_a_malformed_rule_file(tmp_path):
    directory = tmp_path / PROCEDURAL_DIR
    directory.mkdir(parents=True)
    (directory / "bad.json").write_text(json.dumps({"rule_id": "x", "version": "v1"}), encoding="utf-8")

    # A malformed library is a hard error: it must never be silently ignored.
    with pytest.raises(ProceduralMemoryError, match="missing required fields"):
        load_procedural_memory(tmp_path)
    with pytest.raises(ProceduralMemoryError, match="missing required fields"):
        load_procedural_memory(tmp_path, required=False)

    (directory / "bad.json").unlink()
    assert load_procedural_memory(tmp_path, required=False).version == "procedural-unavailable"
    with pytest.raises(ProceduralMemoryError, match="missing"):
        load_procedural_memory(tmp_path)


def test_context_snapshot_prefix_records_the_procedural_revision(tmp_path):
    pack = build_context_pack(_task(tmp_path))
    prefix = pack["snapshot_prefix"]

    assert prefix["procedural_memory_version"] == "procedural-v2"
    assert prefix["procedural_memory_hash"].startswith("sha256:")
    assert prefix["skill_content_hash"].startswith("sha256:")
    assert "procedural_rules" in pack["stable_prefix"]


# --------------------------------------------------------------------------- V-1


def test_write_context_enforces_the_published_schema(tmp_path):
    with pytest.raises(ContractViolation):
        write_context(tmp_path, {"context_schema_version": "context-pack-v2"})

    pack = build_context_pack(_task(tmp_path))
    write_context(tmp_path, pack)
    assert (tmp_path / "agent_context.json").is_file()


# --------------------------------------------------------------------------- M-9


def test_snapshot_identity_is_reused_instead_of_rewritten(tmp_path):
    store = GlobalMemoryStore(tmp_path)
    first = store.create_snapshot()
    second = store.create_snapshot()

    assert second["memory_snapshot_id"] == first["memory_snapshot_id"]
    assert second["reused"] is True
    assert second["created_at"] == first["created_at"]
    assert first["reused"] is False


def test_prune_snapshots_keeps_the_declared_count_and_protects_ids(tmp_path):
    store = GlobalMemoryStore(tmp_path)
    directory = store.root / "snapshots"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(5):
        (directory / f"MSNAP-{index:04d}.json").write_text(
            json.dumps({"memory_snapshot_id": f"MSNAP-{index:04d}", "created_at": f"2026-01-0{index + 1}T00:00:00+00:00"}),
            encoding="utf-8",
        )

    report = store.prune_snapshots(keep=2, protect=["MSNAP-0000"])

    assert sorted(report["removed"]) == ["MSNAP-0001.json", "MSNAP-0002.json", "MSNAP-0003.json"]
    assert (directory / "MSNAP-0000.json").is_file()
    assert (directory / "MSNAP-0004.json").is_file()
    with pytest.raises(Exception):
        store.prune_snapshots(keep=0)
