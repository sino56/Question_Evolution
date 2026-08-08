from pathlib import Path

import pytest

from agent_runtime.skills import SkillOutputRejected, load_stage_skills, validate_skill_output
from agent_runtime.skills.skill_registry import get_skill
from agent_runtime.global_judge import build_evidence_pack, run_global_judge


ROOT = Path(__file__).resolve().parents[1]
P1_SKILLS = {
    "memory-compile-skill": "memory_compilation",
    "strategy-proposal-skill": "strategy_proposal",
    "operator-diagnosis-skill": "post_experiment_review",
    "human-review-precheck-skill": "human_review_precheck",
}


def test_p1_skills_are_registered_with_auditable_documents_and_schemas():
    for skill_id, stage in P1_SKILLS.items():
        spec = get_skill(skill_id)
        assert spec.stage == stage
        content = (ROOT / "agent_skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")
        assert "## 2. Input materials" in content
        assert "## 3. Prohibited actions" in content
        assert "## 5. Output structure" in content
        assert "## 7. Acceptance criteria" in content
        assert (ROOT / "schemas" / spec.output_schema).is_file()


def test_p1_loads_by_its_runtime_stages_without_broad_context_access():
    memory = load_stage_skills(
        "memory_compilation",
        requested_context_layers=["memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"],
    )
    precheck = load_stage_skills(
        "human_review_precheck",
        requested_context_layers=["task_context", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"],
    )
    review = load_stage_skills(
        "post_experiment_review",
        requested_context_layers=["task_context", "memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"],
    )
    assert [item.spec.skill_id for item in memory.loaded] == ["memory-compile-skill"]
    assert [item.spec.skill_id for item in precheck.loaded] == ["human-review-precheck-skill"]
    assert {item.spec.skill_id for item in review.loaded} >= {"experiment-review-skill", "operator-diagnosis-skill"}


def test_memory_proposal_and_review_skills_cannot_publish_or_confirm_final_state():
    refs = [{"path": "round_1/effect_analysis.jsonl"}]
    with pytest.raises(SkillOutputRejected, match="active"):
        validate_skill_output("memory-compile-skill", {"status": "active", "evidence_refs": refs})
    with pytest.raises(SkillOutputRejected, match="human boundary confirmation"):
        validate_skill_output("human-review-precheck-skill", {"confirmed_boundary": True, "evidence_refs": refs})
    with pytest.raises(SkillOutputRejected, match="forbidden action"):
        validate_skill_output("operator-diagnosis-skill", {"evidence_refs": refs, "requested_actions": ["modify_operator"]})


def test_operator_and_human_review_documents_preserve_the_required_governance_boundaries():
    operator = (ROOT / "agent_skills" / "operator-diagnosis-skill" / "SKILL.md").read_text(encoding="utf-8")
    precheck = (ROOT / "agent_skills" / "human-review-precheck-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "单次 `not_applicable` 禁用整个算子" in operator
    assert "不得替代人工确认" in precheck


def test_global_judge_loads_the_strategy_proposal_procedure(tmp_path):
    experiment = tmp_path / "experiments" / "day" / "exp"
    experiment.mkdir(parents=True)
    (experiment / "round_1").mkdir()
    (experiment / "round_1" / "branch_results.jsonl").write_text(
        '{"sample_id":"s","branch_status":"score_increased","operator_used":"O16"}\n', encoding="utf-8"
    )
    report = run_global_judge(build_evidence_pack(experiment, project_root=tmp_path, snapshot={"memory_snapshot_id": "snapshot"}))
    assert report["skill_load"]["loaded_skill_ids"] == ["strategy-proposal-skill"]
