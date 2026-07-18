import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


RUN_LOOP = (ROOT / "run_loop.sh").read_text(encoding="utf-8")


def assert_in_order(text, snippets):
    cursor = -1
    for snippet in snippets:
        position = text.find(snippet)
        assert position != -1, f"missing snippet: {snippet}"
        assert position > cursor, f"snippet out of order: {snippet}"
        cursor = position


def test_run_loop_uses_stage06_full_pipeline_order():
    assert_in_order(
        RUN_LOOP,
        [
            "Step 1/13: profile_samples.py",
            "Step 2/13: select_evolution_candidates.py",
            "Step 3/13: operator_router.py",
            "Step 4/13: question_evolution.py",
            "Step 5/13: validate_evolved_question.py",
            "Step 6/13: light_factual_check.py",
            "Step 7/13: validate_difficulty_gain.py",
            "Step 8/13: candidate_selection.py",
            "Step 9/13: collect_answers.py",
            "Step 10/13: gen_rubric.py",
            "Step 11/13: scoring.py",
            "Step 12/13: analyze_evolution_effect.py",
            "Step 13/13: update_sample_state.py",
        ],
    )


def test_run_loop_carries_state_forward_and_guards_memory_writes():
    assert 'MEMORY_DIR="$EXP_DIR/memory"' in RUN_LOOP
    assert 'run_if_missing "$ROUND_DIR/state_updated.jsonl"' in RUN_LOOP
    assert 'ROUND_OUTPUT_FOR_NEXT="$ROUND_DIR/state_updated.jsonl"' in RUN_LOOP
    assert 'PREV_SCORED="$ROUND_OUTPUT_FOR_NEXT"' in RUN_LOOP
    assert '--memory-dir "$MEMORY_DIR"' in RUN_LOOP
    assert '--preselection-invalid-input "$ROUND_DIR/invalid_generation_cases.jsonl"' in RUN_LOOP
    assert '--invalid-output "$ROUND_DIR/invalid_generation_cases.jsonl"' in RUN_LOOP
    assert 'validate_candidate_coverage "$ROUND_DIR/routed.jsonl" "$ROUND_DIR/candidates.jsonl"' in RUN_LOOP
    assert 'difficulty_validated_candidates.jsonl' in RUN_LOOP
    assert 'difficulty_gain_report.json' in RUN_LOOP
    assert 'light_factual_checked_candidates.jsonl' in RUN_LOOP
    assert 'light_factual_report.json' in RUN_LOOP
    assert 'candidate_selection_report.json' in RUN_LOOP
    assert 'operator_router_report.json' in RUN_LOOP
    assert 'state_update_report.json' in RUN_LOOP


def test_run_loop_defaults_to_admitted_input_with_legacy_fallback():
    assert 'DEFAULT_INPUT_FILE="admitted_seed_samples.jsonl"' in RUN_LOOP
    assert 'LEGACY_INPUT_FILE="data/data.jsonl"' in RUN_LOOP
    assert 'INPUT_FILE=${INPUT_FILE:-$DEFAULT_INPUT_FILE}' in RUN_LOOP
    assert "请设置 INPUT_FILE 指向" in RUN_LOOP


def test_run_loop_can_resume_an_existing_experiment_without_resetting_summary():
    assert 'RESUME_EXP_DIR=${RESUME_EXP_DIR:-}' in RUN_LOOP
    assert '--resume-exp-dir)' in RUN_LOOP
    assert 'EXP_DIR=$(cd "$RESUME_EXP_DIR" && pwd)' in RUN_LOOP
    assert 'if [ ! -f "$SUMMARY_FILE" ]; then' in RUN_LOOP
    assert 'append_summary_round_if_missing()' in RUN_LOOP


def test_run_loop_injects_the_current_round_into_each_round_input():
    assert "prepare_round_input()" in RUN_LOOP
    assert 'item["round"] = round_number' in RUN_LOOP
    assert 'prepare_round_input "$INPUT_FILE" "$ROUND_DIR/input.jsonl" "$ROUND"' in RUN_LOOP
    assert 'prepare_round_input "$PREV_SCORED" "$ROUND_DIR/input.jsonl" "$ROUND"' in RUN_LOOP


def test_run_loop_uses_rolled_back_state_for_global_stop_checks():
    assert 'AVG_RATE=$(compute_avg_score_rate "$ROUND_OUTPUT_FOR_NEXT")' in RUN_LOOP
    assert 'CONTINUE_COUNT=$(extract_continue_count "$ROUND_OUTPUT_FOR_NEXT")' in RUN_LOOP
    assert '"$CONTINUE_COUNT" -eq 0' in RUN_LOOP


def test_run_loop_uses_existing_stage_cli_flags():
    assert "--high-score-threshold \"$MIN_SCORE_RATE\"" in RUN_LOOP
    assert "--enable-uncertain-low-probe" in RUN_LOOP
    assert "--uncertain-low-probe-min-score \"$UNCERTAIN_LOW_PROBE_MIN_SCORE\"" in RUN_LOOP
    assert "--failure-memory-window-rounds \"$FAILURE_MEMORY_WINDOW_ROUNDS\"" in RUN_LOOP
    assert "--min-score-rate \"$MIN_SCORE_RATE\"" in RUN_LOOP
    assert "--num-candidates \"$NUM_CANDIDATES\"" in RUN_LOOP
    assert "--max-candidate-budget \"$MAX_CANDIDATE_BUDGET\"" in RUN_LOOP
    assert "--validation-retries \"$VALIDATION_RETRIES\"" in RUN_LOOP
    assert "--min-gain-score \"$MIN_DIFFICULTY_GAIN_SCORE\"" in RUN_LOOP
    assert "--borderline-gain-score \"$BORDERLINE_DIFFICULTY_GAIN_SCORE\"" in RUN_LOOP
    assert "--min-competitive-judgment-score \"$MIN_COMPETITIVE_JUDGMENT_SCORE\"" in RUN_LOOP
    assert "--enable-weak-probe" in RUN_LOOP
    assert "--weak-answer-model \"$WEAK_ANSWER_MODEL\"" in RUN_LOOP
    assert "--weak-answer-base-url \"$WEAK_ANSWER_BASE_URL\"" in RUN_LOOP
    assert "--judge-base-url \"$QWEN_BASE_URL\"" in RUN_LOOP
    assert "--judge-api-key \"$QWEN_API_KEY\"" in RUN_LOOP
    assert "--answer-trials \"$SCORING_ANSWER_TRIALS\"" in RUN_LOOP
    assert "--qwen-judge-repeats \"$QWEN_JUDGE_REPEATS\"" in RUN_LOOP
    assert "--gpt-judge-base-url \"$GPT_JUDGE_BASE_URL\"" in RUN_LOOP
    assert "--gpt-judge-api-key \"$GPT_JUDGE_API_KEY\"" in RUN_LOOP
    assert "--gpt-judge-model \"$GPT_JUDGE_MODEL\"" in RUN_LOOP
    assert "--gpt-judge-repeats \"$GPT_JUDGE_REPEATS\"" in RUN_LOOP
    assert "--qwen-max-concurrent \"$QWEN_SCORING_MAX_CONCURRENT\"" in RUN_LOOP
    assert "--gpt-max-concurrent \"$GPT_SCORING_MAX_CONCURRENT\"" in RUN_LOOP
    assert "--base-url \"$ANSWER_BASE_URL\"" in RUN_LOOP
    assert "--base-url \"$RUBRIC_BASE_URL\"" in RUN_LOOP
    select_call_start = RUN_LOOP.find("python select_evolution_candidates.py")
    route_call_start = RUN_LOOP.find("python operator_router.py")
    assert select_call_start != -1
    assert route_call_start != -1
    select_call = RUN_LOOP[select_call_start:route_call_start]
    assert "--min-score-rate" not in select_call


def test_run_loop_keeps_rubric_and_scoring_as_closed_loop_steps_only():
    rubric_call_start = RUN_LOOP.find("python gen_rubric.py")
    scoring_call_start = RUN_LOOP.find("Step 11/13: scoring.py")
    assert rubric_call_start != -1
    assert scoring_call_start != -1
    assert rubric_call_start < scoring_call_start

    rubric_call = RUN_LOOP[rubric_call_start:scoring_call_start]
    assert "--prompt-version" not in rubric_call
    assert "expected_evaluation_focus" not in RUN_LOOP
    assert "judge agreement" not in RUN_LOOP.lower()


if __name__ == "__main__":
    test_run_loop_uses_stage06_full_pipeline_order()
    test_run_loop_carries_state_forward_and_guards_memory_writes()
    test_run_loop_defaults_to_admitted_input_with_legacy_fallback()
    test_run_loop_injects_the_current_round_into_each_round_input()
    test_run_loop_uses_rolled_back_state_for_global_stop_checks()
    test_run_loop_uses_existing_stage_cli_flags()
    test_run_loop_keeps_rubric_and_scoring_as_closed_loop_steps_only()
    print("stage06 run loop integration checks passed")
