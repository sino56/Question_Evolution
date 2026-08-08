# Question Evolution Agent Skills

`agent_skills/` contains versioned, read-only operating procedures for the Question Evolution Agent Harness. A Skill is neither a pipeline tool nor an O10–O33 business operator:

- A Tool performs a registered action such as running the loop, observing artifacts, or writing a report.
- An Operator generates a candidate question inside the established evolution pipeline.
- A Skill defines how an Agent analyzes bounded evidence and writes an auditable, non-executable recommendation.

## Loading and safety contract

`agent_runtime.skills.skill_registry` is the sole registry. `load_stage_skills()` validates that the SKILL document and output schema exist, verifies its seven required sections, enforces declared context layers, and emits `skill_loaded` or `skill_load_failed`. A load failure falls back to the existing base safety rules; it never adds authority or blocks the formal pipeline.

Every Skill output must retain `evidence_refs` or `artifact_refs`. `validate_skill_output()` rejects active publication, final human-boundary confirmation, missing evidence, protected mutations, or oversized unauditable output. The existing policy, schema, Plan Validator, Global Judge publish gate and human review remain authoritative.

## Registry mapping

| Priority | Skill | Runtime stage |
| --- | --- | --- |
| P0 | `experiment-review-skill` | `post_experiment_review` |
| P0 | `agent-report-skill` | `agent_reporting` |
| P0 | `recovery-diagnosis-skill` | `recovery_diagnosis` |
| P1 | `memory-compile-skill` | `memory_compilation` |
| P1 | `strategy-proposal-skill` | `strategy_proposal` |
| P1 | `operator-diagnosis-skill` | `post_experiment_review` |
| P1 | `human-review-precheck-skill` | `human_review_precheck` |
| P2 | `planning-strategy-skill` | `planning_strategy` |
| P2 | `multi-agent-advisor-skill` | `multi_agent_advice` |
| P2 | `model-routing-skill` | `model_routing` |

Minimal, schema-checked input/output examples are in [`examples/`](examples/). They are deliberately redacted summaries rather than prompts, answers, complete logs, or complete Memory.
