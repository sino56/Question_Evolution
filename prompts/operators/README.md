# Operator Prompts

Stage 3 keeps the stable O10-O18 IDs and adds stable O19-O33 IDs while defining each operator through the
same content-level template: reasoning object, question construction, semantic
transformation, invariants, competition structure, preserved parent
obligations, required reasoning tasks, target/excluded errors, adjacent
boundaries, controls, and allowed/forbidden answer shapes.

- `O10_evidence_sufficiency_ladder.py`: discover a minimal sufficient fact set
  and verify its members through internal ablation.
- `O11_unobserved_state_attribution.py`: test endpoint, time-window, and path
  consistency without inventing an event inside the unobserved interval.
- `O12_conjunctive_necessity.py`: compare hidden X-only, Y-only, and X+Y
  scenarios to test independent and joint necessity.
- `O13_minimal_disqualifier.py`: identify a failed required link and separate
  its local effect from its effect on the overall claim.
- `O14_information_closure.py`: retain the information-closure content
  identity only; it does not define an independent question type.
- `O15_counterfactual_threshold_shift.py`: vary one fact while keeping one
  comparison quantity, one conclusion layer, and any stated threshold fixed.
- `O16_close_alternative_normalization.py`: compare one target explanation
  with one close alternative through coverage, residuals, and a discriminator.
- `O17_action_vs_fact_threshold.py`: map current facts to two explicitly
  provided business rules and keep action and fact conclusions distinct.
- `O18_baseline_scope_mismatch.py`: choose a comparable baseline by inclusion
  scope and assess the same observation against it.
- `O19_multi_entity_role_binding`: bind actions, time spans, and directional
  roles to the correct entity without merging or ignoring swaps.
- `O20_multistage_event_breakpoint`: recover a multistage state chain and
  locate the breakpoint that changes its downstream effect.
- `O21_object_provenance_identity`: track an object's provenance and identity
  through transfer, occlusion, reappearance, and competing sources.
- `O22_path_topology_reachability`: jointly apply topology, direction,
  endpoint, and time-window constraints to reachability.
- `O23_observation_reliability_conflict`: determine what an observation is
  reliable enough to establish before using it as a fact.
- `O24_multi_hypothesis_residual_ranking`: rank competing explanations through
  coverage, conflict, residuals, and additional-assumption cost.
- `O25_procedural_invariant_frame`: preserve record mappings,
  reference frames, units, and step dependencies across a procedure.
- `O26_quantitative_threshold_propagation`: propagate given uncertainty
  and units into a result interval before applying a business threshold.
- `O27_cross_layer_conclusion_calibration`: calibrate the chain from
  observation and support through fact, writeable conclusion, and action.
- `O28_multihop_chain_closure`: verify that a chain remains closed across
  stages, nodes, entities, paths, and endpoint obligations.
- `O29_entity_identity_conflict_resolution`: resolve supporting and exclusive
  identity evidence without globalizing a local binding.
- `O30_active_discriminative_observation`: select a feasible next observation
  whose possible outcomes distinguish the live explanations.
- `O31_observation_accumulation_calibration`: distinguish independent
  information gain from same-source repetition and derived observations.
- `O32_role_graph_critical_edge`: recover directed role edges and identify
  which edge or functionally equivalent path is necessary to the conclusion.
- `O33_cross_modal_support_boundary`: align source scope, time, entity, and
  conflicts before stating the strongest cross-modal conclusion.

The shared prompt renders these fields as internal generation controls and
explicitly forbids copying their labels, roles, expected direction, or answer
decomposition into the question. Generating operators remain callable through
`build_operator_prompt`; `question_evolution.py` consumes the same stable
registry. The O19-O33 definitions live in `new_operator_specs.py`; their
`semantic_axes` are internal content dependencies, not a public answer schema.

This directory implements content expansion and repair only. It does not add fact-slot
applicability gates, semantic/policy versions, payload schemas, answer
contracts, hard rejection, qualification status, release checks, or an O14
validator. It also does not add adapters, shadow routing, rollout gates, retry
state, scorer mappings, or forced qualification. Those belong to the second
parts of the operator expansion plans.
