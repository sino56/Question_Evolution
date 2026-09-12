import time
from dataclasses import replace

from agent_runtime.multi_agent.advisor_dispatcher import select_advisors
from agent_runtime.multi_agent.advisor_executor import AdvisorExecutor
from agent_runtime.multi_agent.advisor_registry import get_advisor
from agent_runtime.multi_agent.evidence_pack import build_evidence_pack
from agent_runtime.multi_agent.coordinator import run_advisor_stage
from agent_runtime.multi_agent.advice_merge import merge_advice


def _pack(tmp_path):
    return build_evidence_pack(tmp_path, task={"goal": "review"}, state={"agent_run_id": "run", "memory_snapshot_id": "m"}, observation={"experiment_dir": "x", "status": "observed", "evidence_refs": [{"path": "x"}]})


def test_post_experiment_trigger_selects_five_read_only_advisors():
    selected = select_advisors("post_experiment_review", {"status": "observed"})
    assert {item.advisor_id for item in selected} == {"router_diagnosis", "operator_generation_diagnosis", "validation_diagnosis", "scoring_stability", "search_cost"}


def test_advisor_failure_does_not_stop_other_advisors(tmp_path):
    def handler(spec, context, selection):
        if spec.advisor_id == "router_diagnosis":
            raise RuntimeError("expected test failure")
        return {"summary": "ok", "findings": [], "forbidden_actions_requested": []}

    records, advice = AdvisorExecutor(tmp_path, parent_run_id="run", handler=handler).execute([get_advisor("router_diagnosis"), get_advisor("search_cost")], _pack(tmp_path))
    assert {record["status"] for record in records} == {"failed", "completed"}
    assert len(advice) == 2


def test_timeout_is_recorded_as_timeout_not_completed(tmp_path):
    spec = replace(get_advisor("search_cost"), max_runtime_seconds=1)

    def handler(spec, context, selection):
        time.sleep(1.1)
        return {"summary": "late", "findings": [], "forbidden_actions_requested": []}

    records, _ = AdvisorExecutor(tmp_path, parent_run_id="run", handler=handler).execute([spec], _pack(tmp_path))
    assert records[0]["status"] == "timeout"


def test_unwhitelisted_tool_request_is_rejected_by_merge(tmp_path):
    def handler(spec, context, selection):
        return {"summary": "bad", "findings": [], "requested_tools": ["spawn_advisor"]}

    pack = _pack(tmp_path)
    _, advice = AdvisorExecutor(tmp_path, parent_run_id="run", handler=handler).execute([get_advisor("router_diagnosis")], pack)
    merged = merge_advice(tmp_path, advice_items=advice, evidence_pack=pack)
    assert merged["policy_rejections"][0]["reason"] == "policy_rejected"


def test_memory_stage_is_serial_and_review_synthesis_follows_prechecks(tmp_path):
    common = {"task": {"goal": "review"}, "state": {"agent_run_id": "run", "memory_snapshot_id": "m"}, "plan": {"plan_id": "p"}, "observation": {"experiment_dir": "x", "status": "observed", "evidence_refs": [{"path": "x"}]}}
    memory = run_advisor_stage(tmp_path / "memory", stage="memory_compilation", **common)
    assert [record["advisor_id"] for record in memory["advisor_records"]] == ["fact_extraction", "classification_mapping", "strategy_induction", "conflict_review", "publication_precheck"]
    review = run_advisor_stage(tmp_path / "review", stage="human_review_precheck", **common)
    assert review["advisor_records"][-1]["advisor_id"] == "review_synthesis"


def test_timeout_keeps_the_task_id_audit_chain_and_suppresses_late_output(tmp_path):
    import json as _json

    spec = replace(get_advisor("search_cost"), max_runtime_seconds=1, retry_count=0)

    def handler(spec, context, selection):
        time.sleep(1.2)
        return {"summary": "late", "findings": [], "forbidden_actions_requested": []}

    executor = AdvisorExecutor(tmp_path, parent_run_id="run", handler=handler)
    records, _ = executor.execute([spec], _pack(tmp_path))

    timeout_ids = [record["advisor_task_id"] for record in records if record["status"] == "timeout"]
    assert timeout_ids, "expected a timeout record"
    started = []
    events_path = tmp_path / "multi_agent" / "advisor_events.jsonl"
    for line in events_path.read_text(encoding="utf-8").splitlines():
        event = _json.loads(line)
        if event.get("event_type") == "advisor_started":
            started.append(event["advisor_task_id"])
    # The timeout record must reference the same advisor task that started.
    assert set(timeout_ids).issubset(set(started))
    # A late finishing worker must not publish a contradicting output file.
    output = tmp_path / "multi_agent" / "advice" / spec.advisor_id / "advisor_output.json"
    assert not output.exists()


def test_deterministic_handlers_with_injected_models_are_labelled_deterministic(tmp_path, monkeypatch):
    monkeypatch.delenv("ADVISOR_BASE_URL", raising=False)
    monkeypatch.delenv("ADVISOR_API_KEY", raising=False)

    def handler(spec, context, selection):
        return {"summary": "ok", "findings": [], "forbidden_actions_requested": []}

    records, _ = AdvisorExecutor(
        tmp_path, parent_run_id="run", handler=handler, models={"reasoning_high": "external-model"},
    ).execute([get_advisor("router_diagnosis")], _pack(tmp_path))

    # Without provider credentials the advice is deterministic; recording it
    # under the external model name would overstate the evidence (V-8).
    assert records[0]["selected_model"] == "local-deterministic-advisor"


def test_plan_candidates_stage_is_retired_from_the_registry():
    from agent_runtime.multi_agent.advisor_registry import STAGES, list_advisors

    assert "plan_candidates" not in STAGES
    assert list_advisors(stage="plan_candidates") == []
