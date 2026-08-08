from pathlib import Path

import pytest

from agent_runtime.skills.skill_loader import SkillOutputRejected, SkillRequestRejected, load_stage_skills, validate_skill_output, validate_skill_request
from agent_runtime.skills.skill_registry import REGISTRY, validate_registry
from agent_runtime.reporter import write_agent_report


ROOT = Path(__file__).resolve().parents[1]


def test_registered_p0_skills_have_documents_schemas_and_narrow_context_contracts():
    validate_registry()
    assert {"experiment-review-skill", "agent-report-skill", "recovery-diagnosis-skill"}.issubset(REGISTRY)
    for spec in REGISTRY.values():
        assert spec.document_path.is_file()
        assert (ROOT / "schemas" / spec.output_schema).is_file()


def test_unregistered_skills_and_full_context_requests_are_rejected():
    with pytest.raises(ValueError, match="unregistered skill"):
        validate_skill_request("unregistered-skill", requested_context_layers=[])
    with pytest.raises(SkillRequestRejected, match="forbidden context"):
        validate_skill_request("experiment-review-skill", requested_context_layers=["complete_parent_context"])


def test_skill_load_failure_writes_event_and_falls_back_to_base_rules(tmp_path):
    result = load_stage_skills(
        "agent_reporting",
        requested_context_layers=["task_context", "dynamic_tail.observation_summary"],
        skill_root=tmp_path,
        event_path=tmp_path / "agent_events.jsonl",
    )
    assert result.fallback_to_base_rules is True
    assert not result.loaded
    event = (tmp_path / "agent_events.jsonl").read_text(encoding="utf-8")
    assert "skill_load_failed" in event and "base_rules" in event


def test_skill_output_requires_auditable_references_and_cannot_request_mutations():
    with pytest.raises(SkillOutputRejected, match="requires evidence_refs"):
        validate_skill_output("experiment-review-skill", {"summary": "unsupported"})
    accepted = validate_skill_output("experiment-review-skill", {"summary": "candidate only", "outcome_type": "no_gain", "evidence_refs": [{"path": "round_1/effect.jsonl"}]})
    assert accepted["skill_id"] == "experiment-review-skill"
    with pytest.raises(SkillOutputRejected, match="forbidden action"):
        validate_skill_output("experiment-review-skill", {"evidence_refs": [{"path": "x"}], "requested_actions": ["modify_score"]})


def test_missing_required_input_material_degrades_to_base_rules(tmp_path):
    result = load_stage_skills(
        "agent_reporting",
        requested_context_layers=["task_context", "dynamic_tail.observation_summary"],
        available_inputs=["agent_task"],
        event_path=tmp_path / "agent_events.jsonl",
    )
    assert result.fallback_to_base_rules is True
    assert "required input material" in result.failures[0]


def test_incomplete_skill_document_fails_the_registry_loader_contract(tmp_path):
    document = tmp_path / "agent-report-skill" / "SKILL.md"
    document.parent.mkdir()
    document.write_text("# agent-report-skill\n\n## 1. Applicable scenarios\n", encoding="utf-8")
    result = load_stage_skills(
        "agent_reporting",
        requested_context_layers=["task_context", "dynamic_tail.observation_summary"],
        skill_root=tmp_path,
    )
    assert result.fallback_to_base_rules is True
    assert "required section" in result.failures[0]


def test_agent_report_loads_its_registered_skill_and_records_auditable_event(tmp_path):
    write_agent_report(
        tmp_path,
        task={"goal": "review"},
        state={},
        plan={"selected_search_mode": "single_branch", "selected_execution_scope": "full_iteration", "budget": {}},
        observation={"evidence_refs": [{"path": "round_1/effect_analysis.jsonl"}]},
        tool_results=[],
        decision={"action": "stop_and_report", "reason": "done"},
    )
    events = (tmp_path / "agent_events.jsonl").read_text(encoding="utf-8")
    assert "skill_loaded" in events and "agent-report-skill" in events
