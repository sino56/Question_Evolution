# Operator Prompts

O10-O33 use a shared content-definition template.  Each generation spec records
its ability axis, reasoning object, content transformation, invariants,
competition structure, parent obligations, observable reasoning outputs, error
taxonomy, adjacent boundaries and positive/negative/surface controls.

- `O10_evidence_sufficiency_ladder.py`: minimal sufficient fact set.
- `O11_unobserved_state_attribution.py`: endpoint temporal consistency.
- `O12_conjunctive_necessity.py`: independent joint necessity.
- `O13_minimal_disqualifier.py`: required-link failure and claim-level effect.
- `O14_information_closure.py`: global validation-only information closure.
- `O15_counterfactual_threshold_shift.py`: one quantity and one conclusion layer.
- `O16_close_alternative_normalization.py`: close-explanation coverage and residuals.
- `O17_action_vs_fact_threshold.py`: dual explicit-rule scope mapping.
- `O18_baseline_scope_mismatch.py`: baseline inclusion scope and anomaly effect.

`OPERATOR_SPECS` includes the O14 validation identity.  Generation callers use
`GENERATION_OPERATOR_SPECS`; `build_operator_prompt` rejects O14 so it cannot
silently become a question-generation fallback.

`new_operator_specs.py` contains the stable content definitions for O19-O33.
O19-O27 cover entity/role binding, multistage breakpoints, object provenance,
path topology, observation reliability, hypothesis residuals, procedural
invariants, quantitative propagation, and cross-layer calibration. O28-O33
cover multihop closure, identity conflict resolution, active observation,
observation accumulation, role-graph critical edges, and cross-modal support.

Their content specs are callable through `build_operator_prompt`, but their
mechanism contracts remain `qualification_only`. The rule Router records them
as `recognized_operator_id` and shadow plans; they cannot become production
fallbacks until forced qualification and natural-routing evidence support a
separate lifecycle transition.
