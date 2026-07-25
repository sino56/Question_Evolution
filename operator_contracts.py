"""Machine-readable contracts for the repaired O10-O18 operators.

The content prompt specs describe what an operator measures.  This module owns
the mechanism-level identity, versions, applicability prerequisites, generated
payload schema, answer-contract freezing and release-check metadata.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from prompts.operators import OPERATOR_SPECS


ENABLED = "enabled"
DISABLED = "disabled"
VALIDATION_ONLY = "validation_only"
QUALIFICATION_ONLY = "qualification_only"
SHADOW_ROUTING = "shadow_routing"
SUSPENDED = "suspended"
RETIRED = "retired"
OPERATOR_STATUSES = {
    ENABLED,
    DISABLED,
    VALIDATION_ONLY,
    QUALIFICATION_ONLY,
    SHADOW_ROUTING,
    SUSPENDED,
    RETIRED,
}

ELIGIBLE = "eligible"
NOT_APPLICABLE = "not_applicable"
REJECT_CANDIDATE = "reject_candidate"
DIAGNOSTIC_RISK = "diagnostic_risk"
OPERATOR_SPACE_EXHAUSTED = "operator_space_exhausted"

ANSWER_CONTRACT_VERSION = "answer_contract_v2"
VALIDATION_POLICY_VERSION = "operator_validation_v2"

FORBIDDEN_FACT_TYPES = ("example", "suggestion", "external_knowledge")


@dataclass(frozen=True)
class OperatorContract:
    operator_id: str
    semantic_version: str
    prompt_version: str
    applicability_version: str
    validation_policy_version: str
    evidence_status: str
    ability_axis: str
    ability_axes: Sequence[str]
    reasoning_object: str
    preserved_parent_obligations: Sequence[str]
    required_reasoning_output: Sequence[str]
    target_error_taxonomy: Sequence[str]
    excluded_error_taxonomy: Sequence[str]
    required_fact_slots: Sequence[str]
    forbidden_fact_types: Sequence[str]
    transformation_contract: Mapping[str, Any]
    invariants: Sequence[str]
    answer_space: Mapping[str, Any]
    operator_payload_schema: Mapping[str, Any]
    answer_contract_schema: Mapping[str, Any]
    scorer_mapping: Mapping[str, Any]
    neighbor_operators: Sequence[str]
    routing_exclusions: Sequence[str]
    release_checks: Sequence[Mapping[str, str]]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _payload_schema(
    required: Sequence[str],
    properties: Mapping[str, str],
) -> Dict[str, Any]:
    return {
        "type": "object",
        "required": list(required),
        "properties": dict(properties),
        # Unknown fields are deliberately retained for forward-compatible
        # replay.  They are never silently stripped by this module.
        "additionalProperties": True,
    }


def _release_checks(*operator_specific: str) -> Tuple[Dict[str, str], ...]:
    common = (
        {"check": "public_envelope_complete", "kind": "deterministic"},
        {"check": "operator_payload_complete", "kind": "deterministic"},
        {"check": "fact_ids_authorized", "kind": "deterministic"},
        {"check": "answer_contract_consistent", "kind": "deterministic"},
        {"check": "parent_reasoning_obligation_drift", "kind": "diagnostic"},
        {"check": "surface_swap_sensitivity", "kind": "diagnostic"},
        {"check": "cross_operator_isomorphism", "kind": "diagnostic"},
    )
    return common + tuple(
        {"check": name, "kind": "deterministic"} for name in operator_specific
    )


def _contract(
    operator_id: str,
    *,
    prompt_version: str,
    evidence_status: str,
    required_fact_slots: Sequence[str],
    payload_required: Sequence[str],
    payload_properties: Mapping[str, str],
    transformation_contract: Mapping[str, Any],
    answer_space: Mapping[str, Any],
    scorer_fields: Sequence[str],
    release_checks: Sequence[str] = (),
    status: str = ENABLED,
) -> OperatorContract:
    spec = OPERATOR_SPECS[operator_id]
    return OperatorContract(
        operator_id=operator_id,
        semantic_version="2.0",
        prompt_version=prompt_version,
        applicability_version=f"{operator_id.split('_', 1)[0].lower()}_applicability_v2",
        validation_policy_version=VALIDATION_POLICY_VERSION,
        evidence_status=evidence_status,
        ability_axis=spec.ability_axis,
        ability_axes=tuple(spec.ability_axes or (spec.ability_axis,)),
        reasoning_object=spec.reasoning_object,
        preserved_parent_obligations=tuple(spec.preserved_parent_obligations),
        required_reasoning_output=tuple(spec.required_reasoning_tasks),
        target_error_taxonomy=tuple(spec.target_error_taxonomy),
        excluded_error_taxonomy=tuple(spec.excluded_error_taxonomy),
        required_fact_slots=tuple(required_fact_slots),
        forbidden_fact_types=FORBIDDEN_FACT_TYPES,
        transformation_contract=dict(transformation_contract),
        invariants=tuple(spec.invariants),
        answer_space=dict(answer_space),
        operator_payload_schema=_payload_schema(payload_required, payload_properties),
        answer_contract_schema={
            "type": "object",
            "required": ["answer_key", "decisive_fact_ids", "rubric_assertions"],
            "additionalProperties": True,
        },
        scorer_mapping={
            "answer_contract_version": ANSWER_CONTRACT_VERSION,
            "rubric_fields": list(scorer_fields),
        },
        neighbor_operators=(),
        routing_exclusions=(),
        release_checks=_release_checks(*release_checks),
        status=status,
    )


OPERATOR_CONTRACTS: Dict[str, OperatorContract] = {
    "O10_evidence_sufficiency_ladder": _contract(
        "O10_evidence_sufficiency_ladder",
        prompt_version="o10_prompt_v3",
        evidence_status="experiment_driven_revision",
        required_fact_slots=("target_claim", "observable_fact_ids", "minimal_sufficient_fact_ids"),
        payload_required=(
            "minimal_sufficient_fact_ids",
            "related_nonmember_fact_ids",
            "set_connection",
            "ablation_results",
        ),
        payload_properties={
            "minimal_sufficient_fact_ids": "array",
            "related_nonmember_fact_ids": "array",
            "set_connection": "string",
            "ablation_results": "object",
        },
        transformation_contract={
            "allowed_transforms": ["add_related_fact", "remove_required_member", "surface_reorder"],
            "max_semantic_axes": 1,
        },
        answer_space={"relation": ["sufficient", "insufficient"], "fixed_direction_labels": False},
        scorer_fields=(
            "identify_minimal_sufficient_fact_ids",
            "explain_fact_set_connection",
            "separate_relevant_from_sufficient_facts",
        ),
        release_checks=("minimal_set_member_ablation_valid",),
    ),
    "O11_unobserved_state_attribution": _contract(
        "O11_unobserved_state_attribution",
        prompt_version="o11_prompt_v3",
        evidence_status="qualification_hypothesis",
        required_fact_slots=(
            "entry_endpoint",
            "exit_endpoint",
            "time_window",
            "path_constraints",
            "candidate_hypotheses",
        ),
        payload_required=(
            "endpoint_fact_ids",
            "time_window_fact_ids",
            "path_constraint_fact_ids",
            "candidate_hypotheses",
            "consistency_result",
        ),
        payload_properties={
            "endpoint_fact_ids": "array",
            "time_window_fact_ids": "array",
            "path_constraint_fact_ids": "array",
            "candidate_hypotheses": "array",
            "consistency_result": "object",
        },
        transformation_contract={
            "allowed_transforms": ["reorder_hypotheses", "vary_endpoint_compatible_hypothesis"],
            "forbid_unobserved_event_invention": True,
        },
        answer_space={"relation": ["consistent", "inconsistent", "underdetermined"]},
        scorer_fields=(
            "map_hypotheses_to_endpoints",
            "check_time_window_consistency",
            "check_path_constraint_consistency",
        ),
        release_checks=("no_unobserved_event_asserted_as_fact",),
        status=DISABLED,
    ),
    "O12_conjunctive_necessity": _contract(
        "O12_conjunctive_necessity",
        prompt_version="o12_prompt_v3",
        evidence_status="experiment_driven_revision",
        required_fact_slots=("target_claim", "fact_x_id", "fact_y_id"),
        payload_required=("fact_x_id", "fact_y_id", "version_outcomes", "joint_closure"),
        payload_properties={
            "fact_x_id": "string",
            "fact_y_id": "string",
            "version_outcomes": "object",
            "joint_closure": "boolean",
        },
        transformation_contract={
            "allowed_transforms": ["present_x_only", "present_y_only", "present_xy", "surface_reorder"],
            "preserve_scenario_comparability": True,
        },
        answer_space={"versions": ["x_only", "y_only", "xy"], "joint_closure": "boolean"},
        scorer_fields=(
            "assess_x_independent_contribution",
            "assess_y_independent_contribution",
            "assess_xy_joint_closure",
        ),
        release_checks=("version_comparability_preserved",),
    ),
    "O13_minimal_disqualifier": _contract(
        "O13_minimal_disqualifier",
        prompt_version="o13_prompt_v3",
        evidence_status="experiment_driven_revision",
        required_fact_slots=("target_claim", "required_link_id", "candidate_fact_ids"),
        payload_required=(
            "selected_fact_id",
            "broken_link_id",
            "claim_level_effect",
            "alternative_support_fact_ids",
        ),
        payload_properties={
            "selected_fact_id": "string",
            "broken_link_id": "string",
            "claim_level_effect": "string",
            "alternative_support_fact_ids": "array",
        },
        transformation_contract={
            "allowed_transforms": ["add_review_fact", "surface_reorder"],
            "preserve_target_claim": True,
        },
        answer_space={
            "claim_level_effect": [
                "confidence_only",
                "local_link_broken",
                "overall_claim_weakened",
                "overall_claim_reversed",
                "local_link_broken_overall_supported",
            ]
        },
        scorer_fields=("selected_fact_id", "broken_link_id", "claim_level_effect"),
        release_checks=("link_effect_level_consistent",),
    ),
    "O14_information_closure": _contract(
        "O14_information_closure",
        prompt_version="o14_validation_v2",
        evidence_status="validation_policy",
        required_fact_slots=(),
        payload_required=("findings",),
        payload_properties={"findings": "array"},
        transformation_contract={"allowed_transforms": []},
        answer_space={"validation_findings_only": True},
        scorer_fields=("map_surface_facts_to_fact_ids", "check_authorized_transforms"),
        release_checks=("global_information_closure",),
        status=VALIDATION_ONLY,
    ),
    "O15_counterfactual_threshold_shift": _contract(
        "O15_counterfactual_threshold_shift",
        prompt_version="o15_prompt_v3",
        evidence_status="experiment_driven_revision",
        required_fact_slots=(
            "target_claim",
            "changed_fact_id",
            "comparison_quantity",
            "conclusion_layer",
        ),
        payload_required=(
            "changed_fact_id",
            "comparison_quantity",
            "direction_or_order",
            "conclusion_layer_effect",
            "threshold_given",
        ),
        payload_properties={
            "changed_fact_id": "string",
            "comparison_quantity": "string",
            "direction_or_order": "string",
            "conclusion_layer_effect": "string",
            "threshold_given": "boolean",
        },
        transformation_contract={
            "allowed_transforms": ["replace_single_fact", "swap_before_after"],
            "max_changed_core_facts": 1,
            "preserve_threshold": True,
        },
        answer_space={
            "direction_or_order": ["not_increased", "decreased", "increased", "unchanged", "reversed"],
            "requires_explicit_threshold_for_reversal": True,
        },
        scorer_fields=("changed_fact_id", "comparison_quantity", "direction_or_order", "conclusion_layer_effect"),
        release_checks=("single_comparison_quantity", "threshold_required_for_reversal"),
    ),
    "O16_close_alternative_normalization": _contract(
        "O16_close_alternative_normalization",
        prompt_version="o16_prompt_v3",
        evidence_status="experiment_driven_revision",
        required_fact_slots=(
            "target_claim",
            "target_hypothesis",
            "alternative_hypothesis",
            "discriminator_fact_id",
        ),
        payload_required=(
            "core_fact_ids",
            "peripheral_fact_ids",
            "shared_core_fact_ids",
            "discriminator_fact_id",
            "hypotheses",
            "discriminator_ablation_result",
        ),
        payload_properties={
            "core_fact_ids": "array",
            "peripheral_fact_ids": "array",
            "shared_core_fact_ids": "array",
            "discriminator_fact_id": "string",
            "hypotheses": "array",
            "discriminator_ablation_result": "string",
        },
        transformation_contract={
            "allowed_transforms": ["add_single_alternative_hypothesis", "remove_discriminator", "surface_swap"],
            "max_alternative_hypotheses": 1,
        },
        answer_space={"coverage_result": ["covers_core", "covers_peripheral_only", "underdetermined"]},
        scorer_fields=("compare_hypothesis_coverage", "identify_discriminator_fact_id", "explain_residuals"),
        release_checks=("single_alternative_hypothesis", "discriminator_ablation_nondiagnostic"),
    ),
    "O17_action_vs_fact_threshold": _contract(
        "O17_action_vs_fact_threshold",
        prompt_version="o17_prompt_v3",
        evidence_status="qualification_hypothesis",
        required_fact_slots=(
            "rule_a_text",
            "rule_a_version",
            "rule_a_subject",
            "rule_a_threshold",
            "rule_b_text",
            "rule_b_version",
            "rule_b_subject",
            "rule_b_threshold",
            "current_facts",
        ),
        payload_required=("rule_a_mapping", "rule_b_mapping", "current_fact_ids", "scope_separation"),
        payload_properties={
            "rule_a_mapping": "object",
            "rule_b_mapping": "object",
            "current_fact_ids": "array",
            "scope_separation": "string",
        },
        transformation_contract={
            "allowed_transforms": ["surface_swap_rules", "boundary_value_variant"],
            "rules_must_be_explicit": True,
        },
        answer_space={"mapping": ["applies", "does_not_apply", "partially_matches"]},
        scorer_fields=("map_current_facts_to_rule_a", "map_current_facts_to_rule_b", "separate_rule_scopes"),
        release_checks=("both_rules_explicit_and_versioned",),
        status=DISABLED,
    ),
    "O18_baseline_scope_mismatch": _contract(
        "O18_baseline_scope_mismatch",
        prompt_version="o18_prompt_v3",
        evidence_status="qualification_hypothesis",
        required_fact_slots=(
            "baseline_a.source",
            "baseline_a.inclusion_criteria",
            "baseline_a.summary",
            "baseline_b.source",
            "baseline_b.inclusion_criteria",
            "baseline_b.summary",
            "observation",
            "target_anomaly_claim",
        ),
        payload_required=(
            "selected_baseline_id",
            "baseline_comparison",
            "observation_fact_id",
            "anomaly_effect",
        ),
        payload_properties={
            "selected_baseline_id": "string",
            "baseline_comparison": "object",
            "observation_fact_id": "string",
            "anomaly_effect": "string",
        },
        transformation_contract={
            "allowed_transforms": ["surface_swap_baselines", "change_applicable_baseline"],
            "preserve_observation": True,
        },
        answer_space={"anomaly_effect": ["changed", "unchanged", "underdetermined"]},
        scorer_fields=("select_comparable_baseline", "explain_inclusion_scope_match", "assess_anomaly_effect"),
        release_checks=("observation_preserved", "baseline_summary_present"),
        status=DISABLED,
    ),
}


def _new_operator_contract(
    operator_id: str,
    *,
    required_fact_slots: Sequence[str],
    payload_properties: Mapping[str, str],
    allowed_transforms: Sequence[str],
    neighbor_operators: Sequence[str],
    scorer_fields: Sequence[str],
) -> OperatorContract:
    spec = OPERATOR_SPECS[operator_id]
    prefix = operator_id.split("_", 1)[0].lower()
    return OperatorContract(
        operator_id=operator_id,
        semantic_version="1.0",
        prompt_version=f"{prefix}_prompt_v1",
        applicability_version=f"{prefix}_applicability_v1",
        validation_policy_version="new_operator_validation_v1",
        evidence_status="qualification_evidence_pending",
        ability_axis=spec.ability_axis,
        ability_axes=tuple(spec.ability_axes or (spec.ability_axis,)),
        reasoning_object=spec.reasoning_object,
        preserved_parent_obligations=tuple(spec.preserved_parent_obligations),
        required_reasoning_output=tuple(spec.required_reasoning_tasks),
        target_error_taxonomy=tuple(spec.target_error_taxonomy),
        excluded_error_taxonomy=tuple(spec.excluded_error_taxonomy),
        required_fact_slots=tuple(required_fact_slots),
        forbidden_fact_types=FORBIDDEN_FACT_TYPES,
        transformation_contract={
            "allowed_transforms": list(allowed_transforms),
            "fixed_quantity_limits": False,
            "single_selected_operator": True,
            "manual_review_record_is_record_only": True,
        },
        invariants=tuple(spec.invariants),
        answer_space={
            "axis_specific": True,
            "fixed_direction_labels": False,
            "candidate_total_is_supplementary": True,
        },
        operator_payload_schema=_payload_schema(
            tuple(payload_properties),
            payload_properties,
        ),
        answer_contract_schema={
            "type": "object",
            "required": ["answer_key", "decisive_fact_ids", "rubric_assertions"],
            "axis_contracts_supported": True,
            "additionalProperties": True,
        },
        scorer_mapping={
            "answer_contract_version": ANSWER_CONTRACT_VERSION,
            "rubric_fields": list(scorer_fields),
            "per_axis_attribution_required": True,
        },
        neighbor_operators=tuple(neighbor_operators),
        routing_exclusions=("production_fallback_until_qualified",),
        release_checks=_release_checks(
            "single_selected_operator",
            "required_slots_traceable",
            "manual_review_record_not_consumed",
        ),
        status=QUALIFICATION_ONLY,
    )


_NEW_OPERATOR_DEFINITIONS: Dict[str, Dict[str, Any]] = {
    "O19_multi_entity_role_binding": {
        "slots": ("entities", "observations_by_node", "candidate_role_bindings", "relation_directions", "target_relation_claim"),
        "payload": {
            "entities": "array", "observations_by_node": "object", "node_entity_bindings": "array",
            "relation_directions": "array", "role_assignments": "array", "target_relation_conclusion": "object",
        },
        "transforms": ("swap_entity_binding", "swap_event_role", "reverse_relation_direction", "surface_swap"),
        "neighbors": ("O10_evidence_sufficiency_ladder", "O21_object_provenance_identity", "O29_entity_identity_conflict_resolution", "O32_role_graph_critical_edge"),
    },
    "O20_multistage_event_breakpoint": {
        "slots": ("event_nodes", "state_before_after_each_node", "temporal_order", "entity_bindings", "required_edges", "target_chain_claim"),
        "payload": {
            "event_nodes": "array", "state_by_node": "object", "required_edges": "array",
            "entity_bindings": "array", "breakpoints": "array", "local_closed_chains": "array", "closure_status": "string",
        },
        "transforms": ("delete_required_edge", "replace_state_transition", "reorder_temporal_edge", "surface_swap"),
        "neighbors": ("O11_unobserved_state_attribution", "O13_minimal_disqualifier", "O22_path_topology_reachability", "O28_multihop_chain_closure"),
    },
    "O21_object_provenance_identity": {
        "slots": ("object_candidates", "observable_attributes", "locations_over_time", "transfer_events", "occlusion_intervals", "competing_sources", "target_identity_claim"),
        "payload": {
            "object_candidates": "array", "observable_attributes": "object", "transfer_events": "array",
            "occlusion_intervals": "array", "competing_sources": "array", "transfer_gaps": "array", "identity_resolution": "string",
        },
        "transforms": ("swap_object_binding", "change_reappearance_object", "change_competing_source", "surface_swap"),
        "neighbors": ("O19_multi_entity_role_binding", "O20_multistage_event_breakpoint", "O29_entity_identity_conflict_resolution"),
    },
    "O22_path_topology_reachability": {
        "slots": ("graph_nodes", "graph_edges", "observed_endpoints", "time_windows", "travel_time_ranges", "candidate_paths", "target_reachability_claim"),
        "payload": {
            "graph_nodes": "array", "graph_edges": "array", "observed_endpoints": "array",
            "time_windows": "object", "travel_time_ranges": "object", "candidate_paths": "array", "reachability_result": "object",
        },
        "transforms": ("change_edge_availability", "change_endpoint_window", "change_path_constraint", "surface_swap"),
        "neighbors": ("O11_unobserved_state_attribution", "O20_multistage_event_breakpoint", "O28_multihop_chain_closure"),
    },
    "O23_observation_reliability_conflict": {
        "slots": ("observation_claim", "visible_features", "quality_conditions", "occlusion_or_view_limits", "competing_observations", "downstream_claim"),
        "payload": {
            "observation_claim": "object", "visible_features": "array", "quality_conditions": "object",
            "view_limits": "array", "competing_observations": "array", "reliability_resolution": "string", "max_supported_downstream_claim": "object",
        },
        "transforms": ("change_observation_quality", "change_visible_feature", "change_view_limit", "surface_swap"),
        "neighbors": ("O10_evidence_sufficiency_ladder", "O29_entity_identity_conflict_resolution", "O31_observation_accumulation_calibration", "O33_cross_modal_support_boundary"),
    },
    "O24_multi_hypothesis_residual_ranking": {
        "slots": ("hypotheses", "shared_fact_set", "coverage_matrix", "conflict_matrix", "unexplained_residuals", "target_ranking_rule"),
        "payload": {
            "hypotheses": "array", "shared_fact_ids": "array", "coverage_matrix": "object",
            "conflict_matrix": "object", "unexplained_residuals": "object", "extra_assumption_costs": "object", "ranking_result": "array",
        },
        "transforms": ("add_discriminator", "replace_discriminator", "change_observable_discriminator", "surface_swap"),
        "neighbors": ("O16_close_alternative_normalization", "O30_active_discriminative_observation"),
    },
    "O25_procedural_invariant_frame": {
        "slots": ("procedure_steps", "reference_frame", "measurement_units", "ordering_dependencies", "recording_mapping", "target_comparability_claim"),
        "payload": {
            "procedure_steps": "array", "reference_frames": "object", "measurement_units": "object",
            "ordering_dependencies": "array", "recording_mapping": "object", "broken_invariants": "array", "comparability_result": "string",
        },
        "transforms": ("change_reference_frame", "change_unit", "reorder_dependent_steps", "shift_record_mapping", "surface_swap"),
        "neighbors": ("O10_evidence_sufficiency_ladder", "O12_conjunctive_necessity"),
    },
    "O26_quantitative_threshold_propagation": {
        "slots": ("observed_values", "units", "uncertainty_intervals", "transformation_formula_or_rule", "decision_threshold", "target_quantitative_claim"),
        "payload": {
            "observed_values": "object", "units": "object", "uncertainty_intervals": "object",
            "transformation_rule": "object", "decision_threshold": "object", "derived_interval": "object", "threshold_relation": "string",
        },
        "transforms": ("change_observed_value", "change_uncertainty_interval", "change_threshold_distance", "surface_swap"),
        "neighbors": ("O18_baseline_scope_mismatch", "O31_observation_accumulation_calibration"),
    },
    "O27_cross_layer_conclusion_calibration": {
        "slots": ("claim_layers", "allowed_transitions", "required_thresholds_or_rules", "current_evidence_state", "target_layer"),
        "payload": {
            "claim_layers": "array", "allowed_transitions": "array", "required_thresholds_or_rules": "object",
            "current_evidence_state": "object", "target_layer": "string", "used_transition_edges": "array", "max_supported_claim": "object",
        },
        "transforms": ("change_lower_layer_evidence", "change_local_reasoning_link", "surface_swap"),
        "neighbors": ("O13_minimal_disqualifier", "O15_counterfactual_threshold_shift", "O17_action_vs_fact_threshold", "O33_cross_modal_support_boundary"),
    },
    "O28_multihop_chain_closure": {
        "slots": ("event_nodes", "state_by_node", "required_edges", "entity_bindings_across_nodes", "target_claim"),
        "payload": {
            "event_nodes": "array", "state_by_node": "object", "required_edges": "array", "optional_edges": "array",
            "conflict_edges": "array", "entity_bindings_across_nodes": "array", "path_time_constraints": "object",
            "local_closed_chains": "array", "breakpoints": "array", "closure_status": "string",
        },
        "transforms": ("change_node_state", "change_chain_edge", "change_cross_node_binding", "change_path_time_constraint", "surface_swap"),
        "neighbors": ("O11_unobserved_state_attribution", "O20_multistage_event_breakpoint", "O22_path_topology_reachability"),
    },
    "O29_entity_identity_conflict_resolution": {
        "slots": ("candidate_entities", "local_bindings", "observable_attributes", "temporal_spatial_relations", "identity_claim"),
        "payload": {
            "candidate_entities": "array", "local_bindings": "array", "observable_attributes": "object",
            "temporal_spatial_relations": "array", "transfer_gaps": "array", "conflicting_bindings": "array",
            "identity_claim": "object", "identity_resolution": "string",
        },
        "transforms": ("change_observable_attribute", "change_temporal_spatial_relation", "change_transfer_gap", "change_conflicting_binding", "surface_swap"),
        "neighbors": ("O19_multi_entity_role_binding", "O21_object_provenance_identity", "O23_observation_reliability_conflict"),
    },
    "O30_active_discriminative_observation": {
        "slots": ("hypotheses", "current_fact_ids", "candidate_observations", "possible_outcomes_by_observation"),
        "payload": {
            "hypotheses": "array", "current_fact_ids": "array", "candidate_observations": "array",
            "possible_outcomes_by_observation": "object", "hypothesis_discrimination_matrix": "object",
            "selected_observation_id": "string", "selection_rationale_contract": "array",
        },
        "transforms": ("change_observation_outcomes", "change_observation_availability", "surface_swap"),
        "neighbors": ("O16_close_alternative_normalization", "O23_observation_reliability_conflict", "O24_multi_hypothesis_residual_ranking"),
    },
    "O31_observation_accumulation_calibration": {
        "slots": ("observation_events", "dependency_graph", "quality_by_observation", "target_claim"),
        "payload": {
            "observation_events": "array", "dependency_graph": "object", "quality_by_observation": "object",
            "new_information_by_observation": "object", "support_order_before_after": "object", "max_supported_claim": "object",
        },
        "transforms": ("change_observation_dependency", "change_observation_quality", "change_new_information", "change_time_alignment", "surface_swap"),
        "neighbors": ("O10_evidence_sufficiency_ladder", "O23_observation_reliability_conflict", "O26_quantitative_threshold_propagation"),
    },
    "O32_role_graph_critical_edge": {
        "slots": ("entities", "candidate_roles", "directed_relation_edges", "fact_ids_by_edge", "target_role_claim"),
        "payload": {
            "entities": "array", "candidate_roles": "array", "directed_relation_edges": "array",
            "fact_ids_by_edge": "object", "target_role_claim": "object", "necessary_edges": "array",
            "alternative_edges": "array", "conclusions_after_edge_change": "object",
        },
        "transforms": ("swap_entity_binding", "delete_relation_edge", "replace_relation_edge", "reverse_relation_edge", "surface_swap"),
        "neighbors": ("O13_minimal_disqualifier", "O19_multi_entity_role_binding", "O29_entity_identity_conflict_resolution"),
    },
    "O33_cross_modal_support_boundary": {
        "slots": ("sources", "fact_ids_by_source", "scope_by_source", "time_alignment", "target_claim"),
        "payload": {
            "sources": "array", "fact_ids_by_source": "object", "scope_by_source": "object", "time_alignment": "object",
            "entity_links_across_sources": "array", "supported_claim_layers_by_source": "object",
            "source_conflicts": "array", "fusion_result": "object", "max_supported_claim_after_fusion": "object",
        },
        "transforms": ("change_source_scope", "change_time_alignment", "change_cross_source_entity_link", "change_source_conflict", "surface_swap"),
        "neighbors": ("O14_information_closure", "O23_observation_reliability_conflict", "O27_cross_layer_conclusion_calibration"),
    },
}


for _operator_id, _definition in _NEW_OPERATOR_DEFINITIONS.items():
    if _operator_id in OPERATOR_CONTRACTS:
        raise RuntimeError(f"duplicate operator contract ID: {_operator_id}")
    OPERATOR_CONTRACTS[_operator_id] = _new_operator_contract(
        _operator_id,
        required_fact_slots=_definition["slots"],
        payload_properties=_definition["payload"],
        allowed_transforms=_definition["transforms"],
        neighbor_operators=_definition["neighbors"],
        scorer_fields=OPERATOR_SPECS[_operator_id].required_reasoning_tasks,
    )


def validate_contract_registry(
    contracts: Optional[Mapping[str, OperatorContract]] = None,
) -> List[str]:
    """Return deterministic identity and semantic-collision findings."""

    registry = dict(contracts or OPERATOR_CONTRACTS)
    findings: List[str] = []
    seen_numeric_ids: Dict[str, str] = {}
    semantic_signatures: Dict[Tuple[Tuple[str, ...], str], str] = {}
    for key, contract in registry.items():
        if key != contract.operator_id:
            findings.append(
                f"registry key {key} conflicts with contract operator_id {contract.operator_id}"
            )
        numeric_id = contract.operator_id.split("_", 1)[0]
        previous = seen_numeric_ids.get(numeric_id)
        if previous and previous != contract.operator_id:
            findings.append(
                f"duplicate stable operator number {numeric_id}: {previous}, {contract.operator_id}"
            )
        seen_numeric_ids[numeric_id] = contract.operator_id
        signature = (
            tuple(sorted(contract.ability_axes)),
            " ".join(contract.reasoning_object.split()).lower(),
        )
        previous = semantic_signatures.get(signature)
        if previous and previous != contract.operator_id:
            findings.append(
                f"semantic collision: {previous} and {contract.operator_id}"
            )
        semantic_signatures[signature] = contract.operator_id
        if contract.status not in OPERATOR_STATUSES:
            findings.append(
                f"{contract.operator_id} has unsupported status {contract.status}"
            )
    return findings


_REGISTRY_FINDINGS = validate_contract_registry()
if _REGISTRY_FINDINGS:
    raise RuntimeError("; ".join(_REGISTRY_FINDINGS))


def get_operator_contract(operator_id: str) -> OperatorContract:
    try:
        return OPERATOR_CONTRACTS[operator_id]
    except KeyError as exc:
        raise ValueError(f"Unknown operator_id: {operator_id}") from exc


def enabled_generation_operator_ids() -> Tuple[str, ...]:
    return tuple(
        operator_id
        for operator_id, contract in OPERATOR_CONTRACTS.items()
        if contract.status == ENABLED
    )


def _meta_info(item: Mapping[str, Any]) -> Mapping[str, Any]:
    value = item.get("meta_info")
    return value if isinstance(value, Mapping) else {}


def extract_fact_ledger(item: Mapping[str, Any]) -> List[Dict[str, Any]]:
    ledger = item.get("fact_ledger")
    if not isinstance(ledger, list):
        ledger = _meta_info(item).get("fact_ledger")
    if not isinstance(ledger, list):
        return []
    return [dict(fact) for fact in ledger if isinstance(fact, Mapping)]


def extract_operator_manifest(
    item: Mapping[str, Any],
    operator_id: str,
) -> Dict[str, Any]:
    meta_info = _meta_info(item)
    manifests = meta_info.get("operator_manifests")
    candidates = [
        item.get("operator_manifest"),
        manifests.get(operator_id) if isinstance(manifests, Mapping) else None,
        meta_info.get("operator_manifest"),
        meta_info.get("qualification_manifest"),
    ]
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            return dict(candidate)
    return {}


def _nested_value(source: Mapping[str, Any], path: str) -> Any:
    current: Any = source
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current


def _missing_value(value: Any) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _manifest_referenced_fact_ids(manifest: Mapping[str, Any]) -> List[str]:
    fact_ids = collect_referenced_fact_ids(manifest)
    current_facts = manifest.get("current_facts")
    if isinstance(current_facts, list):
        for fact in current_facts:
            if isinstance(fact, str) and fact.strip() and fact.strip() not in fact_ids:
                fact_ids.append(fact.strip())
            elif isinstance(fact, Mapping):
                fact_id = fact.get("fact_id")
                if isinstance(fact_id, str) and fact_id.strip() and fact_id.strip() not in fact_ids:
                    fact_ids.append(fact_id.strip())
    return fact_ids


def evaluate_operator_applicability(
    item: Mapping[str, Any],
    operator_id: str,
    *,
    allow_disabled: bool = False,
) -> Dict[str, Any]:
    contract = get_operator_contract(operator_id)
    base = {
        "operator_id": operator_id,
        "applicability_version": contract.applicability_version,
        "operator_status": contract.status,
        "candidate_budget_consumed": False,
    }
    if contract.status == VALIDATION_ONLY:
        return {
            **base,
            "status": VALIDATION_ONLY,
            "reason": "validation-only operator cannot generate candidates",
            "missing_required_fact_slots": [],
            "forbidden_fact_ids": [],
        }
    if contract.status != ENABLED and not allow_disabled:
        return {
            **base,
            "status": contract.status,
            "reason": (
                "operator cannot control production generation until forced "
                "qualification and natural routing evidence support enablement"
            ),
            "missing_required_fact_slots": [],
            "forbidden_fact_ids": [],
        }

    manifest = extract_operator_manifest(item, operator_id)
    missing_slots = [
        slot
        for slot in contract.required_fact_slots
        if _missing_value(_nested_value(manifest, slot))
    ]
    ledger = extract_fact_ledger(item)
    human_confirmed = manifest.get("human_confirmed") is True
    operator_number = int(operator_id[1:].split("_", 1)[0])
    traceable_source_manifest = bool(
        str(manifest.get("source_manifest_id") or "").strip()
        or (
            isinstance(manifest.get("source_manifest"), Mapping)
            and str(
                manifest.get("source_manifest", {}).get("manifest_id")
                or manifest.get("source_manifest", {}).get("source_id")
                or ""
            ).strip()
        )
    )
    if contract.required_fact_slots and not ledger:
        if operator_number >= 19 and not traceable_source_manifest:
            missing_slots.append(
                "fact_ledger_or_traceable_source_manifest"
            )
        elif operator_number < 19 and not human_confirmed:
            missing_slots.append("fact_ledger_or_human_confirmation")

    ledger_by_id: Dict[str, Dict[str, Any]] = {}
    forbidden_ids: List[str] = []
    for fact in ledger:
        fact_id = str(fact.get("fact_id") or "").strip()
        if fact_id:
            ledger_by_id[fact_id] = fact
    referenced_ids = _manifest_referenced_fact_ids(manifest)
    missing_fact_ids = [
        fact_id
        for fact_id in referenced_ids
        if ledger and fact_id not in ledger_by_id
    ]
    for fact_id in referenced_ids:
        fact = ledger_by_id.get(fact_id)
        if not fact:
            continue
        fact_type = str(fact.get("fact_type") or fact.get("type") or "").strip().lower()
        if fact_type in set(contract.forbidden_fact_types):
            forbidden_ids.append(fact_id)

    if missing_slots or missing_fact_ids or forbidden_ids:
        reasons: List[str] = []
        if missing_slots:
            reasons.append("missing required fact slots: " + ", ".join(dict.fromkeys(missing_slots)))
        if missing_fact_ids:
            reasons.append("manifest fact IDs absent from fact ledger: " + ", ".join(missing_fact_ids))
        if forbidden_ids:
            reasons.append("forbidden fact types used: " + ", ".join(forbidden_ids))
        return {
            **base,
            "status": NOT_APPLICABLE,
            "reason": "; ".join(reasons),
            "missing_required_fact_slots": list(dict.fromkeys(missing_slots)),
            "missing_fact_ids": missing_fact_ids,
            "forbidden_fact_ids": forbidden_ids,
            "manifest_source": "human_confirmed" if human_confirmed else "fact_ledger",
        }

    return {
        **base,
        "status": ELIGIBLE,
        "reason": "all required fact slots are present and authorized",
        "missing_required_fact_slots": [],
        "missing_fact_ids": [],
        "forbidden_fact_ids": [],
        "manifest_source": "human_confirmed" if human_confirmed else "fact_ledger",
        "source_manifest_id": (
            manifest.get("source_manifest_id")
            or (
                manifest.get("source_manifest", {}).get("manifest_id")
                if isinstance(manifest.get("source_manifest"), Mapping)
                else None
            )
        ),
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def build_recipe_hash(
    operator_id: str,
    *,
    source_record: Mapping[str, Any],
    operator_manifest: Optional[Mapping[str, Any]] = None,
) -> str:
    contract = get_operator_contract(operator_id)
    recipe = {
        "operator_id": contract.operator_id,
        "semantic_version": contract.semantic_version,
        "prompt_version": contract.prompt_version,
        "applicability_version": contract.applicability_version,
        "validation_policy_version": contract.validation_policy_version,
        "source": {
            "sample_id": source_record.get("sample_id"),
            "index": source_record.get("index"),
            "round": source_record.get("round"),
            "prompt": source_record.get("prompt"),
        },
        "operator_manifest": dict(operator_manifest or {}),
    }
    return sha256_json(recipe)


def answer_contract_hash(answer_contract: Mapping[str, Any]) -> str:
    payload = {
        key: value
        for key, value in answer_contract.items()
        if key != "answer_contract_hash"
    }
    return sha256_json(payload)


def freeze_answer_contract(
    raw: Mapping[str, Any],
    *,
    operator_id: str,
    recipe_hash: str,
    target_claim: Any,
    conclusion_layer: Any,
    operator_payload: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("answer_contract must be an object")
    answer_key = raw.get("answer_key")
    if answer_key in (None, "", [], {}):
        raise ValueError("answer_contract.answer_key is required")

    decisive_fact_ids = raw.get("decisive_fact_ids")
    if not isinstance(decisive_fact_ids, list):
        raise ValueError("answer_contract.decisive_fact_ids must be an array")

    frozen = dict(raw)
    frozen.update(
        {
            "answer_contract_version": ANSWER_CONTRACT_VERSION,
            "operator_id": operator_id,
            "recipe_hash": recipe_hash,
            "target_claim": target_claim,
            "conclusion_layer": conclusion_layer,
            "operator_answer": dict(operator_payload),
            "frozen": True,
        }
    )
    frozen["answer_contract_hash"] = answer_contract_hash(frozen)
    return frozen


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str) and bool(value.strip())
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def validate_operator_payload(
    operator_id: str,
    payload: Any,
) -> List[str]:
    contract = get_operator_contract(operator_id)
    schema = contract.operator_payload_schema
    if not isinstance(payload, Mapping):
        return ["operator_payload must be an object"]
    errors: List[str] = []
    properties = schema.get("properties", {})
    for field in schema.get("required", ()):
        if field not in payload:
            errors.append(f"operator_payload missing required field: {field}")
            continue
        expected_type = properties.get(field)
        if expected_type and not _type_matches(payload.get(field), expected_type):
            errors.append(
                f"operator_payload.{field} must be {expected_type}"
            )
    return errors


def validate_answer_contract(
    answer_contract: Any,
    *,
    operator_id: str,
    recipe_hash: str,
    target_claim: Any,
    conclusion_layer: Any,
    operator_payload: Mapping[str, Any],
) -> List[str]:
    if not isinstance(answer_contract, Mapping):
        return ["answer_contract must be an object"]
    errors: List[str] = []
    expected = {
        "answer_contract_version": ANSWER_CONTRACT_VERSION,
        "operator_id": operator_id,
        "recipe_hash": recipe_hash,
        "target_claim": target_claim,
        "conclusion_layer": conclusion_layer,
        "operator_answer": dict(operator_payload),
        "frozen": True,
    }
    for field, value in expected.items():
        if answer_contract.get(field) != value:
            errors.append(f"answer_contract.{field} conflicts with frozen candidate envelope")
    if answer_contract.get("answer_key") in (None, "", [], {}):
        errors.append("answer_contract.answer_key is required")
    if not isinstance(answer_contract.get("decisive_fact_ids"), list):
        errors.append("answer_contract.decisive_fact_ids must be an array")
    recorded_hash = answer_contract.get("answer_contract_hash")
    if recorded_hash != answer_contract_hash(answer_contract):
        errors.append("answer_contract_hash mismatch")
    return errors


def build_candidate_envelope(
    evolved: Mapping[str, Any],
    *,
    operator_id: str,
    source_record: Mapping[str, Any],
    operator_manifest: Optional[Mapping[str, Any]] = None,
    require_blind_resolution: bool = False,
) -> Dict[str, Any]:
    contract = get_operator_contract(operator_id)
    target_claim = evolved.get("target_claim")
    conclusion_layer = evolved.get("conclusion_layer")
    payload = evolved.get("operator_payload")
    surface_fact_ids = evolved.get("surface_fact_ids")
    applied_transforms = evolved.get("applied_transforms")
    resolution = evolved.get("answer_contract_resolution")
    if target_claim in (None, "", [], {}):
        raise ValueError("target_claim is required")
    if not isinstance(conclusion_layer, str) or not conclusion_layer.strip():
        raise ValueError("conclusion_layer is required")
    if not isinstance(surface_fact_ids, list) or not surface_fact_ids or not all(
        isinstance(fact_id, str) and fact_id.strip()
        for fact_id in surface_fact_ids
    ):
        raise ValueError("surface_fact_ids must be an array of fact IDs")
    if not isinstance(applied_transforms, list) or not applied_transforms or not all(
        isinstance(transform, str) and transform.strip()
        for transform in applied_transforms
    ):
        raise ValueError("applied_transforms must be an array")
    if require_blind_resolution and (
        not isinstance(resolution, Mapping)
        or resolution.get("status") != "resolved"
    ):
        raise ValueError("blind solver and contract resolver must agree before freezing answer contract")
    payload_errors = validate_operator_payload(operator_id, payload)
    if payload_errors:
        raise ValueError("; ".join(payload_errors))

    recipe_hash = build_recipe_hash(
        operator_id,
        source_record=source_record,
        operator_manifest=operator_manifest,
    )
    frozen_answer_contract = freeze_answer_contract(
        evolved.get("answer_contract", {}),
        operator_id=operator_id,
        recipe_hash=recipe_hash,
        target_claim=target_claim,
        conclusion_layer=conclusion_layer,
        operator_payload=payload,
    )
    leakage_risks = evolved.get("surface_leakage_risks")
    if not isinstance(leakage_risks, (list, Mapping)):
        leakage_risks = []
    manual_review_record = evolved.get("manual_review_record")
    if not isinstance(manual_review_record, Mapping):
        manual_review_record = {}
    axis_assignments = evolved.get("axis_assignments")
    if not isinstance(axis_assignments, list) or not axis_assignments:
        axis_assignments = [
            {
                "axis_id": f"axis_{contract.ability_axes[0]}",
                "semantic_ability_axis": contract.ability_axes[0],
                "source_fact_ids": list(surface_fact_ids),
                "target_claim": target_claim,
                "operator_payload": dict(payload),
                "answer_contract_id": frozen_answer_contract["answer_contract_hash"],
                "rubric_item_ids": [],
            }
        ]
    raw_axis_contracts = evolved.get("axis_answer_contracts")
    if not isinstance(raw_axis_contracts, Mapping):
        raw_axis_contracts = {}
    normalized_axis_assignments: List[Dict[str, Any]] = []
    frozen_axis_contracts: Dict[str, Dict[str, Any]] = {}
    for index, raw_assignment in enumerate(axis_assignments):
        if not isinstance(raw_assignment, Mapping):
            continue
        assignment = dict(raw_assignment)
        axis_id = str(
            assignment.get("axis_id")
            or f"axis_{index + 1}"
        ).strip()
        axis_payload = assignment.get("operator_payload")
        if not isinstance(axis_payload, Mapping):
            axis_payload = payload
        axis_target_claim = assignment.get("target_claim", target_claim)
        raw_axis_contract = raw_axis_contracts.get(axis_id)
        if not isinstance(raw_axis_contract, Mapping):
            raw_axis_contract = (
                evolved.get("answer_contract", {})
                if len(axis_assignments) == 1
                else {
                    "answer_key": assignment.get("answer_key"),
                    "decisive_fact_ids": list(
                        assignment.get("source_fact_ids") or surface_fact_ids
                    ),
                    "rubric_assertions": list(
                        assignment.get("rubric_assertions") or []
                    ),
                }
            )
        axis_recipe_hash = sha256_json(
            {"recipe_hash": recipe_hash, "axis_id": axis_id}
        )
        frozen_axis_contract = freeze_answer_contract(
            raw_axis_contract,
            operator_id=operator_id,
            recipe_hash=axis_recipe_hash,
            target_claim=axis_target_claim,
            conclusion_layer=assignment.get(
                "conclusion_layer",
                conclusion_layer,
            ),
            operator_payload=axis_payload,
        )
        frozen_axis_contracts[axis_id] = frozen_axis_contract
        assignment.update(
            {
                "axis_id": axis_id,
                "source_fact_ids": list(
                    assignment.get("source_fact_ids") or surface_fact_ids
                ),
                "target_claim": axis_target_claim,
                "operator_payload": dict(axis_payload),
                "answer_contract_id": frozen_axis_contract[
                    "answer_contract_hash"
                ],
                "rubric_item_ids": list(
                    assignment.get("rubric_item_ids") or []
                ),
            }
        )
        normalized_axis_assignments.append(assignment)
    axis_assignments = normalized_axis_assignments
    axis_interactions = evolved.get("axis_interactions")
    if not isinstance(axis_interactions, list):
        axis_interactions = []
    group_id = str(
        evolved.get("candidate_group_id")
        or source_record.get("candidate_group_id")
        or source_record.get("sample_id")
        or source_record.get("index")
        or ""
    )
    candidate_id = str(
        evolved.get("candidate_id")
        or source_record.get("candidate_id")
        or ""
    )

    return {
        "operator_id": operator_id,
        "selected_operator_id": operator_id,
        "candidate_group_id": group_id,
        "candidate_id": candidate_id,
        "semantic_version": contract.semantic_version,
        "prompt_version": contract.prompt_version,
        "applicability_version": contract.applicability_version,
        "validation_policy_version": contract.validation_policy_version,
        "recipe_hash": recipe_hash,
        "evidence_status": contract.evidence_status,
        "status": contract.status,
        "ability_axis": contract.ability_axis,
        "ability_axes": list(contract.ability_axes),
        "target_error_taxonomy": list(contract.target_error_taxonomy),
        "target_claim": target_claim,
        "conclusion_layer": conclusion_layer.strip(),
        "surface_fact_ids": list(surface_fact_ids),
        "applied_transforms": list(applied_transforms),
        "required_reasoning_output": list(contract.required_reasoning_output),
        "preserved_parent_obligations": list(contract.preserved_parent_obligations),
        "operator_payload": dict(payload),
        "source_fact_ids": list(surface_fact_ids),
        "axis_assignments": [dict(axis) for axis in axis_assignments if isinstance(axis, Mapping)],
        "axis_answer_contracts": frozen_axis_contracts,
        "axis_interactions": [dict(edge) for edge in axis_interactions if isinstance(edge, Mapping)],
        "manual_review_record": dict(manual_review_record),
        "adapter_version": str(
            (operator_manifest or {}).get("adapter_version")
            or (operator_manifest or {}).get("adapter_id")
            or ""
        ),
        "surface_leakage_risks": leakage_risks,
        "answer_contract": frozen_answer_contract,
        "answer_contract_resolution": (
            dict(resolution)
            if isinstance(resolution, Mapping)
            else {
                "status": "generator_only_legacy",
                "required": False,
            }
        ),
    }


def validate_candidate_envelope(
    envelope: Any,
    *,
    operator_id: Optional[str] = None,
) -> List[str]:
    if not isinstance(envelope, Mapping):
        return ["operator_envelope must be an object"]
    resolved_operator = operator_id or str(envelope.get("operator_id") or "")
    if not resolved_operator:
        return ["operator_envelope.operator_id is required"]
    try:
        contract = get_operator_contract(resolved_operator)
    except ValueError as exc:
        return [str(exc)]

    errors: List[str] = []
    expected_identity = {
        "operator_id": resolved_operator,
        "selected_operator_id": resolved_operator,
        "semantic_version": contract.semantic_version,
        "prompt_version": contract.prompt_version,
        "applicability_version": contract.applicability_version,
        "validation_policy_version": contract.validation_policy_version,
        "evidence_status": contract.evidence_status,
        "status": contract.status,
        "ability_axis": contract.ability_axis,
        "ability_axes": list(contract.ability_axes),
        "target_error_taxonomy": list(contract.target_error_taxonomy),
        "required_reasoning_output": list(contract.required_reasoning_output),
        "preserved_parent_obligations": list(contract.preserved_parent_obligations),
    }
    for field, expected in expected_identity.items():
        if envelope.get(field) != expected:
            errors.append(f"operator_envelope.{field} conflicts with registered contract")

    recipe_hash = envelope.get("recipe_hash")
    if not isinstance(recipe_hash, str) or len(recipe_hash) != 64:
        errors.append("operator_envelope.recipe_hash must be a sha256 hex string")
    errors.extend(
        validate_operator_payload(resolved_operator, envelope.get("operator_payload"))
    )
    payload = envelope.get("operator_payload")
    if isinstance(payload, Mapping) and isinstance(recipe_hash, str):
        errors.extend(
            validate_answer_contract(
                envelope.get("answer_contract"),
                operator_id=resolved_operator,
                recipe_hash=recipe_hash,
                target_claim=envelope.get("target_claim"),
                conclusion_layer=envelope.get("conclusion_layer"),
                operator_payload=payload,
            )
        )
    if envelope.get("target_claim") in (None, "", [], {}):
        errors.append("operator_envelope.target_claim is required")
    if not isinstance(envelope.get("conclusion_layer"), str) or not str(
        envelope.get("conclusion_layer")
    ).strip():
        errors.append("operator_envelope.conclusion_layer is required")
    if not isinstance(envelope.get("surface_fact_ids"), list) or not envelope.get("surface_fact_ids") or not all(
        isinstance(fact_id, str) and fact_id.strip()
        for fact_id in envelope.get("surface_fact_ids", [])
    ):
        errors.append("operator_envelope.surface_fact_ids must be an array of fact IDs")
    if not isinstance(envelope.get("applied_transforms"), list) or not envelope.get("applied_transforms"):
        errors.append("operator_envelope.applied_transforms must be a non-empty array")
    axis_assignments = envelope.get("axis_assignments")
    axis_answer_contracts = envelope.get("axis_answer_contracts")
    if not isinstance(axis_answer_contracts, Mapping):
        errors.append("operator_envelope.axis_answer_contracts must be an object")
        axis_answer_contracts = {}
    if not isinstance(axis_assignments, list) or not axis_assignments:
        errors.append("operator_envelope.axis_assignments must be a non-empty array")
    else:
        seen_axis_ids = set()
        for index, assignment in enumerate(axis_assignments):
            if not isinstance(assignment, Mapping):
                errors.append(
                    f"operator_envelope.axis_assignments[{index}] must be an object"
                )
                continue
            axis_id = str(assignment.get("axis_id") or "").strip()
            semantic_axis = str(
                assignment.get("semantic_ability_axis") or ""
            ).strip()
            if not axis_id:
                errors.append(
                    f"operator_envelope.axis_assignments[{index}].axis_id is required"
                )
            elif axis_id in seen_axis_ids:
                errors.append(f"duplicate axis_id: {axis_id}")
            seen_axis_ids.add(axis_id)
            if semantic_axis not in set(contract.ability_axes):
                errors.append(
                    f"axis {axis_id or index} does not belong to selected operator"
                )
            if not isinstance(assignment.get("operator_payload"), Mapping):
                errors.append(
                    f"operator_envelope.axis_assignments[{index}].operator_payload must be an object"
                )
            else:
                errors.extend(
                    f"axis {axis_id or index}: {error}"
                    for error in validate_operator_payload(
                        resolved_operator,
                        assignment.get("operator_payload"),
                    )
                )
            if not str(assignment.get("answer_contract_id") or "").strip():
                errors.append(
                    f"operator_envelope.axis_assignments[{index}].answer_contract_id is required"
                )
            axis_contract = axis_answer_contracts.get(axis_id)
            if not isinstance(axis_contract, Mapping):
                errors.append(f"axis {axis_id or index} has no frozen answer contract")
            elif assignment.get("answer_contract_id") != axis_contract.get(
                "answer_contract_hash"
            ):
                errors.append(f"axis {axis_id or index} answer contract ID mismatch")
            elif axis_contract.get("answer_contract_hash") != answer_contract_hash(
                axis_contract
            ):
                errors.append(f"axis {axis_id or index} answer contract hash mismatch")
    axis_interactions = envelope.get("axis_interactions")
    if not isinstance(axis_interactions, list):
        errors.append("operator_envelope.axis_interactions must be an array")
    else:
        for index, interaction in enumerate(axis_interactions):
            if not isinstance(interaction, Mapping):
                errors.append(
                    f"operator_envelope.axis_interactions[{index}] must be an object"
                )
                continue
            for field in (
                "source_axis_id",
                "target_axis_id",
                "relation",
                "interaction_contract_id",
            ):
                if not str(interaction.get(field) or "").strip():
                    errors.append(
                        f"operator_envelope.axis_interactions[{index}].{field} is required"
                    )
    resolution = envelope.get("answer_contract_resolution")
    if not isinstance(resolution, Mapping):
        errors.append("operator_envelope.answer_contract_resolution must be an object")
    elif resolution.get("required") is True and resolution.get("status") != "resolved":
        errors.append("answer contract was not resolved by an independent blind solver")
    return errors


def collect_referenced_fact_ids(value: Any) -> List[str]:
    """Collect fact references without stripping unknown payload fields."""

    found: List[str] = []

    def add(candidate: Any) -> None:
        if isinstance(candidate, str) and candidate.strip() and candidate.strip() not in found:
            found.append(candidate.strip())

    def visit(node: Any, key: str = "") -> None:
        if isinstance(node, Mapping):
            for child_key, child in node.items():
                if child_key.endswith("_fact_id"):
                    add(child)
                elif child_key.endswith("_fact_ids") and isinstance(child, list):
                    for item in child:
                        add(item)
                else:
                    visit(child, child_key)
        elif isinstance(node, list):
            for child in node:
                visit(child, key)

    visit(value)
    return found


def contract_registry_snapshot() -> Dict[str, Dict[str, Any]]:
    return {
        operator_id: contract.to_dict()
        for operator_id, contract in OPERATOR_CONTRACTS.items()
    }
