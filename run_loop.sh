#!/bin/bash
# Question Evolution 循环流水线
# 每轮把上一轮 scored/state 结果接入画像、分流、路由、多候选进化、复杂度校验、
# 标准采答案/rubric/评分闭环、效果统计和状态更新。
# 支持断点续跑：每一步的目标文件已存在且非空时跳过该步，避免重复写 memory。

set -euo pipefail

# ===================== 可配置参数 =====================
read_config_value() {
    python -c "import sys; from local_api_config import get_config_value; print(get_config_value(*sys.argv[1:-1], default=sys.argv[-1]))" "$@"
}

CONFIG_BASE_URL=$(read_config_value BASE_URL OPENAI_BASE_URL "")
CONFIG_GPT_MODEL=$(read_config_value GPT_MODEL QA_MODEL "gpt-5.4")
CONFIG_QWEN_BASE_URL=$(read_config_value QWEN_BASE_URL "$CONFIG_BASE_URL")
CONFIG_QWEN_API_KEY=$(read_config_value QWEN_API_KEY "")
CONFIG_QWEN_MODEL=$(read_config_value QWEN_MODEL GPT_MODEL "hjl_Qwen3.6-27B")
CONFIG_PROFILE_MODEL=$(read_config_value PROFILE_MODEL EVOLVE_MODEL QA_MODEL GPT_MODEL "$CONFIG_GPT_MODEL")
CONFIG_DIFFICULTY_GAIN_MODEL=$(read_config_value DIFFICULTY_GAIN_MODEL PROFILE_MODEL EVOLVE_MODEL QA_MODEL GPT_MODEL "$CONFIG_PROFILE_MODEL")
CONFIG_DIFFICULTY_GAIN_BASE_URL=$(read_config_value DIFFICULTY_GAIN_BASE_URL PROFILE_BASE_URL EVOLVE_BASE_URL BASE_URL OPENAI_BASE_URL "$CONFIG_BASE_URL")
CONFIG_WEAK_ANSWER_MODEL=$(read_config_value WEAK_ANSWER_MODEL QWEN_MODEL GPT_MODEL "$CONFIG_QWEN_MODEL")
CONFIG_WEAK_ANSWER_BASE_URL=$(read_config_value WEAK_ANSWER_BASE_URL QWEN_BASE_URL BASE_URL OPENAI_BASE_URL "$CONFIG_QWEN_BASE_URL")
CONFIG_WEAK_ANSWER_API_KEY=$(read_config_value WEAK_ANSWER_API_KEY QWEN_API_KEY "")
CONFIG_EVOLVE_MODEL=$(read_config_value EVOLVE_MODEL QA_MODEL GPT_MODEL "$CONFIG_GPT_MODEL")
CONFIG_ANSWER_MODEL=$(read_config_value ANSWER_MODEL QA_MODEL GPT_MODEL "$CONFIG_GPT_MODEL")
CONFIG_RUBRIC_MODEL=$(read_config_value RUBRIC_MODEL QA_MODEL GPT_MODEL "$CONFIG_GPT_MODEL")

MAX_ROUNDS=${MAX_ROUNDS:-5}                      # 最大迭代轮数
EARLY_STOP_RATE=${EARLY_STOP_RATE:-0.5}          # 平均得分率低于该值时停止
NO_INFO_STOP_ROUNDS=${NO_INFO_STOP_ROUNDS:-2}    # 连续多少轮无新信息时停止
NO_INFO_MIN_DELTA=${NO_INFO_MIN_DELTA:-0.0001}   # 平均分变化小于该值视为无新信息
MIN_SCORE_RATE=${MIN_SCORE_RATE:-0.8}            # legacy question_evolution 触发阈值
NUM_CANDIDATES=${NUM_CANDIDATES:-2}              # 每条待进化样本最多生成候选数，范围 1-4
MAX_CANDIDATE_BUDGET=${MAX_CANDIDATE_BUDGET:-0}  # 单轮候选总预算；0 表示待进化样本数 * 2
VALIDATION_RETRIES=${VALIDATION_RETRIES:-1}      # validate-retry 次数；当前最多 1 次
MIN_DIFFICULTY_GAIN_SCORE=${MIN_DIFFICULTY_GAIN_SCORE:-0.75}
BORDERLINE_DIFFICULTY_GAIN_SCORE=${BORDERLINE_DIFFICULTY_GAIN_SCORE:-0.65}
MIN_COMPETITIVE_JUDGMENT_SCORE=${MIN_COMPETITIVE_JUDGMENT_SCORE:-0.60}
DIFFICULTY_GAIN_ALLOW_BORDERLINE=${DIFFICULTY_GAIN_ALLOW_BORDERLINE:-false}
DIFFICULTY_GAIN_ENABLE_WEAK_PROBE=${DIFFICULTY_GAIN_ENABLE_WEAK_PROBE:-false}
WEAK_PROBE_MODE=${WEAK_PROBE_MODE:-light}
ENABLE_UNCERTAIN_LOW_PROBE=${ENABLE_UNCERTAIN_LOW_PROBE:-false}
UNCERTAIN_LOW_PROBE_MIN_SCORE=${UNCERTAIN_LOW_PROBE_MIN_SCORE:-0.55}
FAILURE_MEMORY_WINDOW_ROUNDS=${FAILURE_MEMORY_WINDOW_ROUNDS:-3}
ROUND0_INITIAL_TRIALS=${ROUND0_INITIAL_TRIALS:-3}
ROUND0_EXTRA_TRIALS=${ROUND0_EXTRA_TRIALS:-2}
ROUND0_MAX_TRIALS=${ROUND0_MAX_TRIALS:-5}
ROUND0_EDGE_LOW=${ROUND0_EDGE_LOW:-0.72}
ROUND0_EDGE_HIGH=${ROUND0_EDGE_HIGH:-0.83}
ROUND0_STRONG_HIGH_RATE=${ROUND0_STRONG_HIGH_RATE:-0.85}
ROUND0_ANSWER_TEMPERATURE=${ROUND0_ANSWER_TEMPERATURE:-0.7}
ROUND0_ANSWER_TOP_P=${ROUND0_ANSWER_TOP_P:-0.95}
ROUND0_ANSWER_SEED_BASE=${ROUND0_ANSWER_SEED_BASE:-20260704}
ROUND0_JUDGE_TEMPERATURE=${ROUND0_JUDGE_TEMPERATURE:-0.0}

DEFAULT_INPUT_FILE="admitted_seed_samples.jsonl"
LEGACY_INPUT_FILE="data/data.jsonl"
INPUT_FILE=${INPUT_FILE:-$DEFAULT_INPUT_FILE}    # 推荐输入：已完成准入的种子样本
EXP_ROOT=${EXP_ROOT:-"experiments"}              # 实验结果根目录

# Qwen（候选模型 / 评分模型）配置
QWEN_BASE_URL=${QWEN_BASE_URL:-$CONFIG_QWEN_BASE_URL}
QWEN_API_KEY=${QWEN_API_KEY:-$CONFIG_QWEN_API_KEY}
QWEN_MODEL=${QWEN_MODEL:-$CONFIG_QWEN_MODEL}

# GPT / OpenAI-compatible 配置。API key 优先使用各脚本支持的环境变量：
# PROFILE_API_KEYS、EVOLVE_API_KEYS、OPENAI_API_KEYS 或 OPENAI_API_KEY。
GPT_MODEL=${GPT_MODEL:-$CONFIG_GPT_MODEL}
OPENAI_BASE_URL=${OPENAI_BASE_URL:-$CONFIG_BASE_URL}
PROFILE_MODEL=${PROFILE_MODEL:-$CONFIG_PROFILE_MODEL}
PROFILE_BASE_URL=${PROFILE_BASE_URL:-$OPENAI_BASE_URL}
DIFFICULTY_GAIN_MODEL=${DIFFICULTY_GAIN_MODEL:-$CONFIG_DIFFICULTY_GAIN_MODEL}
DIFFICULTY_GAIN_BASE_URL=${DIFFICULTY_GAIN_BASE_URL:-$CONFIG_DIFFICULTY_GAIN_BASE_URL}
WEAK_ANSWER_MODEL=${WEAK_ANSWER_MODEL:-$CONFIG_WEAK_ANSWER_MODEL}
WEAK_ANSWER_BASE_URL=${WEAK_ANSWER_BASE_URL:-$CONFIG_WEAK_ANSWER_BASE_URL}
WEAK_ANSWER_API_KEY=${WEAK_ANSWER_API_KEY:-$CONFIG_WEAK_ANSWER_API_KEY}
EVOLVE_MODEL=${EVOLVE_MODEL:-$CONFIG_EVOLVE_MODEL}
EVOLVE_BASE_URL=${EVOLVE_BASE_URL:-$OPENAI_BASE_URL}
ANSWER_BASE_URL=${ANSWER_BASE_URL:-$OPENAI_BASE_URL}
RUBRIC_BASE_URL=${RUBRIC_BASE_URL:-$OPENAI_BASE_URL}

# 并发数
SCORING_CONCURRENCY=${SCORING_CONCURRENCY:-10}
PROFILE_CONCURRENCY=${PROFILE_CONCURRENCY:-5}
DIFFICULTY_GAIN_CONCURRENCY=${DIFFICULTY_GAIN_CONCURRENCY:-$PROFILE_CONCURRENCY}
EVO_CONCURRENCY=${EVO_CONCURRENCY:-10}
ANSWER_CONCURRENCY=${ANSWER_CONCURRENCY:-10}
RUBRIC_CONCURRENCY=${RUBRIC_CONCURRENCY:-10}
# ======================================================

if [ ! -f "$INPUT_FILE" ] && [ "$INPUT_FILE" = "$DEFAULT_INPUT_FILE" ] && [ -f "$LEGACY_INPUT_FILE" ]; then
    echo "未找到 $DEFAULT_INPUT_FILE，回退到旧输入文件: $LEGACY_INPUT_FILE"
    INPUT_FILE="$LEGACY_INPUT_FILE"
fi

if [ ! -f "$INPUT_FILE" ]; then
    echo "输入文件不存在: $INPUT_FILE"
    echo "请设置 INPUT_FILE 指向 admitted_seed_samples.jsonl 或其他已准入 JSONL。"
    exit 1
fi

DIFFICULTY_GAIN_ALLOW_BORDERLINE_ARGS=()
if [ "$DIFFICULTY_GAIN_ALLOW_BORDERLINE" = "true" ]; then
    DIFFICULTY_GAIN_ALLOW_BORDERLINE_ARGS=(--allow-borderline)
fi

DIFFICULTY_GAIN_WEAK_PROBE_ARGS=()
if [ "$DIFFICULTY_GAIN_ENABLE_WEAK_PROBE" = "true" ]; then
    DIFFICULTY_GAIN_WEAK_PROBE_ARGS=(
        --enable-weak-probe
        --weak-probe-mode "$WEAK_PROBE_MODE"
        --weak-answer-model "$WEAK_ANSWER_MODEL"
        --weak-answer-base-url "$WEAK_ANSWER_BASE_URL"
    )
    if [ -n "$WEAK_ANSWER_API_KEY" ]; then
        DIFFICULTY_GAIN_WEAK_PROBE_ARGS+=(--weak-answer-api-key "$WEAK_ANSWER_API_KEY")
    fi
fi

UNCERTAIN_LOW_PROBE_ARGS=()
if [ "$ENABLE_UNCERTAIN_LOW_PROBE" = "true" ]; then
    UNCERTAIN_LOW_PROBE_ARGS=(
        --enable-uncertain-low-probe
        --uncertain-low-probe-min-score "$UNCERTAIN_LOW_PROBE_MIN_SCORE"
    )
fi

# 为当天运行自动选择实验目录：
#   experiments/YYYY-MM-DD/exp
#   experiments/YYYY-MM-DD/exp1
#   experiments/YYYY-MM-DD/exp2
#   ...
RUN_DATE=$(date +%F)
DAY_DIR="$EXP_ROOT/$RUN_DATE"
mkdir -p "$DAY_DIR"

EXP_DIR="$DAY_DIR/exp"
if [ -e "$EXP_DIR" ]; then
    EXP_INDEX=1
    while [ -e "$DAY_DIR/exp$EXP_INDEX" ]; do
        EXP_INDEX=$((EXP_INDEX + 1))
    done
    EXP_DIR="$DAY_DIR/exp$EXP_INDEX"
fi
mkdir -p "$EXP_DIR"

MEMORY_DIR="$EXP_DIR/memory"
mkdir -p "$MEMORY_DIR"
for bank_file in operator_memory_bank.jsonl failure_memory_bank.jsonl invalid_generation_cases.jsonl; do
    if [ ! -f "$MEMORY_DIR/$bank_file" ]; then
        : > "$MEMORY_DIR/$bank_file"
    fi
done

echo "本次实验目录: $EXP_DIR"
echo "Memory 目录: $MEMORY_DIR"

run_if_missing() {
    local output_file="$1"
    local step_label="$2"
    shift 2

    if [ -f "$output_file" ] && [ -s "$output_file" ]; then
        echo "检测到已存在 $output_file，跳过 $step_label"
    else
        echo "$step_label"
        "$@"
    fi
}

# 辅助函数：计算 jsonl 的平均得分率
compute_avg_score_rate() {
    local scored_file="$1"
    python -c "import json, sys; rates=[]; f=open(sys.argv[1], encoding='utf-8'); \
[rates.append((item.get('scoring_result',{}).get('total_awarded',0) or 0)/(item.get('scoring_result',{}).get('total_possible',0) or 1)) for item in (json.loads(line) for line in f if line.strip()) if (item.get('scoring_result',{}).get('total_possible',0) or 0) > 0]; \
print(f'{(sum(rates)/len(rates) if rates else 0.0):.4f}')" "$scored_file"
}

# 辅助函数：比较两个浮点数，输出 true/false
lt_float() {
    python -c "print('true' if float('$1') < float('$2') else 'false')"
}

abs_diff_float() {
    python -c "print(abs(float('$1') - float('$2')))"
}

extract_effect_count() {
    local analyzed_file="$1"
    python -c "import json, sys; count=0; f=open(sys.argv[1], encoding='utf-8'); \
[globals().__setitem__('count', count + 1) for item in (json.loads(line) for line in f if line.strip()) if isinstance(item.get('effect_analysis', {}), dict) and item.get('effect_analysis', {}).get('effect_label') == 'effective_boundary_probe']; \
print(count)" "$analyzed_file"
}

validate_candidate_coverage() {
    local input_file="$1"
    local candidates_file="$2"
    python -c '
import json
import sys

def load(path):
    content = open(path, encoding="utf-8").read().strip()
    if not content:
        return []
    if content.startswith("["):
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError(f"{path} must be a JSON array or JSONL")
        return data
    return [json.loads(line) for line in content.splitlines() if line.strip()]

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
    print(
        "candidate coverage check failed; missing groups: " + ", ".join(missing[:20]),
        file=sys.stderr,
    )
    sys.exit(1)
' "$input_file" "$candidates_file"
}

SUMMARY_FILE="$EXP_DIR/summary.txt"
echo "Question Evolution Loop Summary" > "$SUMMARY_FILE"
echo "================================" >> "$SUMMARY_FILE"
echo "Input file: $INPUT_FILE" >> "$SUMMARY_FILE"
echo "Memory dir: $MEMORY_DIR" >> "$SUMMARY_FILE"
echo "Max rounds: $MAX_ROUNDS" >> "$SUMMARY_FILE"
echo "Early stop rate: $EARLY_STOP_RATE" >> "$SUMMARY_FILE"
echo "No-info stop rounds: $NO_INFO_STOP_ROUNDS" >> "$SUMMARY_FILE"
echo "No-info min delta: $NO_INFO_MIN_DELTA" >> "$SUMMARY_FILE"
echo "Evolution trigger rate: $MIN_SCORE_RATE" >> "$SUMMARY_FILE"
echo "Num candidates: $NUM_CANDIDATES" >> "$SUMMARY_FILE"
echo "Max candidate budget: $MAX_CANDIDATE_BUDGET" >> "$SUMMARY_FILE"
echo "Validation retries: $VALIDATION_RETRIES" >> "$SUMMARY_FILE"
echo "Min difficulty gain score: $MIN_DIFFICULTY_GAIN_SCORE" >> "$SUMMARY_FILE"
echo "Borderline difficulty gain score: $BORDERLINE_DIFFICULTY_GAIN_SCORE" >> "$SUMMARY_FILE"
echo "Min competitive judgment score: $MIN_COMPETITIVE_JUDGMENT_SCORE" >> "$SUMMARY_FILE"
echo "Difficulty gain allow borderline: $DIFFICULTY_GAIN_ALLOW_BORDERLINE" >> "$SUMMARY_FILE"
echo "Difficulty gain weak probe: $DIFFICULTY_GAIN_ENABLE_WEAK_PROBE" >> "$SUMMARY_FILE"
echo "Uncertain low probe: $ENABLE_UNCERTAIN_LOW_PROBE" >> "$SUMMARY_FILE"
echo "Failure memory window rounds: $FAILURE_MEMORY_WINDOW_ROUNDS" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"
echo "Round | Avg Score Rate | Status" >> "$SUMMARY_FILE"
echo "------|----------------|--------" >> "$SUMMARY_FILE"

# ===================== Round 0: 初始评分 =====================
ROUND=0
ROUND_DIR="$EXP_DIR/round_$ROUND"
mkdir -p "$ROUND_DIR"

echo ""
echo "========================================"
echo "Round $ROUND: 初始评分（baseline）"
echo "========================================"

run_if_missing "$ROUND_DIR/input.jsonl" "[Round $ROUND] Step 0/2: 准备 baseline input" \
    cp "$INPUT_FILE" "$ROUND_DIR/input.jsonl"

run_if_missing "$ROUND_DIR/scored.jsonl" "[Round $ROUND] Step 1/2: round0_stability_probe.py baseline" \
    python round0_stability_probe.py \
        --input "$ROUND_DIR/input.jsonl" \
        --output "$ROUND_DIR/scored.jsonl" \
        --answer-mode llm \
        --answer-base-url "$QWEN_BASE_URL" \
        --answer-api-key "$QWEN_API_KEY" \
        --answer-model "$QWEN_MODEL" \
        --judge-base-url "$QWEN_BASE_URL" \
        --judge-api-key "$QWEN_API_KEY" \
        --judge-model "$QWEN_MODEL" \
        --max-concurrent "$SCORING_CONCURRENCY" \
        --initial-trials "$ROUND0_INITIAL_TRIALS" \
        --extra-trials "$ROUND0_EXTRA_TRIALS" \
        --max-trials "$ROUND0_MAX_TRIALS" \
        --answer-temperature "$ROUND0_ANSWER_TEMPERATURE" \
        --answer-top-p "$ROUND0_ANSWER_TOP_P" \
        --answer-seed-base "$ROUND0_ANSWER_SEED_BASE" \
        --judge-temperature "$ROUND0_JUDGE_TEMPERATURE" \
        --score-threshold "$MIN_SCORE_RATE" \
        --strong-high-threshold "$ROUND0_STRONG_HIGH_RATE" \
        --edge-low "$ROUND0_EDGE_LOW" \
        --edge-high "$ROUND0_EDGE_HIGH" \
        --cache-dir "$ROUND_DIR/round0_cache" \
        --report-output "$ROUND_DIR/round0_stability_report.json"

AVG_RATE=$(compute_avg_score_rate "$ROUND_DIR/scored.jsonl")
echo "Round $ROUND 平均得分率: $AVG_RATE"
printf "%5s | %14s | %s\n" "$ROUND" "$AVG_RATE" "baseline" >> "$SUMMARY_FILE"

PREV_SCORED="$ROUND_DIR/scored.jsonl"
PREV_AVG_RATE="$AVG_RATE"
PREV_EFFECT_COUNT=0
NO_INFO_STREAK=0

# ===================== Round 1..N: 循环进化 =====================
for ROUND in $(seq 1 "$MAX_ROUNDS"); do
    ROUND_DIR="$EXP_DIR/round_$ROUND"
    mkdir -p "$ROUND_DIR"

    echo ""
    echo "========================================"
    echo "Round $ROUND: Question Evolution"
    echo "========================================"

    run_if_missing "$ROUND_DIR/input.jsonl" "[Round $ROUND] Step 0/13: 复制上一轮 scored/state 输入" \
        cp "$PREV_SCORED" "$ROUND_DIR/input.jsonl"

    if [ -f "$ROUND_DIR/scored.jsonl" ] && [ -s "$ROUND_DIR/scored.jsonl" ]; then
        echo "检测到已存在 $ROUND_DIR/scored.jsonl，跳过本轮生成闭环"
    else
        run_if_missing "$ROUND_DIR/profiled.jsonl" "[Round $ROUND] Step 1/13: profile_samples.py" \
            python profile_samples.py \
                --input "$ROUND_DIR/input.jsonl" \
                --output "$ROUND_DIR/profiled.jsonl" \
                --model "$PROFILE_MODEL" \
                --base-url "$PROFILE_BASE_URL" \
                --concurrency "$PROFILE_CONCURRENCY"

        run_if_missing "$ROUND_DIR/profiled_candidates.jsonl" "[Round $ROUND] Step 2/13: select_evolution_candidates.py" \
            python select_evolution_candidates.py \
                --input "$ROUND_DIR/profiled.jsonl" \
                --output "$ROUND_DIR/profiled_candidates.jsonl" \
                --high-score-threshold "$MIN_SCORE_RATE" \
                --report-output "$ROUND_DIR/evolution_candidate_report.json" \
                "${UNCERTAIN_LOW_PROBE_ARGS[@]}"

        run_if_missing "$ROUND_DIR/routed.jsonl" "[Round $ROUND] Step 3/13: operator_router.py" \
            python operator_router.py \
                --input "$ROUND_DIR/profiled_candidates.jsonl" \
                --output "$ROUND_DIR/routed.jsonl" \
                --memory-dir "$MEMORY_DIR" \
                --failure-memory-window-rounds "$FAILURE_MEMORY_WINDOW_ROUNDS" \
                --report-output "$ROUND_DIR/operator_router_report.json"

        run_if_missing "$ROUND_DIR/candidates.jsonl" "[Round $ROUND] Step 4/13: question_evolution.py" \
            python question_evolution.py \
                --input "$ROUND_DIR/routed.jsonl" \
                --output "$ROUND_DIR/candidates.jsonl" \
                --min-score-rate "$MIN_SCORE_RATE" \
                --model "$EVOLVE_MODEL" \
                --base-url "$EVOLVE_BASE_URL" \
                --concurrency "$EVO_CONCURRENCY" \
                --num-candidates "$NUM_CANDIDATES" \
                --max-candidate-budget "$MAX_CANDIDATE_BUDGET" \
                --validation-retries "$VALIDATION_RETRIES"
        validate_candidate_coverage "$ROUND_DIR/routed.jsonl" "$ROUND_DIR/candidates.jsonl"

        run_if_missing "$ROUND_DIR/validated_candidates.jsonl" "[Round $ROUND] Step 5/13: validate_evolved_question.py" \
            python validate_evolved_question.py \
                --input "$ROUND_DIR/candidates.jsonl" \
                --output "$ROUND_DIR/validated_candidates.jsonl"

        run_if_missing "$ROUND_DIR/light_factual_checked_candidates.jsonl" "[Round $ROUND] Step 6/13: light_factual_check.py" \
            python light_factual_check.py \
                --input "$ROUND_DIR/validated_candidates.jsonl" \
                --output "$ROUND_DIR/light_factual_checked_candidates.jsonl" \
                --report-output "$ROUND_DIR/light_factual_report.json"

        run_if_missing "$ROUND_DIR/difficulty_validated_candidates.jsonl" "[Round $ROUND] Step 7/13: validate_difficulty_gain.py" \
            python validate_difficulty_gain.py \
                --input "$ROUND_DIR/light_factual_checked_candidates.jsonl" \
                --output "$ROUND_DIR/difficulty_validated_candidates.jsonl" \
                --report-output "$ROUND_DIR/difficulty_gain_report.json" \
                --model "$DIFFICULTY_GAIN_MODEL" \
                --base-url "$DIFFICULTY_GAIN_BASE_URL" \
                --concurrency "$DIFFICULTY_GAIN_CONCURRENCY" \
                --min-gain-score "$MIN_DIFFICULTY_GAIN_SCORE" \
                --borderline-gain-score "$BORDERLINE_DIFFICULTY_GAIN_SCORE" \
                --min-competitive-judgment-score "$MIN_COMPETITIVE_JUDGMENT_SCORE" \
                "${DIFFICULTY_GAIN_ALLOW_BORDERLINE_ARGS[@]}" \
                "${DIFFICULTY_GAIN_WEAK_PROBE_ARGS[@]}"

        run_if_missing "$ROUND_DIR/evolved.jsonl" "[Round $ROUND] Step 8/13: candidate_selection.py" \
            python candidate_selection.py \
                --input "$ROUND_DIR/difficulty_validated_candidates.jsonl" \
                --output "$ROUND_DIR/evolved.jsonl" \
                --invalid-output "$ROUND_DIR/invalid_generation_cases.jsonl" \
                --report-output "$ROUND_DIR/candidate_selection_report.json"

        run_if_missing "$ROUND_DIR/with_answers.jsonl" "[Round $ROUND] Step 9/13: collect_answers.py" \
            python collect_answers.py \
                --input "$ROUND_DIR/evolved.jsonl" \
                --output "$ROUND_DIR/with_answers.jsonl" \
                --concurrency "$ANSWER_CONCURRENCY" \
                --samples 1 \
                --model "$GPT_MODEL" \
                --base-url "$ANSWER_BASE_URL"

        run_if_missing "$ROUND_DIR/rubric.jsonl" "[Round $ROUND] Step 10/13: gen_rubric.py" \
            python gen_rubric.py \
                --input "$ROUND_DIR/with_answers.jsonl" \
                --output "$ROUND_DIR/rubric.jsonl" \
                --concurrency "$RUBRIC_CONCURRENCY" \
                --model "$GPT_MODEL" \
                --base-url "$RUBRIC_BASE_URL"

        run_if_missing "$ROUND_DIR/scored.jsonl" "[Round $ROUND] Step 11/13: scoring.py" \
            python scoring.py \
                --input "$ROUND_DIR/rubric.jsonl" \
                --output "$ROUND_DIR/scored.jsonl" \
                --answer-mode llm \
                --answer-base-url "$QWEN_BASE_URL" \
                --answer-api-key "$QWEN_API_KEY" \
                --answer-model "$QWEN_MODEL" \
                --judge-base-url "$QWEN_BASE_URL" \
                --judge-api-key "$QWEN_API_KEY" \
                --judge-model "$QWEN_MODEL" \
                --concurrency "$SCORING_CONCURRENCY"
    fi

    run_if_missing "$ROUND_DIR/effect_analysis.jsonl" "[Round $ROUND] Step 12/13: analyze_evolution_effect.py" \
        python analyze_evolution_effect.py \
            --before "$PREV_SCORED" \
            --input "$ROUND_DIR/scored.jsonl" \
            --output "$ROUND_DIR/effect_analysis.jsonl" \
            --matrix-output "$ROUND_DIR/effect_matrix.jsonl"

    run_if_missing "$ROUND_DIR/state_updated.jsonl" "[Round $ROUND] Step 13/13: update_sample_state.py" \
        python update_sample_state.py \
            --input "$ROUND_DIR/effect_analysis.jsonl" \
            --output "$ROUND_DIR/state_updated.jsonl" \
            --memory-dir "$MEMORY_DIR" \
            --preselection-invalid-input "$ROUND_DIR/invalid_generation_cases.jsonl" \
            --report-output "$ROUND_DIR/state_update_report.json"

    # 计算本轮平均得分率
    AVG_RATE=$(compute_avg_score_rate "$ROUND_DIR/scored.jsonl")
    echo "Round $ROUND 平均得分率: $AVG_RATE"
    EFFECT_COUNT=$(extract_effect_count "$ROUND_DIR/effect_analysis.jsonl")
    AVG_DELTA=$(abs_diff_float "$AVG_RATE" "$PREV_AVG_RATE")

    ROUND_OUTPUT_FOR_NEXT="$ROUND_DIR/scored.jsonl"
    if [ -f "$ROUND_DIR/state_updated.jsonl" ] && [ -s "$ROUND_DIR/state_updated.jsonl" ]; then
        ROUND_OUTPUT_FOR_NEXT="$ROUND_DIR/state_updated.jsonl"
    fi

    # 检查提前停止条件
    SHOULD_STOP=$(lt_float "$AVG_RATE" "$EARLY_STOP_RATE")
    if [ "$SHOULD_STOP" = "true" ]; then
        echo "提前停止：Round $ROUND 平均得分率 $AVG_RATE < $EARLY_STOP_RATE"
        printf "%5s | %14s | %s\n" "$ROUND" "$AVG_RATE" "early_stop" >> "$SUMMARY_FILE"
        PREV_SCORED="$ROUND_OUTPUT_FOR_NEXT"
        break
    fi

    if [ "$EFFECT_COUNT" -eq 0 ] && [ "$(lt_float "$AVG_DELTA" "$NO_INFO_MIN_DELTA")" = "true" ]; then
        NO_INFO_STREAK=$((NO_INFO_STREAK + 1))
    else
        NO_INFO_STREAK=0
    fi

    if [ "$NO_INFO_STREAK" -ge "$NO_INFO_STOP_ROUNDS" ]; then
        echo "提前停止：连续 $NO_INFO_STREAK 轮无新信息（effect_count=0 且 avg_delta=$AVG_DELTA < $NO_INFO_MIN_DELTA）"
        printf "%5s | %14s | %s\n" "$ROUND" "$AVG_RATE" "no_info_stop" >> "$SUMMARY_FILE"
        PREV_SCORED="$ROUND_OUTPUT_FOR_NEXT"
        break
    fi

    printf "%5s | %14s | %s\n" "$ROUND" "$AVG_RATE" "continue" >> "$SUMMARY_FILE"

    PREV_SCORED="$ROUND_OUTPUT_FOR_NEXT"
    PREV_AVG_RATE="$AVG_RATE"
    PREV_EFFECT_COUNT="$EFFECT_COUNT"
done

# ===================== 保存最终结果 =====================
FINAL_DIR="$EXP_DIR/final"
mkdir -p "$FINAL_DIR"
cp "$PREV_SCORED" "$FINAL_DIR/final_scored.jsonl"

echo ""
echo "========================================"
echo "循环结束"
echo "最终结果: $FINAL_DIR/final_scored.jsonl"
echo "各轮汇总: $SUMMARY_FILE"
echo "========================================"
cat "$SUMMARY_FILE"
