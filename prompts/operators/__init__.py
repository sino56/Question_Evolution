from typing import Any, Dict

from .O10_evidence_sufficiency_ladder import SPEC as O10_SPEC
from .O11_unobserved_state_attribution import SPEC as O11_SPEC
from .O12_conjunctive_necessity import SPEC as O12_SPEC
from .O13_minimal_disqualifier import SPEC as O13_SPEC
from .O14_information_closure import SPEC as O14_SPEC
from .O15_counterfactual_threshold_shift import SPEC as O15_SPEC
from .O16_close_alternative_normalization import SPEC as O16_SPEC
from .O17_action_vs_fact_threshold import SPEC as O17_SPEC
from .O18_baseline_scope_mismatch import SPEC as O18_SPEC
from .base import OperatorPromptSpec, build_prompt
from .new_operator_specs import NEW_OPERATOR_SPECS


_LEGACY_OPERATOR_SPECS = {
    spec.operator_id: spec
    for spec in (
        O10_SPEC,
        O11_SPEC,
        O12_SPEC,
        O13_SPEC,
        O14_SPEC,
        O15_SPEC,
        O16_SPEC,
        O17_SPEC,
        O18_SPEC,
    )
}
_duplicate_ids = set(_LEGACY_OPERATOR_SPECS).intersection(NEW_OPERATOR_SPECS)
if _duplicate_ids:
    raise RuntimeError(
        "duplicate operator IDs: " + ", ".join(sorted(_duplicate_ids))
    )
OPERATOR_SPECS = {**_LEGACY_OPERATOR_SPECS, **NEW_OPERATOR_SPECS}
GENERATION_OPERATOR_SPECS = {
    operator_id: spec
    for operator_id, spec in OPERATOR_SPECS.items()
    if spec.generates_question
}


def get_operator_spec(operator_id: str) -> OperatorPromptSpec:
    try:
        return OPERATOR_SPECS[operator_id]
    except KeyError as exc:
        raise ValueError(f"Unknown operator_id: {operator_id}") from exc


def build_operator_prompt(
    operator_id: str,
    *,
    prompt: str,
    reference_answer: str,
    candidate_answer: str,
    rubric: Any,
    sample_profile: Dict[str, Any],
    overscore_diagnosis: Dict[str, Any],
    evolution_state: Dict[str, Any],
    operator_route: Dict[str, Any],
    operator_manifest: Dict[str, Any],
    fact_ledger: Any,
) -> str:
    spec = get_operator_spec(operator_id)
    if not spec.generates_question:
        raise ValueError(f"operator_id={operator_id} is validation_only and cannot generate questions")
    return build_prompt(
        spec,
        prompt=prompt,
        reference_answer=reference_answer,
        candidate_answer=candidate_answer,
        rubric=rubric,
        sample_profile=sample_profile,
        overscore_diagnosis=overscore_diagnosis,
        evolution_state=evolution_state,
        operator_route=operator_route,
        operator_manifest=operator_manifest,
        fact_ledger=fact_ledger if isinstance(fact_ledger, list) else [],
    )
