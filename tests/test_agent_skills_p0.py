from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
P0_SKILLS = (
    "experiment-review-skill",
    "agent-report-skill",
    "recovery-diagnosis-skill",
)
REQUIRED_SECTIONS = (
    "Applicable scenarios",
    "Input materials",
    "Prohibited actions",
    "Workflow",
    "Output structure",
    "Failure fallback",
    "Acceptance criteria",
)


def test_p0_skill_documents_follow_the_required_operating_procedure_shape():
    for skill_id in P0_SKILLS:
        content = (ROOT / "agent_skills" / skill_id / "SKILL.md").read_text(encoding="utf-8")
        assert content.startswith(f"# {skill_id}\n")
        for section in REQUIRED_SECTIONS:
            assert section in content
        assert "evidence_refs" in content
        assert "active Memory" in content


def test_recovery_skill_prohibits_same_condition_rerun_after_negative_gain():
    content = (ROOT / "agent_skills" / "recovery-diagnosis-skill" / "SKILL.md").read_text(encoding="utf-8")
    assert "score_increased" in content
    assert "相同条件重复重跑" in content
