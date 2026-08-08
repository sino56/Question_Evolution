from pathlib import Path

import pytest

import question_evolution_agent as cli
from agent_runtime.multi_agent.advisor_executor import AdvisorExecutor
from agent_runtime.multi_agent.advisor_registry import get_advisor
from agent_runtime.multi_agent.evidence_pack import build_evidence_pack
from agent_runtime.multi_agent.model_router import select_model
from agent_runtime.skills import load_stage_skills
from agent_runtime.skills.skill_registry import get_skill
from agent_runtime.task import parse_agent_task


ROOT = Path(__file__).resolve().parents[1]
P2_SKILLS = {
    "planning-strategy-skill": "planning_strategy",
    "multi-agent-advisor-skill": "multi_agent_advice",
    "model-routing-skill": "model_routing",
}


def test_p2_skills_have_registered_docs_schemas_and_narrow_context_contracts():
    for skill_id, stage in P2_SKILLS.items():
        spec = get_skill(skill_id)
        assert spec.stage == stage
        content = (ROOT / "agent_skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")
        for heading in ("Applicable scenarios", "Input materials", "Prohibited actions", "Output structure", "Acceptance criteria"):
            assert heading in content
        assert (ROOT / "schemas" / spec.output_schema).is_file()


def test_planner_advisor_and_model_routing_skills_load_for_their_runtime_stages():
    planning = load_stage_skills("planning_strategy", requested_context_layers=["task_context", "memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"])
    advisor = load_stage_skills("multi_agent_advice", requested_context_layers=["advisor_spec_context", "evidence_pack_slice", "advisor_dynamic_instruction", "artifact_refs"])
    routing = load_stage_skills("model_routing", requested_context_layers=["advisor_spec_context", "evidence_pack_slice", "artifact_refs"])
    assert [item.spec.skill_id for item in planning.loaded] == ["planning-strategy-skill"]
    assert [item.spec.skill_id for item in advisor.loaded] == ["multi-agent-advisor-skill"]
    assert [item.spec.skill_id for item in routing.loaded] == ["model-routing-skill"]


def test_high_risk_model_routing_cannot_downgrade_to_low_cost_extraction():
    with pytest.raises(ValueError, match="cannot fall back"):
        select_model("synthesis_high", "extract_low_cost", models={"extract_low_cost": "cheap"})
    selection = select_model("synthesis_high", "reasoning_medium", models={"reasoning_medium": "review-model"})
    assert selection.model_tier == "reasoning_medium"
    assert selection.fallback_used is True


def test_dry_run_records_planning_skill_load_event(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    task = parse_agent_task({"goal": "plan a bounded review", "input_file": "data/data.jsonl"}, project_root=tmp_path)
    code, run_dir = cli.run_agent("dry-run", task)
    assert code == 0
    events = (run_dir / "agent_events.jsonl").read_text(encoding="utf-8")
    assert "planning-strategy-skill" in events and "skill_loaded" in events


def test_advisor_execution_records_its_skill_load_without_formal_tool_access(tmp_path):
    pack = build_evidence_pack(
        tmp_path,
        task={"goal": "review"},
        state={"agent_run_id": "run", "memory_snapshot_id": "snapshot"},
        observation={"status": "observed", "evidence_refs": [{"path": "round_1/effect_analysis.jsonl"}]},
    )
    AdvisorExecutor(tmp_path, parent_run_id="run", handler=lambda *_: {"summary": "ok", "findings": [], "forbidden_actions_requested": []}).execute([get_advisor("router_diagnosis")], pack)
    events = (tmp_path / "multi_agent" / "advisor_events.jsonl").read_text(encoding="utf-8")
    assert "multi-agent-advisor-skill" in events and "skill_loaded" in events
