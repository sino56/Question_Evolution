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
from .O19_multi_entity_role_binding import SPEC as O19_SPEC
from .O20_multistage_event_breakpoint import SPEC as O20_SPEC
from .O21_object_provenance_identity import SPEC as O21_SPEC
from .O22_path_topology_reachability import SPEC as O22_SPEC
from .O23_observation_reliability_conflict import SPEC as O23_SPEC
from .O24_multi_hypothesis_residual_ranking import SPEC as O24_SPEC
from .O25_procedural_invariant_frame import SPEC as O25_SPEC
from .O26_quantitative_threshold_propagation import SPEC as O26_SPEC
from .O27_cross_layer_conclusion_calibration import SPEC as O27_SPEC
from .O28_multihop_chain_closure import SPEC as O28_SPEC
from .O29_entity_identity_conflict_resolution import SPEC as O29_SPEC
from .O30_active_discriminative_observation import SPEC as O30_SPEC
from .O31_observation_accumulation_calibration import SPEC as O31_SPEC
from .O32_role_graph_critical_edge import SPEC as O32_SPEC
from .O33_cross_modal_support_boundary import SPEC as O33_SPEC
from .base import OperatorPromptSpec, build_prompt


OPERATOR_SPECS = {
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
        O19_SPEC,
        O20_SPEC,
        O21_SPEC,
        O22_SPEC,
        O23_SPEC,
        O24_SPEC,
        O25_SPEC,
        O26_SPEC,
        O27_SPEC,
        O28_SPEC,
        O29_SPEC,
        O30_SPEC,
        O31_SPEC,
        O32_SPEC,
        O33_SPEC,
    )
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
    generator_visible_context: Dict[str, Any] | None = None,
) -> str:
    return build_prompt(
        get_operator_spec(operator_id),
        prompt=prompt,
        reference_answer=reference_answer,
        candidate_answer=candidate_answer,
        rubric=rubric,
        sample_profile=sample_profile,
        overscore_diagnosis=overscore_diagnosis,
        evolution_state=evolution_state,
        operator_route=operator_route,
        generator_visible_context=generator_visible_context,
    )
