from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SH_LOOP = (ROOT / "run_loop.sh").read_text(encoding="utf-8")
PS_LOOP = (ROOT / "run_loop.ps1").read_text(encoding="utf-8")


def test_search_window_defaults_to_live_multi_operator_branch_in_both_loops():
    assert "SEARCH_MODE=${SEARCH_MODE:-multi_operator_branch}" in SH_LOOP
    assert "SEARCH_BRANCH_WINDOW=${SEARCH_BRANCH_WINDOW:-1}" in SH_LOOP
    assert '$SEARCH_MODE = Get-EnvOrDefault "SEARCH_MODE" "multi_operator_branch"' in PS_LOOP
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


def test_default_entrypoints_use_hybrid_live_router_and_pass_its_transport_settings():
    assert "ROUTING_MODE=${ROUTING_MODE:-hybrid}" in SH_LOOP
    assert "ASSIGNMENT_MODE=${ASSIGNMENT_MODE:-live}" in SH_LOOP
    assert "ROUTER_CONCURRENCY=${ROUTER_CONCURRENCY:-20}" in SH_LOOP
    assert "ROUTER_TIMEOUT=${ROUTER_TIMEOUT:-60}" in SH_LOOP
    assert "ROUTER_RETRIES=${ROUTER_RETRIES:-0}" in SH_LOOP
    assert '--routing-mode "$ROUTING_MODE"' in SH_LOOP
    assert '--assignment-mode "$ASSIGNMENT_MODE"' in SH_LOOP
    assert '--router-cache "$EXP_DIR/router_cache.jsonl"' in SH_LOOP
    assert '--router-trace-output "$ROUND_DIR/router_traces.jsonl.gz"' in SH_LOOP
    assert '--assignment-mode "$ASSIGNMENT_MODE"' in SH_LOOP

    assert '$ROUTING_MODE = Get-EnvOrDefault "ROUTING_MODE" "hybrid"' in PS_LOOP
    assert '$ASSIGNMENT_MODE = Get-EnvOrDefault "ASSIGNMENT_MODE" "live"' in PS_LOOP
    assert '$ROUTER_CONCURRENCY = Get-EnvOrDefault "ROUTER_CONCURRENCY" "20"' in PS_LOOP
    assert '"--routing-mode" $ROUTING_MODE' in PS_LOOP
    assert '"--assignment-mode" $ASSIGNMENT_MODE' in PS_LOOP
    assert '"--router-cache" (Join-Path $EXP_DIR "router_cache.jsonl")' in PS_LOOP


def test_vertical_operator_mode_is_wired_with_depth_and_protection_controls():
    assert "multi_operator_vertical_stack" in SH_LOOP
    assert "python vertical_operator_search.py" in SH_LOOP
    assert 'SEARCH_MAX_DEPTH=${SEARCH_MAX_DEPTH:-3}' in SH_LOOP
    assert '--max-depth "$SEARCH_MAX_DEPTH"' in SH_LOOP
    assert '--max-request-attempts-per-sample "$SEARCH_MAX_REQUEST_ATTEMPTS_PER_SAMPLE"' in SH_LOOP
    assert '"vertical_operator_search.py", "--input", $routed' in PS_LOOP
    assert '"--max-depth", $SEARCH_MAX_DEPTH' in PS_LOOP
    assert '$SEARCH_ALLOW_OPERATOR_REPEAT_IN_PATH -eq "true"' in PS_LOOP
