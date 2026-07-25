# Operator Prompts

Stage 3 implements O10-O18 as small prompt specs:

- `O10_evidence_sufficiency_ladder.py`: close business-judgment competition.
- `O11_unobserved_state_attribution.py`: unobserved-state attribution.
- `O12_conjunctive_necessity.py`: strong-clue versus unclosed-threshold distinction.
- `O13_minimal_disqualifier.py`: whether a new fact changes an existing evaluation.
- `O14_information_closure.py`: information-closure boundary.
- `O15_counterfactual_threshold_shift.py`: single-variable threshold shift.
- `O16_close_alternative_normalization.py`: close-alternative normalization.
- `O17_action_vs_fact_threshold.py`: action versus fact threshold.
- `O18_baseline_scope_mismatch.py`: baseline-scope mismatch.

`__init__.py` exposes the registry consumed by `question_evolution.py`.
