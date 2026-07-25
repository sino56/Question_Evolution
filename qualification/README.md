# Operator Qualification

`operator_qualification.py` separates operator Prompt qualification from Router
validation.

## Forced qualification

Input records must already have been generated with one forced operator and an
isolated memory namespace. Each record must include:

- `candidate_operator`;
- `qualification_manifest.human_confirmed=true`;
- the normal `validation_result`;
- `qualification.answer_unique_and_rubric_consistent`;
- `qualification.no_surface_leakage`;
- `qualification.parent_obligations_preserved`;
- `qualification.required_reasoning_observable`;
- `qualification.non_isomorphic_to_adjacent`;
- `qualification.neighbor_attribution_correct`;
- `qualification.target_error_taxonomy_hit`;
- `qualification.manual_boundary_confirmed`;
- for O19-O33, `qualification.required_slots_complete`;
- for O19-O33, `qualification.operator_payload_replayable`;
- for O19-O33, `qualification.gold_answer_contract_consistent`;
- for O19-O33, `qualification.content_controls_consistent`;
- for O19-O33, `qualification.adapter_semantics_preserved`;
- for O19-O33, `qualification.manual_review_record_ignored`;
- optional `qualification.semantic_direction` and `effect_analysis`.

Example:

```bash
python operator_qualification.py \
  --mode forced \
  --operator-id O13_minimal_disqualifier \
  --input qualification/o13_forced_records.jsonl \
  --output qualification/o13_forced_report.json \
  --qualification-run-id o13-holdout-v1 \
  --memory-namespace isolated-o13-v1
```

Score drop is reported but never acts as the sole qualification criterion.
The command produces evidence only; it does not mutate the operator registry.

O19-O27 require at least 6 confirmed records per forced qualification set.
O28-O33 require at least 8. `new_operator_qualification_data.py` builds
development and qualification-holdout annotation templates with two business
surfaces, neighboring-family labels and operator-specific controls:

```bash
python new_operator_qualification_data.py \
  --output qualification/new_operator_annotation_templates.jsonl
```

These generated records intentionally set
`qualification_manifest.human_confirmed=false` and are not qualification
evidence. They reserve split and annotation structure only. The historical
four-scenario samples listed in the design documents are development/regression
inputs and must not be copied into qualification holdout.

## Natural routing validation

Run this only after the corresponding forced report is
`design_hypothesis_confirmed`. Use a separate holdout with
`expected_operator_id`, `expected_applicability`, and the actual
`operator_route`.

```bash
python operator_qualification.py \
  --mode natural \
  --input qualification/natural_routing_holdout.jsonl \
  --output qualification/natural_routing_report.json \
  --qualification-run-id natural-holdout-v1 \
  --qualified-operator-id O28_multihop_chain_closure
```

The natural report includes per-operator precision/recall and a confusion
matrix. Records for operators not named by `--qualified-operator-id` are
reported as skipped, so qualification-only families cannot be promoted by a
natural-routing run that bypasses forced qualification.

O11, O17 and O18 remain disabled while their evidence result is
`evidence_insufficient`. Real human-confirmed dedicated manifests are not
checked into the repository and must not be replaced with synthetic evidence.
