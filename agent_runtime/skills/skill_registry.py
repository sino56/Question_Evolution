"""Static registry for Agent Skills.

The registry is deliberately declarative: a Skill describes a read-only
operating procedure and never grants a tool, mutation, or policy bypass.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping


SKILL_ROOT = Path(__file__).resolve().parents[2] / "agent_skills"
STAGES = {"post_experiment_review", "agent_reporting", "recovery_diagnosis", "memory_compilation", "strategy_proposal", "human_review_precheck", "planning_strategy", "multi_agent_advice", "model_routing"}
CONTEXT_LAYERS = {
    "task_context",
    "memory_context_summary",
    "dynamic_tail.observation_summary",
    "dynamic_tail.event_refs",
    "dynamic_tail.tool_results",
    "artifact_refs",
    "advisor_spec_context",
    "evidence_pack_slice",
    "advisor_dynamic_instruction",
}
GLOBAL_FORBIDDEN_ACTIONS = {
    "modify_score",
    "modify_prompt",
    "modify_router",
    "modify_rubric",
    "modify_operator",
    "modify_state",
    "publish_active_memory",
    "run_pipeline",
    "resume_pipeline",
}


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    stage: str
    required_inputs: tuple[str, ...]
    allowed_context_layers: tuple[str, ...]
    forbidden_actions: tuple[str, ...]
    output_schema: str
    version: str = "v1"

    @property
    def document_path(self) -> Path:
        return SKILL_ROOT / self.skill_id / "SKILL.md"

    def as_dict(self) -> dict:
        value = asdict(self)
        value["required_inputs"] = list(self.required_inputs)
        value["allowed_context_layers"] = list(self.allowed_context_layers)
        value["forbidden_actions"] = list(self.forbidden_actions)
        value["document_path"] = str(self.document_path)
        return value


_SPECS = (
    SkillSpec(
        "experiment-review-skill",
        "post_experiment_review",
        ("experiment_summary", "branch_results", "effect_analysis", "memory_summary"),
        ("task_context", "memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"),
        ("modify_score", "modify_operator", "publish_active_memory"),
        "experiment_review_skill_output.schema.json",
    ),
    SkillSpec(
        "agent-report-skill",
        "agent_reporting",
        ("agent_task", "agent_plan", "tool_events", "observation_summary", "decision_record"),
        ("task_context", "memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "dynamic_tail.tool_results", "artifact_refs"),
        ("modify_score", "modify_operator", "publish_active_memory"),
        "agent_report_skill_output.schema.json",
    ),
    SkillSpec(
        "recovery-diagnosis-skill",
        "recovery_diagnosis",
        ("agent_events", "tool_results", "checkpoint", "manifest", "termination_reason"),
        ("task_context", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "dynamic_tail.tool_results", "artifact_refs"),
        ("modify_state", "publish_active_memory", "resume_pipeline"),
        "recovery_diagnosis_skill_output.schema.json",
    ),
    SkillSpec(
        "memory-compile-skill",
        "memory_compilation",
        ("local_memory", "failure_memory", "invalid_generation_cases", "effect_analysis", "branch_results"),
        ("memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"),
        ("modify_state", "publish_active_memory", "modify_operator"),
        "memory_compile_skill_output.schema.json",
    ),
    SkillSpec(
        "strategy-proposal-skill",
        "strategy_proposal",
        ("strategy_card_drafts", "conflict_review", "replay_holdout", "human_review_records"),
        ("memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"),
        ("publish_active_memory", "modify_router", "modify_prompt", "modify_rubric", "modify_operator"),
        "strategy_proposal_skill_output.schema.json",
    ),
    SkillSpec(
        "operator-diagnosis-skill",
        "post_experiment_review",
        ("operator_id", "candidate_question", "parent_question", "validation_result", "score_change"),
        ("task_context", "memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"),
        ("modify_operator", "modify_router", "modify_score", "publish_active_memory"),
        "operator_diagnosis_skill_output.schema.json",
    ),
    SkillSpec(
        "human-review-precheck-skill",
        "human_review_precheck",
        ("candidate_question", "parent_question", "score_result", "validation_result", "mechanism_analysis"),
        ("task_context", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"),
        ("modify_score", "modify_state", "publish_active_memory"),
        "human_review_precheck_skill_output.schema.json",
    ),
    SkillSpec(
        "planning-strategy-skill",
        "planning_strategy",
        ("agent_task", "budget", "allowed_tools", "memory_top_k", "observation_summary"),
        ("task_context", "memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"),
        ("run_pipeline", "resume_pipeline", "modify_router", "modify_score", "publish_active_memory"),
        "planning_strategy_skill_output.schema.json",
    ),
    SkillSpec(
        "multi-agent-advisor-skill",
        "multi_agent_advice",
        ("advisor_spec_context", "evidence_pack_slice", "allowed_tools", "output_schema"),
        ("advisor_spec_context", "evidence_pack_slice", "advisor_dynamic_instruction", "artifact_refs"),
        ("run_pipeline", "resume_pipeline", "modify_prompt", "modify_router", "modify_score", "publish_active_memory"),
        "multi_agent_advisor_skill_output.schema.json",
    ),
    SkillSpec(
        "model-routing-skill",
        "model_routing",
        ("advisor_spec", "task_risk", "budget", "evidence_pack_slice_hash", "json_and_evidence_requirements"),
        ("advisor_spec_context", "evidence_pack_slice", "artifact_refs"),
        ("run_pipeline", "modify_score", "modify_prompt", "publish_active_memory"),
        "model_routing_skill_output.schema.json",
    ),
)
REGISTRY: dict[str, SkillSpec] = {item.skill_id: item for item in _SPECS}


def validate_spec(spec: SkillSpec, *, skill_root: Path = SKILL_ROOT) -> None:
    if not spec.skill_id or spec.stage not in STAGES:
        raise ValueError("skill specification has an invalid id or stage")
    if not spec.required_inputs or not spec.allowed_context_layers or not spec.output_schema:
        raise ValueError("skill specification must declare inputs, context layers, and an output schema")
    if not set(spec.allowed_context_layers).issubset(CONTEXT_LAYERS):
        raise ValueError("skill specification requests a forbidden context layer")
    if not set(spec.forbidden_actions).issubset(GLOBAL_FORBIDDEN_ACTIONS):
        raise ValueError("skill specification has an unknown forbidden action")
    if not (skill_root / spec.skill_id / "SKILL.md").is_file():
        raise ValueError(f"skill document is missing: {spec.skill_id}")
    schema = Path(__file__).resolve().parents[2] / "schemas" / spec.output_schema
    if not schema.is_file():
        raise ValueError(f"skill output schema is missing: {spec.output_schema}")


def validate_registry(registry: Mapping[str, SkillSpec] | None = None, *, skill_root: Path = SKILL_ROOT) -> None:
    for key, spec in (registry or REGISTRY).items():
        if key != spec.skill_id:
            raise ValueError("skill registry key must equal skill_id")
        validate_spec(spec, skill_root=skill_root)


def get_skill(skill_id: str) -> SkillSpec:
    try:
        spec = REGISTRY[skill_id]
    except KeyError as exc:
        raise ValueError(f"unregistered skill: {skill_id}") from exc
    validate_spec(spec)
    return spec


def list_skills(*, stage: str | None = None) -> list[SkillSpec]:
    items = [item for item in REGISTRY.values() if stage is None or item.stage == stage]
    for item in items:
        validate_spec(item)
    return sorted(items, key=lambda item: item.skill_id)
