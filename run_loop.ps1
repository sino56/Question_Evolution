# Windows PowerShell entry point for the Question Evolution pipeline.
# Creates a new experiment by default, or resumes -ResumeExperimentDir when
# provided. Any Python stage failure still stops the pipeline immediately.

[CmdletBinding()]
param(
    [string]$InputFile = $env:INPUT_FILE,
    [string]$ExperimentRoot = $env:EXP_ROOT,
    [string]$ResumeExperimentDir = $env:RESUME_EXP_DIR,
    [int]$MaxRounds = 0,
    [string]$PythonExe = $env:PYTHON_EXE
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
Set-Location -LiteralPath $ProjectRoot

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
    $PythonExe = "python"
}
if (-not (Get-Command $PythonExe -ErrorAction SilentlyContinue)) {
    throw "未找到 Python 可执行文件 '$PythonExe'。请安装 Python，或设置 PYTHON_EXE。"
}

function Get-EnvOrDefault {
    param([string]$Name, [string]$Default)
    $value = [Environment]::GetEnvironmentVariable($Name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim()
}

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
    & $script:PythonExe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed (exit code ${LASTEXITCODE}): ${PythonExe} $($Arguments -join ' ')"
    }
}

function Get-ConfigValue {
    param([string[]]$Names, [string]$Default = "")
    $code = "import sys; from local_api_config import get_config_value; print(get_config_value(*sys.argv[1:-1], default=sys.argv[-1]))"
    $output = & $script:PythonExe -c $code @Names $Default
    if ($LASTEXITCODE -ne 0) {
        throw "读取 local_api_config.py 失败。"
    }
    if ($null -eq $output) {
        return ""
    }
    return ($output | Select-Object -Last 1).ToString().Trim()
}

function Resolve-ProjectPath {
    param([string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) {
        return [System.IO.Path]::GetFullPath($Path)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $ProjectRoot $Path))
}

function Test-NonEmptyFile {
    param([string]$Path)
    return (Test-Path -LiteralPath $Path -PathType Leaf) -and ((Get-Item -LiteralPath $Path).Length -gt 0)
}

function Test-PublishedArtifact {
    param([string]$OutputFile, [string]$StageName, [string]$InputFile)
    & $PythonExe (Join-Path $ProjectRoot "artifact_cli.py") validate --output $OutputFile --stage $StageName --input $InputFile *> $null
    return ($LASTEXITCODE -eq 0)
}

function Invoke-Step {
    param(
        [string]$OutputFile,
        [string]$StageName,
        [string]$InputFile,
        [string]$Label,
        [scriptblock]$Action
    )
    if (Test-PublishedArtifact $OutputFile $StageName $InputFile) {
        Write-Host "检测到已验证产物 $OutputFile，跳过 $Label"
        return
    }
    if (Test-Path -LiteralPath $OutputFile) {
        throw "已有产物未通过 manifest 校验，拒绝覆盖: $OutputFile"
    }
    Write-Host $Label
    & $Action
}

function Prepare-RoundInput {
    param([string]$SourceFile, [string]$OutputFile, [int]$RoundNumber, [string]$PerformanceFile)
    # artifact_cli.py publishes each copied record after: item["round"] = round_number
    Invoke-Python "artifact_cli.py" "prepare-round-input" "--input" $SourceFile "--output" $OutputFile "--round" $RoundNumber "--performance-events" $PerformanceFile
}

function Get-AverageScoreRate {
    param([string]$ScoredFile)
$code = @'
import json
import sys

rates = []
with open(sys.argv[1], encoding="utf-8") as source:
    for line in source:
        if not line.strip():
            continue
        item = json.loads(line)
        score_rate = item.get("score_rate")
        if isinstance(score_rate, (int, float)) and 0 <= score_rate <= 1:
            rates.append(float(score_rate))
            continue
        result = item.get("scoring_result") or {}
        possible = result.get("total_possible") or 0
        if possible > 0:
            rates.append((result.get("total_awarded") or 0) / possible)
print(f"{(sum(rates) / len(rates) if rates else 0.0):.4f}")
'@
    $output = & $script:PythonExe -c $code $ScoredFile
    if ($LASTEXITCODE -ne 0) {
        throw "无法计算平均得分率。"
    }
    return ($output | Select-Object -Last 1).ToString().Trim()
}

function Get-EffectiveBoundaryCount {
    param([string]$AnalyzedFile)
    $count = 0
    foreach ($line in Get-Content -LiteralPath $AnalyzedFile -Encoding UTF8) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $record = $line | ConvertFrom-Json
        if ($null -ne $record.effect_analysis -and $record.effect_analysis.effect_label -eq "effective_boundary_probe") {
            $count += 1
        }
    }
    return $count
}

function Get-ContinueCount {
    param([string]$StateFile)
    $activeStatuses = @("continue_with_new_operator", "local_tree_search_needed", "rollback_and_reroute")
    $count = 0
    foreach ($line in Get-Content -LiteralPath $StateFile -Encoding UTF8) {
        if ([string]::IsNullOrWhiteSpace($line)) {
            continue
        }
        $record = $line | ConvertFrom-Json
        $state = $record.evolution_state
        $recommended = @()
        if ($null -ne $state) {
            $recommended = @($state.recommended_next_methods | Where-Object { -not [string]::IsNullOrWhiteSpace([string]$_) })
        }
        if ($null -ne $state -and ($activeStatuses -contains [string]$state.stop_status -or $recommended.Count -gt 0)) {
            $count += 1
        }
    }
    return $count
}

function Assert-CandidateCoverage {
    param([string]$InputFile, [string]$CandidatesFile)
$code = @'
import json
import sys

def load(path):
    with open(path, encoding="utf-8") as source:
        return [json.loads(line) for line in source if line.strip()]

def group_key(item):
    for field in ("candidate_group_id", "sample_id", "index"):
        value = item.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    meta_info = item.get("meta_info")
    if isinstance(meta_info, dict):
        prompt_old = meta_info.get("prompt_old")
        if isinstance(prompt_old, str) and prompt_old.strip():
            return prompt_old.strip()
    return str(item.get("prompt", "") or "").strip()

inputs = [group_key(item) for item in load(sys.argv[1])]
outputs = {group_key(item) for item in load(sys.argv[2])}
missing = [key for key in inputs if key and key not in outputs]
if missing:
    raise SystemExit("candidate coverage check failed; missing groups: " + ", ".join(missing[:20]))
'@
    Invoke-Python -c $code $InputFile $CandidatesFile
}

function Add-SummaryLine {
    param([string]$Path, [string]$Line)
    [System.IO.File]::AppendAllText($Path, $Line + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
}

function Add-SummaryRoundIfMissing {
    param([string]$Path, [int]$Round, [double]$ScoreRate, [string]$Status)
    $pattern = "^\s*$Round\s*\|"
    $alreadyPresent = (Test-Path -LiteralPath $Path -PathType Leaf) -and @(
        Get-Content -LiteralPath $Path -Encoding UTF8 | Where-Object { $_ -match $pattern }
    ).Count -gt 0
    if (-not $alreadyPresent) {
        Add-SummaryLine $Path ("{0,5} | {1,14:F4} | {2}" -f $Round, $ScoreRate, $Status)
    }
}

$CONFIG_BASE_URL = Get-ConfigValue @("BASE_URL", "OPENAI_BASE_URL") ""
$CONFIG_GPT_MODEL = Get-ConfigValue @("GPT_MODEL", "QA_MODEL") "gpt-5.4"
$CONFIG_QWEN_BASE_URL = Get-ConfigValue @("QWEN_BASE_URL") $CONFIG_BASE_URL
$CONFIG_QWEN_API_KEY = Get-ConfigValue @("QWEN_API_KEY") ""
$CONFIG_QWEN_MODEL = Get-ConfigValue @("QWEN_MODEL", "GPT_MODEL") "hjl_Qwen3.6-27B"
$CONFIG_GPT_JUDGE_MODEL = Get-ConfigValue @("GPT_JUDGE_MODEL", "GPT_MODEL", "QA_MODEL") $CONFIG_GPT_MODEL
$CONFIG_GPT_JUDGE_BASE_URL = Get-ConfigValue @("GPT_JUDGE_BASE_URL", "OPENAI_BASE_URL", "BASE_URL") $CONFIG_BASE_URL
$CONFIG_GPT_JUDGE_API_KEY = Get-ConfigValue @("GPT_JUDGE_API_KEY", "OPENAI_API_KEY") ""
$CONFIG_PROFILE_MODEL = Get-ConfigValue @("PROFILE_MODEL", "EVOLVE_MODEL", "QA_MODEL", "GPT_MODEL") $CONFIG_GPT_MODEL
$CONFIG_DIFFICULTY_GAIN_MODEL = Get-ConfigValue @("DIFFICULTY_GAIN_MODEL", "PROFILE_MODEL", "EVOLVE_MODEL", "QA_MODEL", "GPT_MODEL") $CONFIG_PROFILE_MODEL
$CONFIG_DIFFICULTY_GAIN_BASE_URL = Get-ConfigValue @("DIFFICULTY_GAIN_BASE_URL", "PROFILE_BASE_URL", "EVOLVE_BASE_URL", "BASE_URL", "OPENAI_BASE_URL") $CONFIG_BASE_URL
$CONFIG_WEAK_ANSWER_MODEL = Get-ConfigValue @("WEAK_ANSWER_MODEL", "QWEN_MODEL", "GPT_MODEL") $CONFIG_QWEN_MODEL
$CONFIG_WEAK_ANSWER_BASE_URL = Get-ConfigValue @("WEAK_ANSWER_BASE_URL", "QWEN_BASE_URL", "BASE_URL", "OPENAI_BASE_URL") $CONFIG_QWEN_BASE_URL
$CONFIG_WEAK_ANSWER_API_KEY = Get-ConfigValue @("WEAK_ANSWER_API_KEY", "QWEN_API_KEY") ""
$CONFIG_EVOLVE_MODEL = Get-ConfigValue @("EVOLVE_MODEL", "QA_MODEL", "GPT_MODEL") $CONFIG_GPT_MODEL
$CONFIG_ANSWER_MODEL = Get-ConfigValue @("ANSWER_MODEL", "QA_MODEL", "GPT_MODEL") $CONFIG_GPT_MODEL
$CONFIG_RUBRIC_MODEL = Get-ConfigValue @("RUBRIC_MODEL", "QA_MODEL", "GPT_MODEL") $CONFIG_GPT_MODEL

if ([string]::IsNullOrWhiteSpace($InputFile)) {
    $InputFile = if (Test-Path -LiteralPath (Join-Path $ProjectRoot "admitted_seed_samples.jsonl")) {
        "admitted_seed_samples.jsonl"
    } else {
        "data/data.jsonl"
    }
}
if ([string]::IsNullOrWhiteSpace($ExperimentRoot)) { $ExperimentRoot = "experiments" }
if ($MaxRounds -le 0) { $MaxRounds = [int](Get-EnvOrDefault "MAX_ROUNDS" "5") }

$EARLY_STOP_RATE = [double](Get-EnvOrDefault "EARLY_STOP_RATE" "0.5")
$NO_INFO_STOP_ROUNDS = [int](Get-EnvOrDefault "NO_INFO_STOP_ROUNDS" "2")
$NO_INFO_MIN_DELTA = [double](Get-EnvOrDefault "NO_INFO_MIN_DELTA" "0.0001")
$MIN_SCORE_RATE = Get-EnvOrDefault "MIN_SCORE_RATE" "0.8"
$NUM_CANDIDATES = Get-EnvOrDefault "NUM_CANDIDATES" "2"
$MAX_CANDIDATE_BUDGET = Get-EnvOrDefault "MAX_CANDIDATE_BUDGET" "0"
$VALIDATION_RETRIES = Get-EnvOrDefault "VALIDATION_RETRIES" "1"
$MIN_DIFFICULTY_GAIN_SCORE = Get-EnvOrDefault "MIN_DIFFICULTY_GAIN_SCORE" "0.75"
$BORDERLINE_DIFFICULTY_GAIN_SCORE = Get-EnvOrDefault "BORDERLINE_DIFFICULTY_GAIN_SCORE" "0.65"
$MIN_COMPETITIVE_JUDGMENT_SCORE = Get-EnvOrDefault "MIN_COMPETITIVE_JUDGMENT_SCORE" "0.60"
$DIFFICULTY_GAIN_ALLOW_BORDERLINE = Get-EnvOrDefault "DIFFICULTY_GAIN_ALLOW_BORDERLINE" "false"
$DIFFICULTY_GAIN_ENABLE_WEAK_PROBE = Get-EnvOrDefault "DIFFICULTY_GAIN_ENABLE_WEAK_PROBE" "false"
$WEAK_PROBE_MODE = Get-EnvOrDefault "WEAK_PROBE_MODE" "light"
$ENABLE_UNCERTAIN_LOW_PROBE = Get-EnvOrDefault "ENABLE_UNCERTAIN_LOW_PROBE" "false"
$UNCERTAIN_LOW_PROBE_MIN_SCORE = Get-EnvOrDefault "UNCERTAIN_LOW_PROBE_MIN_SCORE" "0.55"
$FAILURE_MEMORY_WINDOW_ROUNDS = Get-EnvOrDefault "FAILURE_MEMORY_WINDOW_ROUNDS" "3"
$ROUND0_INITIAL_TRIALS = Get-EnvOrDefault "ROUND0_INITIAL_TRIALS" "3"
$ROUND0_EXTRA_TRIALS = Get-EnvOrDefault "ROUND0_EXTRA_TRIALS" "2"
$ROUND0_MAX_TRIALS = Get-EnvOrDefault "ROUND0_MAX_TRIALS" "5"
$ROUND0_EDGE_LOW = Get-EnvOrDefault "ROUND0_EDGE_LOW" "0.72"
$ROUND0_EDGE_HIGH = Get-EnvOrDefault "ROUND0_EDGE_HIGH" "0.83"
$ROUND0_STRONG_HIGH_RATE = Get-EnvOrDefault "ROUND0_STRONG_HIGH_RATE" "0.85"
$ROUND0_ANSWER_TEMPERATURE = Get-EnvOrDefault "ROUND0_ANSWER_TEMPERATURE" "0.7"
$ROUND0_ANSWER_TOP_P = Get-EnvOrDefault "ROUND0_ANSWER_TOP_P" "0.95"
$ROUND0_ANSWER_SEED_BASE = Get-EnvOrDefault "ROUND0_ANSWER_SEED_BASE" "20260704"
$ROUND0_JUDGE_TEMPERATURE = Get-EnvOrDefault "ROUND0_JUDGE_TEMPERATURE" "0.0"

$QWEN_BASE_URL = Get-EnvOrDefault "QWEN_BASE_URL" $CONFIG_QWEN_BASE_URL
$QWEN_API_KEY = Get-EnvOrDefault "QWEN_API_KEY" $CONFIG_QWEN_API_KEY
$QWEN_MODEL = Get-EnvOrDefault "QWEN_MODEL" $CONFIG_QWEN_MODEL
$GPT_JUDGE_MODEL = Get-EnvOrDefault "GPT_JUDGE_MODEL" $CONFIG_GPT_JUDGE_MODEL
$GPT_JUDGE_BASE_URL = Get-EnvOrDefault "GPT_JUDGE_BASE_URL" $CONFIG_GPT_JUDGE_BASE_URL
$GPT_JUDGE_API_KEY = Get-EnvOrDefault "GPT_JUDGE_API_KEY" $CONFIG_GPT_JUDGE_API_KEY
$GPT_MODEL = Get-EnvOrDefault "GPT_MODEL" $CONFIG_GPT_MODEL
$OPENAI_BASE_URL = Get-EnvOrDefault "OPENAI_BASE_URL" $CONFIG_BASE_URL
$PROFILE_MODEL = Get-EnvOrDefault "PROFILE_MODEL" $CONFIG_PROFILE_MODEL
$PROFILE_BASE_URL = Get-EnvOrDefault "PROFILE_BASE_URL" $OPENAI_BASE_URL
$DIFFICULTY_GAIN_MODEL = Get-EnvOrDefault "DIFFICULTY_GAIN_MODEL" $CONFIG_DIFFICULTY_GAIN_MODEL
$DIFFICULTY_GAIN_BASE_URL = Get-EnvOrDefault "DIFFICULTY_GAIN_BASE_URL" $CONFIG_DIFFICULTY_GAIN_BASE_URL
$WEAK_ANSWER_MODEL = Get-EnvOrDefault "WEAK_ANSWER_MODEL" $CONFIG_WEAK_ANSWER_MODEL
$WEAK_ANSWER_BASE_URL = Get-EnvOrDefault "WEAK_ANSWER_BASE_URL" $CONFIG_WEAK_ANSWER_BASE_URL
$WEAK_ANSWER_API_KEY = Get-EnvOrDefault "WEAK_ANSWER_API_KEY" $CONFIG_WEAK_ANSWER_API_KEY
$EVOLVE_MODEL = Get-EnvOrDefault "EVOLVE_MODEL" $CONFIG_EVOLVE_MODEL
$EVOLVE_BASE_URL = Get-EnvOrDefault "EVOLVE_BASE_URL" $OPENAI_BASE_URL
$ANSWER_BASE_URL = Get-EnvOrDefault "ANSWER_BASE_URL" $OPENAI_BASE_URL
$RUBRIC_BASE_URL = Get-EnvOrDefault "RUBRIC_BASE_URL" $OPENAI_BASE_URL
$SCORING_ANSWER_TRIALS = Get-EnvOrDefault "SCORING_ANSWER_TRIALS" "3"
$QWEN_JUDGE_REPEATS = Get-EnvOrDefault "QWEN_JUDGE_REPEATS" "2"
$GPT_JUDGE_REPEATS = Get-EnvOrDefault "GPT_JUDGE_REPEATS" "2"
$SCORING_CONCURRENCY = Get-EnvOrDefault "SCORING_CONCURRENCY" "20"
$QWEN_SCORING_MAX_CONCURRENT = Get-EnvOrDefault "QWEN_SCORING_MAX_CONCURRENT" "20"
$GPT_SCORING_MAX_CONCURRENT = Get-EnvOrDefault "GPT_SCORING_MAX_CONCURRENT" "20"
$PROFILE_CONCURRENCY = Get-EnvOrDefault "PROFILE_CONCURRENCY" "5"
$DIFFICULTY_GAIN_CONCURRENCY = Get-EnvOrDefault "DIFFICULTY_GAIN_CONCURRENCY" $PROFILE_CONCURRENCY
$EVO_CONCURRENCY = Get-EnvOrDefault "EVO_CONCURRENCY" "10"
$ANSWER_CONCURRENCY = Get-EnvOrDefault "ANSWER_CONCURRENCY" "10"
$ANSWER_REQUEST_CONCURRENCY = Get-EnvOrDefault "ANSWER_REQUEST_CONCURRENCY" "20"
$RUBRIC_CONCURRENCY = Get-EnvOrDefault "RUBRIC_CONCURRENCY" "10"

$InputFile = Resolve-ProjectPath $InputFile
if (-not (Test-Path -LiteralPath $InputFile -PathType Leaf)) {
    throw "输入文件不存在: $InputFile。请用 -InputFile 指定有效 JSONL。"
}

$isResume = -not [string]::IsNullOrWhiteSpace($ResumeExperimentDir)
if ($isResume) {
    $EXP_DIR = Resolve-ProjectPath $ResumeExperimentDir
    if (-not (Test-Path -LiteralPath $EXP_DIR -PathType Container)) {
        throw "恢复实验目录不存在: $EXP_DIR"
    }
} else {
    $EXP_ROOT = Resolve-ProjectPath $ExperimentRoot
    $runDate = Get-Date -Format "yyyy-MM-dd"
    $dayDir = Join-Path $EXP_ROOT $runDate
    New-Item -ItemType Directory -Force -Path $dayDir | Out-Null
    $EXP_DIR = Join-Path $dayDir "exp"
    if (Test-Path -LiteralPath $EXP_DIR) {
        $index = 1
        do {
            $EXP_DIR = Join-Path $dayDir "exp$index"
            $index += 1
        } while (Test-Path -LiteralPath $EXP_DIR)
    }
    New-Item -ItemType Directory -Force -Path $EXP_DIR | Out-Null
}

$MEMORY_DIR = Join-Path $EXP_DIR "memory"
New-Item -ItemType Directory -Force -Path $MEMORY_DIR | Out-Null
foreach ($bankFile in @("operator_memory_bank.jsonl", "failure_memory_bank.jsonl", "invalid_generation_cases.jsonl")) {
    $bankPath = Join-Path $MEMORY_DIR $bankFile
    if (-not (Test-Path -LiteralPath $bankPath)) {
        [System.IO.File]::WriteAllText($bankPath, "", [System.Text.UTF8Encoding]::new($false))
    }
}

$difficultyGainAllowBorderlineArgs = @()
if ($DIFFICULTY_GAIN_ALLOW_BORDERLINE -eq "true") { $difficultyGainAllowBorderlineArgs += "--allow-borderline" }
$difficultyGainWeakProbeArgs = @()
if ($DIFFICULTY_GAIN_ENABLE_WEAK_PROBE -eq "true") {
    $difficultyGainWeakProbeArgs += @("--enable-weak-probe", "--weak-probe-mode", $WEAK_PROBE_MODE, "--weak-answer-model", $WEAK_ANSWER_MODEL, "--weak-answer-base-url", $WEAK_ANSWER_BASE_URL)
    if (-not [string]::IsNullOrWhiteSpace($WEAK_ANSWER_API_KEY)) { $difficultyGainWeakProbeArgs += @("--weak-answer-api-key", $WEAK_ANSWER_API_KEY) }
}
$uncertainLowProbeArgs = @()
if ($ENABLE_UNCERTAIN_LOW_PROBE -eq "true") { $uncertainLowProbeArgs += @("--enable-uncertain-low-probe", "--uncertain-low-probe-min-score", $UNCERTAIN_LOW_PROBE_MIN_SCORE) }

Write-Host "本次实验目录: $EXP_DIR"
Write-Host "Memory 目录: $MEMORY_DIR"
if ($isResume) { Write-Host "运行模式: 从已有实验目录恢复" }
$SUMMARY_FILE = Join-Path $EXP_DIR "summary.txt"
if (-not (Test-Path -LiteralPath $SUMMARY_FILE -PathType Leaf)) {
    [System.IO.File]::WriteAllText($SUMMARY_FILE, "Question Evolution Loop Summary`n================================`n", [System.Text.UTF8Encoding]::new($false))
    foreach ($line in @(
        "Input file: $InputFile", "Memory dir: $MEMORY_DIR", "Max rounds: $MaxRounds", "Early stop rate: $EARLY_STOP_RATE",
        "No-info stop rounds: $NO_INFO_STOP_ROUNDS", "No-info min delta: $NO_INFO_MIN_DELTA", "Evolution trigger rate: $MIN_SCORE_RATE",
        "Num candidates: $NUM_CANDIDATES", "Max candidate budget: $MAX_CANDIDATE_BUDGET", "Validation retries: $VALIDATION_RETRIES",
        "Scoring answer trials: $SCORING_ANSWER_TRIALS", "Qwen judge repeats: $QWEN_JUDGE_REPEATS", "GPT judge repeats: $GPT_JUDGE_REPEATS",
        "Scoring worker concurrency: $SCORING_CONCURRENCY", "Qwen scoring max concurrent: $QWEN_SCORING_MAX_CONCURRENT", "GPT scoring max concurrent: $GPT_SCORING_MAX_CONCURRENT",
        "Answer worker concurrency: $ANSWER_CONCURRENCY", "Answer request concurrency: $ANSWER_REQUEST_CONCURRENCY",
        "", "Round | Avg Score Rate | Status", "------|----------------|--------"
    )) { Add-SummaryLine $SUMMARY_FILE $line }
}

$round = 0
$roundDir = Join-Path $EXP_DIR "round_$round"
New-Item -ItemType Directory -Force -Path $roundDir | Out-Null
$roundInput = Join-Path $roundDir "input.jsonl"
$roundScored = Join-Path $roundDir "scored.jsonl"
Invoke-Step $roundInput "prepare_round_input" $InputFile "[Round 0] Step 0/2: 准备 baseline input" { Prepare-RoundInput $InputFile $roundInput 0 (Join-Path $roundDir "performance_events.jsonl") }
Invoke-Step $roundScored "round0_stability_probe" $roundInput "[Round 0] Step 1/2: round0_stability_probe.py baseline" {
    Invoke-Python "round0_stability_probe.py" "--input" $roundInput "--output" $roundScored "--answer-mode" "llm" "--answer-base-url" $QWEN_BASE_URL "--answer-api-key" $QWEN_API_KEY "--answer-model" $QWEN_MODEL "--judge-base-url" $QWEN_BASE_URL "--judge-api-key" $QWEN_API_KEY "--judge-model" $QWEN_MODEL "--qwen-judge-repeats" $QWEN_JUDGE_REPEATS "--gpt-judge-base-url" $GPT_JUDGE_BASE_URL "--gpt-judge-api-key" $GPT_JUDGE_API_KEY "--gpt-judge-model" $GPT_JUDGE_MODEL "--gpt-judge-repeats" $GPT_JUDGE_REPEATS "--qwen-max-concurrent" $QWEN_SCORING_MAX_CONCURRENT "--gpt-max-concurrent" $GPT_SCORING_MAX_CONCURRENT "--max-concurrent" $SCORING_CONCURRENCY "--initial-trials" $ROUND0_INITIAL_TRIALS "--extra-trials" $ROUND0_EXTRA_TRIALS "--max-trials" $ROUND0_MAX_TRIALS "--answer-temperature" $ROUND0_ANSWER_TEMPERATURE "--answer-top-p" $ROUND0_ANSWER_TOP_P "--answer-seed-base" $ROUND0_ANSWER_SEED_BASE "--judge-temperature" $ROUND0_JUDGE_TEMPERATURE "--score-threshold" $MIN_SCORE_RATE "--strong-high-threshold" $ROUND0_STRONG_HIGH_RATE "--edge-low" $ROUND0_EDGE_LOW "--edge-high" $ROUND0_EDGE_HIGH "--cache-dir" (Join-Path $roundDir "round0_cache") "--report-output" (Join-Path $roundDir "round0_stability_report.json") "--performance-events" (Join-Path $roundDir "performance_events.jsonl")
}

$previousScored = $roundScored
$previousAverage = [double](Get-AverageScoreRate $roundScored)
Write-Host "Round 0 平均得分率: $($previousAverage.ToString('F4'))"
Add-SummaryRoundIfMissing $SUMMARY_FILE 0 $previousAverage "baseline"
$noInfoStreak = 0

for ($round = 1; $round -le $MaxRounds; $round += 1) {
    $roundDir = Join-Path $EXP_DIR "round_$round"
    New-Item -ItemType Directory -Force -Path $roundDir | Out-Null
    Write-Host "`n========================================"
    Write-Host "Round ${round}: Question Evolution"
    Write-Host "========================================"

    $roundInput = Join-Path $roundDir "input.jsonl"
    $roundScored = Join-Path $roundDir "scored.jsonl"
    Invoke-Step $roundInput "prepare_round_input" $previousScored "[Round $round] Step 0/13: 准备上一轮 scored/state 输入" { Prepare-RoundInput $previousScored $roundInput $round (Join-Path $roundDir "performance_events.jsonl") }

    if (-not (Test-PublishedArtifact $roundScored "scoring" (Join-Path $roundDir "rubric.jsonl"))) {
        if (Test-Path -LiteralPath $roundScored) {
            throw "已有 scored 产物未通过 manifest 校验，拒绝继续: $roundScored"
        }
        $profiled = Join-Path $roundDir "profiled.jsonl"
        $profiledCandidates = Join-Path $roundDir "profiled_candidates.jsonl"
        $routed = Join-Path $roundDir "routed.jsonl"
        $candidates = Join-Path $roundDir "candidates.jsonl"
        $validatedCandidates = Join-Path $roundDir "validated_candidates.jsonl"
        $lightFactualCandidates = Join-Path $roundDir "light_factual_checked_candidates.jsonl"
        $difficultyValidatedCandidates = Join-Path $roundDir "difficulty_validated_candidates.jsonl"
        $evolved = Join-Path $roundDir "evolved.jsonl"
        $withAnswers = Join-Path $roundDir "with_answers.jsonl"
        $rubric = Join-Path $roundDir "rubric.jsonl"

        Invoke-Step $profiled "profile_samples" $roundInput "[Round $round] Step 1/13: profile_samples.py" { Invoke-Python "profile_samples.py" "--input" $roundInput "--output" $profiled "--model" $PROFILE_MODEL "--base-url" $PROFILE_BASE_URL "--concurrency" $PROFILE_CONCURRENCY "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
        Invoke-Step $profiledCandidates "select_evolution_candidates" $profiled "[Round $round] Step 2/13: select_evolution_candidates.py" { Invoke-Python "select_evolution_candidates.py" "--input" $profiled "--output" $profiledCandidates "--high-score-threshold" $MIN_SCORE_RATE "--report-output" (Join-Path $roundDir "evolution_candidate_report.json") "--performance-events" (Join-Path $roundDir "performance_events.jsonl") @uncertainLowProbeArgs }
        Invoke-Step $routed "operator_router" $profiledCandidates "[Round $round] Step 3/13: operator_router.py" { Invoke-Python "operator_router.py" "--input" $profiledCandidates "--output" $routed "--memory-dir" $MEMORY_DIR "--failure-memory-window-rounds" $FAILURE_MEMORY_WINDOW_ROUNDS "--report-output" (Join-Path $roundDir "operator_router_report.json") "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
        Invoke-Step $candidates "question_evolution" $routed "[Round $round] Step 4/13: question_evolution.py" { Invoke-Python "question_evolution.py" "--input" $routed "--output" $candidates "--min-score-rate" $MIN_SCORE_RATE "--model" $EVOLVE_MODEL "--base-url" $EVOLVE_BASE_URL "--concurrency" $EVO_CONCURRENCY "--num-candidates" $NUM_CANDIDATES "--max-candidate-budget" $MAX_CANDIDATE_BUDGET "--validation-retries" $VALIDATION_RETRIES "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
        Assert-CandidateCoverage $routed $candidates
        Invoke-Step $validatedCandidates "validate_evolved_question" $candidates "[Round $round] Step 5/13: validate_evolved_question.py" { Invoke-Python "validate_evolved_question.py" "--input" $candidates "--output" $validatedCandidates "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
        Invoke-Step $lightFactualCandidates "light_factual_check" $validatedCandidates "[Round $round] Step 6/13: light_factual_check.py" { Invoke-Python "light_factual_check.py" "--input" $validatedCandidates "--output" $lightFactualCandidates "--report-output" (Join-Path $roundDir "light_factual_report.json") "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
        Invoke-Step $difficultyValidatedCandidates "validate_difficulty_gain" $lightFactualCandidates "[Round $round] Step 7/13: validate_difficulty_gain.py" { Invoke-Python "validate_difficulty_gain.py" "--input" $lightFactualCandidates "--output" $difficultyValidatedCandidates "--report-output" (Join-Path $roundDir "difficulty_gain_report.json") "--model" $DIFFICULTY_GAIN_MODEL "--base-url" $DIFFICULTY_GAIN_BASE_URL "--concurrency" $DIFFICULTY_GAIN_CONCURRENCY "--min-gain-score" $MIN_DIFFICULTY_GAIN_SCORE "--borderline-gain-score" $BORDERLINE_DIFFICULTY_GAIN_SCORE "--min-competitive-judgment-score" $MIN_COMPETITIVE_JUDGMENT_SCORE "--performance-events" (Join-Path $roundDir "performance_events.jsonl") @difficultyGainAllowBorderlineArgs @difficultyGainWeakProbeArgs }
        Invoke-Step $evolved "candidate_selection" $difficultyValidatedCandidates "[Round $round] Step 8/13: candidate_selection.py" { Invoke-Python "candidate_selection.py" "--input" $difficultyValidatedCandidates "--output" $evolved "--invalid-output" (Join-Path $roundDir "invalid_generation_cases.jsonl") "--report-output" (Join-Path $roundDir "candidate_selection_report.json") "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
        Invoke-Step $withAnswers "collect_answers" $evolved "[Round $round] Step 9/13: collect_answers.py" { Invoke-Python "collect_answers.py" "--input" $evolved "--output" $withAnswers "--concurrency" $ANSWER_CONCURRENCY "--request-concurrency" $ANSWER_REQUEST_CONCURRENCY "--samples" "1" "--model" $GPT_MODEL "--base-url" $ANSWER_BASE_URL "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
        Invoke-Step $rubric "gen_rubric" $withAnswers "[Round $round] Step 10/13: gen_rubric.py" { Invoke-Python "gen_rubric.py" "--input" $withAnswers "--output" $rubric "--concurrency" $RUBRIC_CONCURRENCY "--model" $GPT_MODEL "--base-url" $RUBRIC_BASE_URL "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
        Invoke-Step $roundScored "scoring" $rubric "[Round $round] Step 11/13: scoring.py" { Invoke-Python "scoring.py" "--input" $rubric "--output" $roundScored "--answer-mode" "llm" "--answer-base-url" $QWEN_BASE_URL "--answer-api-key" $QWEN_API_KEY "--answer-model" $QWEN_MODEL "--judge-base-url" $QWEN_BASE_URL "--judge-api-key" $QWEN_API_KEY "--judge-model" $QWEN_MODEL "--answer-trials" $SCORING_ANSWER_TRIALS "--qwen-judge-repeats" $QWEN_JUDGE_REPEATS "--gpt-judge-base-url" $GPT_JUDGE_BASE_URL "--gpt-judge-api-key" $GPT_JUDGE_API_KEY "--gpt-judge-model" $GPT_JUDGE_MODEL "--gpt-judge-repeats" $GPT_JUDGE_REPEATS "--qwen-max-concurrent" $QWEN_SCORING_MAX_CONCURRENT "--gpt-max-concurrent" $GPT_SCORING_MAX_CONCURRENT "--concurrency" $SCORING_CONCURRENCY "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
    }
    else {
        Write-Host "检测到已存在 $roundScored，跳过本轮生成闭环"
    }

    $effectAnalysis = Join-Path $roundDir "effect_analysis.jsonl"
    $stateUpdated = Join-Path $roundDir "state_updated.jsonl"
    Invoke-Step $effectAnalysis "analyze_evolution_effect" $roundScored "[Round $round] Step 12/13: analyze_evolution_effect.py" { Invoke-Python "analyze_evolution_effect.py" "--before" $previousScored "--input" $roundScored "--output" $effectAnalysis "--matrix-output" (Join-Path $roundDir "effect_matrix.jsonl") "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }
    Invoke-Step $stateUpdated "update_sample_state" $effectAnalysis "[Round $round] Step 13/13: update_sample_state.py" { Invoke-Python "update_sample_state.py" "--input" $effectAnalysis "--output" $stateUpdated "--memory-dir" $MEMORY_DIR "--preselection-invalid-input" (Join-Path $roundDir "invalid_generation_cases.jsonl") "--report-output" (Join-Path $roundDir "state_update_report.json") "--performance-events" (Join-Path $roundDir "performance_events.jsonl") }

    $roundOutputForNext = if (Test-PublishedArtifact $stateUpdated "update_sample_state" $effectAnalysis) { $stateUpdated } else { $roundScored }
    $averageRate = [double](Get-AverageScoreRate $roundOutputForNext)
    $effectCount = Get-EffectiveBoundaryCount $effectAnalysis
    $continueCount = Get-ContinueCount $roundOutputForNext
    $averageDelta = [Math]::Abs($averageRate - $previousAverage)
    Write-Host "Round $round 回滚后有效平均得分率: $($averageRate.ToString('F4'))"

    if ($averageRate -lt $EARLY_STOP_RATE) {
        Write-Host "提前停止：Round $round 平均得分率 $averageRate < $EARLY_STOP_RATE"
        Add-SummaryRoundIfMissing $SUMMARY_FILE $round $averageRate "early_stop"
        $previousScored = $roundOutputForNext
        break
    }

    if ($effectCount -eq 0 -and $continueCount -eq 0 -and $averageDelta -lt $NO_INFO_MIN_DELTA) { $noInfoStreak += 1 } else { $noInfoStreak = 0 }
    if ($noInfoStreak -ge $NO_INFO_STOP_ROUNDS) {
        Write-Host "提前停止：连续 $noInfoStreak 轮无新信息。"
        Add-SummaryRoundIfMissing $SUMMARY_FILE $round $averageRate "no_info_stop"
        $previousScored = $roundOutputForNext
        break
    }

    Add-SummaryRoundIfMissing $SUMMARY_FILE $round $averageRate "continue"
    $previousScored = $roundOutputForNext
    $previousAverage = $averageRate
}

$finalDir = Join-Path $EXP_DIR "final"
New-Item -ItemType Directory -Force -Path $finalDir | Out-Null
$finalOutput = Join-Path $finalDir "final_scored.jsonl"
Invoke-Python "artifact_cli.py" "copy-published" "--input" $previousScored "--output" $finalOutput
Write-Host "`n========================================"
Write-Host "循环结束"
Write-Host "最终结果: $finalOutput"
Write-Host "各轮汇总: $SUMMARY_FILE"
Write-Host "========================================"
Get-Content -LiteralPath $SUMMARY_FILE -Encoding UTF8
