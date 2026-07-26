from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SH_LOOP = (ROOT / "run_loop.sh").read_text(encoding="utf-8")
PS_LOOP = (ROOT / "run_loop.ps1").read_text(encoding="utf-8")


def test_search_window_defaults_to_safe_single_branch_in_both_loops():
    assert "SEARCH_MODE=${SEARCH_MODE:-single_branch}" in SH_LOOP
    assert "SEARCH_BRANCH_WINDOW=${SEARCH_BRANCH_WINDOW:-1}" in SH_LOOP
    assert '$SEARCH_MODE = Get-EnvOrDefault "SEARCH_MODE" "single_branch"' in PS_LOOP
    assert '$SEARCH_BRANCH_WINDOW = Get-EnvOrDefault "SEARCH_BRANCH_WINDOW" "1"' in PS_LOOP


def test_search_pipeline_rollback_and_experimental_switches_are_exposed():
    assert "SEARCH_PIPELINE_MODE=${SEARCH_PIPELINE_MODE:-step}" in SH_LOOP
    assert "SEARCH_ARTIFACT_RETENTION=${SEARCH_ARTIFACT_RETENTION:-compact}" in SH_LOOP
    assert "DEFER_GPT_EXPERIMENTAL_EVALUATION=${DEFER_GPT_EXPERIMENTAL_EVALUATION:-false}" in SH_LOOP
    assert "SEARCH_OPERATOR_SORT_MODE=${SEARCH_OPERATOR_SORT_MODE:-route}" in SH_LOOP
    assert '$SEARCH_PIPELINE_MODE = Get-EnvOrDefault "SEARCH_PIPELINE_MODE" "step"' in PS_LOOP
    assert '$SEARCH_ARTIFACT_RETENTION = Get-EnvOrDefault "SEARCH_ARTIFACT_RETENTION" "compact"' in PS_LOOP
    assert '$DEFER_GPT_EXPERIMENTAL_EVALUATION = Get-EnvOrDefault "DEFER_GPT_EXPERIMENTAL_EVALUATION" "false"' in PS_LOOP


def test_multi_operator_mode_is_wired_to_the_production_search_runner():
    assert "python multi_operator_search.py" in SH_LOOP
    assert '--branch-window "$SEARCH_BRANCH_WINDOW"' in SH_LOOP
    assert '--boundary-target "$SEARCH_BOUNDARY_TARGET"' in SH_LOOP
    assert '--pipeline-mode "$SEARCH_PIPELINE_MODE"' in SH_LOOP
    assert '--artifact-retention "$SEARCH_ARTIFACT_RETENTION"' in SH_LOOP
    assert "SEARCH_EXTRA_ARGS+=(--defer-gpt-experimental-evaluation)" in SH_LOOP
    assert "export EVO_CONCURRENCY DIFFICULTY_GAIN_CONCURRENCY" in SH_LOOP
    assert '"multi_operator_search.py", "--input", $routed' in PS_LOOP
    assert '"--branch-window", $SEARCH_BRANCH_WINDOW' in PS_LOOP
    assert '"--pipeline-mode", $SEARCH_PIPELINE_MODE' in PS_LOOP
    assert '"--artifact-retention", $SEARCH_ARTIFACT_RETENTION' in PS_LOOP
    assert '$searchArgs += "--defer-gpt-experimental-evaluation"' in PS_LOOP
    assert "$env:EVO_CONCURRENCY = [string]$EVO_CONCURRENCY" in PS_LOOP


def test_vertical_operator_mode_is_wired_with_depth_and_protection_controls():
    assert "multi_operator_vertical_stack" in SH_LOOP
    assert "python vertical_operator_search.py" in SH_LOOP
    assert 'SEARCH_MAX_DEPTH=${SEARCH_MAX_DEPTH:-3}' in SH_LOOP
    assert '--max-depth "$SEARCH_MAX_DEPTH"' in SH_LOOP
    assert '--max-request-attempts-per-sample "$SEARCH_MAX_REQUEST_ATTEMPTS_PER_SAMPLE"' in SH_LOOP
    assert '"vertical_operator_search.py", "--input", $routed' in PS_LOOP
    assert '"--max-depth", $SEARCH_MAX_DEPTH' in PS_LOOP
    assert '$SEARCH_ALLOW_OPERATOR_REPEAT_IN_PATH -eq "true"' in PS_LOOP
