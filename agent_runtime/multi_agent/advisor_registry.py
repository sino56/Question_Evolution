"""Versioned registry for advisor-only tasks; advisors never receive formal write tools."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Dict, Iterable, Mapping

MODEL_TIERS = {"extract_low_cost", "reasoning_medium", "reasoning_high", "synthesis_high"}
STAGES = {"post_experiment_review", "memory_compilation", "plan_candidates", "human_review_precheck"}
GLOBAL_FORBIDDEN_TOOLS = {"spawn_advisor", "ask_user", "run_full_loop", "resume_full_loop", "write_formal_artifact", "publish_active_memory", "modify_prompt", "modify_operator", "modify_score"}
ALLOWED_TOOLS = {"read_evidence_pack", "read_artifact_ref", "read_memory_ledger", "read_context_pack", "read_policy_snapshot", "read_candidate_summary", "write_temp_advice", "write_strategy_card_draft", "write_plan_candidate", "write_review_precheck"}


@dataclass(frozen=True)
class AdvisorSpec:
    advisor_id: str
    name: str
    stage: str
    purpose: str
    trigger: str
    allowed_inputs: tuple[str, ...]
    allowed_tools: tuple[str, ...]
    model_tier: str
    fallback_model_tier: str
    requires_json_output: bool = True
    requires_evidence_refs: bool = True
    output_schema: str = "advisor_advice.schema.json"
    max_runtime_seconds: int = 120
    max_input_chars: int = 30000
    retry_count: int = 1
    failure_policy: str = "warn_and_continue"
    version: str = "v1"

    def as_dict(self) -> dict:
        value = asdict(self)
        value["allowed_inputs"] = list(self.allowed_inputs)
        value["allowed_tools"] = list(self.allowed_tools)
        return value


def _review(advisor_id: str, purpose: str, *, tier: str = "reasoning_high") -> AdvisorSpec:
    return AdvisorSpec(advisor_id, advisor_id.replace("_", " "), "post_experiment_review", purpose, "experiment_finished", ("evidence_pack.summary", "evidence_pack.observations", "evidence_pack.evidence_refs"), ("read_evidence_pack", "read_artifact_ref", "write_temp_advice"), tier, "reasoning_medium")


def _memory(advisor_id: str, purpose: str, *, tier: str = "reasoning_high") -> AdvisorSpec:
    return AdvisorSpec(advisor_id, advisor_id.replace("_", " "), "memory_compilation", purpose, "memory_compilation_requested", ("evidence_pack.summary", "evidence_pack.memory_summary", "evidence_pack.evidence_refs"), ("read_memory_ledger", "read_evidence_pack", "write_strategy_card_draft"), tier, "reasoning_medium")


def _plan(advisor_id: str, purpose: str) -> AdvisorSpec:
    return AdvisorSpec(advisor_id, advisor_id.replace("_", " "), "plan_candidates", purpose, "plan_candidates_requested", ("evidence_pack.summary", "evidence_pack.observations", "evidence_pack.snapshot_ids"), ("read_context_pack", "read_policy_snapshot", "write_plan_candidate"), "reasoning_high", "reasoning_medium")


def _precheck(advisor_id: str, purpose: str) -> AdvisorSpec:
    return AdvisorSpec(advisor_id, advisor_id.replace("_", " "), "human_review_precheck", purpose, "human_review_precheck_requested", ("evidence_pack.summary", "evidence_pack.observations", "evidence_pack.evidence_refs"), ("read_candidate_summary", "read_artifact_ref", "write_review_precheck"), "reasoning_medium", "extract_low_cost")


_SPECS = (
    _review("router_diagnosis", "Diagnose operator-selection risks without changing routes."),
    _review("operator_generation_diagnosis", "Diagnose generation failure causes without changing prompts."),
    _review("validation_diagnosis", "Diagnose validation risks without overriding validation."),
    _review("scoring_stability", "Assess scoring stability; all conclusions remain advisory."),
    _review("search_cost", "Identify high-cost low-yield search behavior."),
    _memory("fact_extraction", "Extract attributable experiment facts.", tier="extract_low_cost"),
    _memory("classification_mapping", "Map profiles to stable retrieval keys.", tier="extract_low_cost"),
    _memory("strategy_induction", "Induce proposed strategy-card drafts."),
    _memory("conflict_review", "Find conflicts with failure and instability evidence."),
    _memory("publication_precheck", "Recommend only proposed, shadow, review, or rejection states.", tier="synthesis_high"),
    _plan("conservative_plan", "Produce a low-risk, budget-constrained candidate plan."),
    _plan("exploration_plan", "Produce a bounded horizontal-search candidate plan."),
    _plan("vertical_stack_plan", "Assess a bounded two-layer operator plan."),
    _plan("recovery_plan", "Produce a checkpoint-preserving recovery candidate plan."),
    _precheck("boundary_quality", "Classify boundary-evidence quality for human review."),
    _precheck("answerability", "Precheck whether a candidate remains answerable."),
    _precheck("leakage_risk", "Precheck answer and scaffold leakage risk."),
    _precheck("mechanism_hit", "Precheck target reasoning-mechanism evidence."),
    _precheck("review_synthesis", "Synthesize prechecks into a prioritised human-review list."),
)
REGISTRY: Dict[str, AdvisorSpec] = {spec.advisor_id: spec for spec in _SPECS}


def validate_spec(spec: AdvisorSpec) -> None:
    if not spec.advisor_id or spec.stage not in STAGES or spec.model_tier not in MODEL_TIERS or spec.fallback_model_tier not in MODEL_TIERS:
        raise ValueError("advisor specification is incomplete")
    if not spec.requires_json_output or not spec.output_schema or not spec.allowed_inputs:
        raise ValueError("advisor must require structured output and declared inputs")
    if not set(spec.allowed_tools).issubset(ALLOWED_TOOLS) or set(spec.allowed_tools) & GLOBAL_FORBIDDEN_TOOLS:
        raise ValueError("advisor requests forbidden or unregistered tools")
    if spec.max_runtime_seconds < 1 or spec.max_input_chars < 1 or spec.retry_count < 0:
        raise ValueError("advisor limits are invalid")


def get_advisor(advisor_id: str) -> AdvisorSpec:
    try:
        spec = REGISTRY[advisor_id]
    except KeyError as exc:
        raise ValueError(f"unregistered advisor: {advisor_id}") from exc
    validate_spec(spec)
    return spec


def list_advisors(*, stage: str | None = None) -> list[AdvisorSpec]:
    result = [spec for spec in REGISTRY.values() if stage is None or spec.stage == stage]
    for spec in result:
        validate_spec(spec)
    return sorted(result, key=lambda item: item.advisor_id)


def validate_registry(registry: Mapping[str, AdvisorSpec] | None = None) -> None:
    for key, spec in (registry or REGISTRY).items():
        if key != spec.advisor_id:
            raise ValueError("advisor registry key must equal advisor_id")
        validate_spec(spec)
