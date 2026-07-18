import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


WINDOWS_LOOP = (ROOT / "run_loop.ps1").read_text(encoding="utf-8-sig")
WINDOWS_LAUNCHER = (ROOT / "run_loop_windows.cmd").read_text(encoding="utf-8")


def assert_in_order(text, snippets):
    cursor = -1
    for snippet in snippets:
        position = text.find(snippet)
        assert position != -1, f"missing snippet: {snippet}"
        assert position > cursor, f"snippet out of order: {snippet}"
        cursor = position


def test_windows_loop_has_the_same_round_pipeline_order():
    assert_in_order(
        WINDOWS_LOOP,
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


def test_windows_loop_starts_a_new_experiment_and_propagates_round():
    assert 'Join-Path $dayDir "exp"' in WINDOWS_LOOP
    assert 'Join-Path $dayDir "exp$index"' in WINDOWS_LOOP
    assert 'item["round"] = round_number' in WINDOWS_LOOP
    assert "Prepare-RoundInput $previousScored $roundInput $round" in WINDOWS_LOOP


def test_windows_loop_can_resume_an_existing_experiment_without_resetting_summary():
    assert '[string]$ResumeExperimentDir = $env:RESUME_EXP_DIR' in WINDOWS_LOOP
    assert '$isResume = -not [string]::IsNullOrWhiteSpace($ResumeExperimentDir)' in WINDOWS_LOOP
    assert '$EXP_DIR = Resolve-ProjectPath $ResumeExperimentDir' in WINDOWS_LOOP
    assert 'if (-not (Test-Path -LiteralPath $SUMMARY_FILE -PathType Leaf))' in WINDOWS_LOOP
    assert 'function Add-SummaryRoundIfMissing' in WINDOWS_LOOP


def test_windows_loop_fails_fast_and_launcher_is_one_click_safe():
    assert "if ($LASTEXITCODE -ne 0)" in WINDOWS_LOOP
    assert 'throw "Python command failed' in WINDOWS_LOOP
    assert "powershell.exe -NoProfile -ExecutionPolicy Bypass -File" in WINDOWS_LAUNCHER
    assert "pause" in WINDOWS_LAUNCHER.lower()


def test_windows_loop_exposes_separate_scoring_and_answer_request_limits():
    assert '$SCORING_CONCURRENCY = Get-EnvOrDefault "SCORING_CONCURRENCY" "20"' in WINDOWS_LOOP
    assert '"--qwen-max-concurrent" $QWEN_SCORING_MAX_CONCURRENT' in WINDOWS_LOOP
    assert '"--gpt-max-concurrent" $GPT_SCORING_MAX_CONCURRENT' in WINDOWS_LOOP
    assert '"--request-concurrency" $ANSWER_REQUEST_CONCURRENCY' in WINDOWS_LOOP
