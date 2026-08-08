# Pipeline Field Contract

This directory defines the minimal JSONL contract used by the question
evolution stages. The schemas are intentionally small and permissive: they
stabilize field names, ownership, and runtime validation for the current
Stage 0-5 question evolution pipeline.

## 22B / 25B mechanism governance sidecars

`mechanism_candidate.schema.json` describes proposed, evidence-bound behavior
mechanisms and risk patterns. `mechanism_effect_validation.schema.json` records
frozen, root-sample-held-out validation of a mechanism/operator hypothesis.
`mechanism_route_audit.schema.json` records audit-only routing comparisons. All
three are sidecar contracts: none authorizes mutation of scored records,
operator candidates, frozen plans, state, or local memory.

## Agent Harness Contracts

`agent_tool_call.schema.json` and `agent_tool_result.schema.json` describe
the redacted, versioned Tool Registry boundary. `agent_observation.schema.json`
keeps the Stage 1/2 aggregate experiment summary and adds the Phase 3
`observations` timeline, whose normalized entries are the only format consumed
by the Reflector. `agent_tool_event.schema.json` records call IDs, idempotency,
retry metadata, checkpoints, and state transitions without raw environment,
model-response, or JSONL payloads.

`context_pack_v2.schema.json` and `context_cache.schema.json` define the
cache-safe Agent context layers and their audit-only hashes.  They preserve
legacy context fields while keeping dynamic paths, observations, and errors
outside the stable prompt prefix.

## Stage 7 Multi-agent Advisor Contracts

`advisor_spec.schema.json` registers each read-only advisor, its trigger,
input and tool allowlists, model capability tiers, timeout/retry policy, and
structured-output requirements. `advisor_run_record.schema.json` is the
append-only run audit record. `advisor_advice.schema.json` standardizes
evidence-bound advice for conservative merging. None of these contracts grants
an advisor access to formal experiment artifacts, prompts, Router output,
operators, scores, or active global Memory.

## Record Ownership

- `sample_id`, `index`, `round`, `prompt`, `meta_info`, `rubric`,
  `score_prompt`, `scoring_result`, `score_rate`, and `question_evolved` are
  shared pipeline fields.
- Dual Judge scoring optionally adds `evaluation_protocol`,
  `qwen_score_summary`, `gpt_score_summary`, and
  `representative_trial_index`. `scoring_result.answer_trials` keeps the
  ordered per-answer Qwen/GPT repeat records. Only the Qwen summary may drive
  the top-level `score_rate`; the GPT summary is experimental metadata.
- `sample_profile` is produced by `profile_samples.py`; `overscore_diagnosis`
  is produced by the same profiling step and consumed by
  `select_evolution_candidates.py`.
- `operator_route` is produced by the Stage 3 router and consumed by
  `question_evolution.py` when `evolution_action` requires evolution.
- `candidate_group_id`, `candidate_id`, `candidate_operator`, and
  `candidate_generation` are Stage 4 intermediate fields produced only when
  `question_evolution.py --num-candidates` is greater than one.
- `evolution_state` is the cross-round state produced by
  `update_sample_state.py` and consumed by candidate selection, routing, and
  stop rules in later rounds.
- `probe_middle_score_boundary` keeps worthwhile middle-score samples in the
  evolution path. `rollback_and_reroute` marks a score-increased child that was
  restored to its direct parent and must avoid the failed operator next round.
- `meta_info.question_evolution_metadata` is produced by question evolution.
- `validation_result` is produced by `validate_evolved_question.py`.
  The script can optionally run `--validate-schema` to check pipeline records
  against these local schemas.
- `candidate_selection` is produced by `candidate_selection.py`. It keeps the
  legacy selected fields and also records `candidate_flow` plus
  `selected_for_exploration` so weak candidates can be scored under the
  bounded exploration budget without being treated as main-chain successes.
- `effect_analysis` is produced by `analyze_evolution_effect.py` after the
  standard scoring loop has produced a new scored record. It marks
  `score_increased_after_evolution=true` when an evolved question raises the
  score beyond the configured increase threshold.

## Pass-Through Semantics

When `question_evolved` is `false`, downstream scripts must pass the record
through without regenerating answers, rubrics, or scores. Existing
`collect_answers.py`, `gen_rubric.py`, and `scoring.py` already follow this
top-level flag.

## Rubric Boundary

`expected_evaluation_focus` is allowed only inside
`meta_info.question_evolution_metadata`. It is metadata for question generation,
manual review, and later routing. It must not be copied into `gen_rubric.py`,
rubric prompts, score prompts, rubric items, weights, or judge calibration.

## Memory Files

The `memory/*.jsonl` files are append-only artifacts. `update_sample_state.py`
writes low-risk Stage 5 entries after effect analysis: effective operator
experience, failed operator experience, and invalid generation cases. Low
confidence hits must keep `needs_manual_review=true` and must not be treated as
strong success examples without review.

## Multi-Operator Search

- `search_state.schema.json` defines the lightweight scheduler state and
  explicitly forbids embedded full branch collections. Live states additionally
  persist a secret-free route identity and fingerprint for safe resume.
- `branch_result.schema.json` defines complete append-only branch results,
  including independent decision and experimental evaluation status; live
  branches carry the same route fingerprint as their frozen parent plan.
- `vertical_search_state.schema.json` keeps only recoverable vertical scheduler
  references; full nodes and paths stay in sidecar artifacts.
- `vertical_node.schema.json`, `vertical_attempt.schema.json`,
  `boundary_edge.schema.json`, and `boundary_path.schema.json` define the
  ordered, directly comparable vertical-search evidence records.
