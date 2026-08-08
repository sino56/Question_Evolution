# Question Evolution Pipeline

本目录包含一套用于 **PoliceQA 数据难度进化** 的脚本。核心目标：对当前候选模型（如 Qwen）得分过高的题目进行"题目进化"，使其能更好地区分强模型与弱模型，从而提升合成训练数据的质量。

> 背景：在 PoliceQA 上，rubric 增强遇到瓶颈——候选答案（Qwen）并不显著差于参考答案（GPT），差异多在风格偏好。因此我们把进化对象从 **rubric** 转向 **question**：让题目本身变得更难、更需要深度推理。

---

## 1. 整体流程

> 当前实现是“跨轮链式主流程 + 单轮局部多候选探索 + 候选选优”，并非完整多层树搜索。形成有效边界或终止状态的样本会在后续轮次透传并复用已有评分结果。

当前推荐主流程由 `run_loop.sh` 编排，入口是已完成准入的
`data/data.jsonl`。每次执行都会创建新的实验目录。

```text
Stage 0: data/data.jsonl
Stage 1: scoring.py -> round_0/scored.jsonl
Stage 2: profile_samples.py -> select_evolution_candidates.py
Stage 3: operator_router.py -> question_evolution.py
Stage 4: validate_evolved_question.py -> validate_difficulty_gain.py -> candidate_selection.py
Stage 5: collect_answers.py -> gen_rubric.py -> scoring.py
         -> analyze_evolution_effect.py -> update_sample_state.py
```

从 Round 1 开始，每轮 12 个步骤与 `run_loop.sh` 保持一致：

| 步骤 | 脚本 | 产物 |
| --- | --- | --- |
| 0 | 准备上一轮 scored/state 输入并写入当前 `round` | `round_N/input.jsonl` |
| 1 | `profile_samples.py` | `profiled.jsonl` |
| 2 | `select_evolution_candidates.py` | `profiled_candidates.jsonl` |
| 3 | `operator_router.py` | `routed.jsonl` |
| 4 | `question_evolution.py` | `candidates.jsonl` |
| 5 | `validate_evolved_question.py` | `validated_candidates.jsonl` |
| 6 | `validate_difficulty_gain.py` | `difficulty_validated_candidates.jsonl`, `difficulty_gain_report.json` |
| 7 | `candidate_selection.py` | `evolved.jsonl` |
| 8 | `collect_answers.py` | `with_answers.jsonl` |
| 9 | `gen_rubric.py` | `rubric.jsonl` |
| 10 | `scoring.py` | `scored.jsonl` |
| 11 | `analyze_evolution_effect.py` | `effect_analysis.jsonl`, `effect_matrix.jsonl`, `semantic_economy_report.json` |
| 12 | `update_sample_state.py` | `state_updated.jsonl`, memory bank |

`question_evolution.py` 的 legacy 单脚本路径仍可用于兼容旧数据或局部调试，但不再是推荐主流程。推荐路径必须经过画像、分流、路由、复杂度/可回答性校验、难度收益验证、候选选择、效果统计和状态更新。

`question_evolution.py`、`collect_answers.py`、`gen_rubric.py` 和 `scoring.py`
会把单条失败详情写入对应的 `.failed` 文件，并在本阶段结束后返回非零状态；
`run_loop.sh` 会立即停止，不允许缺失样本的部分输出继续进入下一阶段。
Rubric 阶段按样本逐条处理，即使多个样本的 prompt 相同也不会随机删除记录。

### 1.2 题目行为诊断旁路（22A）

`question_behavior_analysis.py` 提供独立于主链的三段式诊断：`statistics`
（22A-0 离线组内统计与阈值校准报告）、`diagnose`（22A-1 规则化
`behavior_analysis.jsonl`）和 `observe`（22A-2 对少量合格题的真实单题观察）。
它只读取已评分 JSONL，绝不改写 `score_rate`、`scoring_result`、路由、状态或
正式 memory。Qwen 是唯一决策评分来源；GPT 只用于离线分歧观察。

```bash
python question_behavior_analysis.py diagnose \
  --input experiments/.../round_1/scored.jsonl \
  --output experiments/.../round_1/behavior_analysis.jsonl \
  --report-output experiments/.../round_1/behavior_analysis_report.json

python question_behavior_analysis.py observe \
  --input experiments/.../round_1/behavior_analysis.jsonl \
  --source-input experiments/.../round_1/scored.jsonl \
  --output experiments/.../round_1/behavior_observed_analysis.jsonl \
  --model "$GPT_MODEL" --base-url "$OPENAI_BASE_URL"
```

主循环默认关闭这条旁路。设置 `ENABLE_QUESTION_BEHAVIOR_ANALYSIS=true` 后才会
生成统计与规则诊断；再设置 `ENABLE_QUESTION_BEHAVIOR_OBSERVER=true` 才会调用
观察器。`QUESTION_BEHAVIOR_MIN_ELIGIBLE_COVERAGE` 可在完成 22A-0 校准后冻结为
批次门槛；低于该门槛时不会发起任何观察器调用。旁路故障只留下失败记录或警告，
不会阻断正式流水线。

### 1.3 机制归纳、验证与路由旁路（22B / 25B + 22C-4）

`mechanism_governance.py` 只消费 22A sidecar、效果分析和冻结配置，并只写
新的 sidecar。它不会改写 `score_rate`、`operator_candidates`、既有
`operator_plan`、`evolution_state` 或本地 memory。推荐按以下顺序离线运行：

```bash
# 22B-2: 多根样本、可定位证据和反例齐全时才产生 proposed 机制候选。
python mechanism_governance.py induce \
  --input experiments/.../round_1/behavior_observed_analysis.jsonl \
  --source-input experiments/.../round_1/scored.jsonl \
  --output experiments/.../round_1/mechanism_candidates.jsonl \
  --publish-facts-output experiments/.../round_1/mechanism_publish_candidates.jsonl \
  --rejections-output experiments/.../round_1/mechanism_induction_rejections.jsonl

# 22B-3: 冻结配置须含 experiment_kind=retrospective|forward，且验证根样本不得来自候选机制的来源证据。
python mechanism_governance.py validate \
  --candidates experiments/.../round_1/mechanism_candidates.jsonl \
  --effects experiments/.../round_2/effect_analysis.jsonl \
  --frozen-config experiments/.../frozen_mechanism_evaluation.json \
  --manual-reviews experiments/.../mechanism_reviews.jsonl \
  --output experiments/.../mechanism_effect_validations.jsonl \
  --matrix-output experiments/.../mechanism_effect_matrix.jsonl \
  --publish-facts-output experiments/.../round_2/mechanism_publish_candidates.jsonl \
  --report-output experiments/.../mechanism_effect_validation_report.json

# 25B + 22C-4: stable snapshot 下的路由旁路对照，仍不改变 Router 输出。
python mechanism_governance.py route-audit \
  --routes experiments/.../round_2/routed.jsonl \
  --candidates experiments/.../round_1/mechanism_candidates.jsonl \
  --validations experiments/.../mechanism_effect_validations.jsonl \
  --project-root . --memory-snapshot-id "$MEMORY_SNAPSHOT_ID" \
  --output experiments/.../round_2/mechanism_route_audit.jsonl
```

`--mode limited` 仅在 stable 的非空 Global Memory snapshot、`qualified`
机制、独立验证、人工批准同时满足时生成“可有限接入”的审计结论；它依然不会
写入或重排 Router 候选。`--rollback` 显式记录并恢复 audit-only 行为。使用
`route-replay` 可在冻结的独立验证集上写出回放报告。
`mechanism_publish_candidates.jsonl` 是传给全局 Memory 发布器的候选事实，不是
自动发布指令。

### 1.4 Agent 动态预算重分配（阶段 10）

Agent 的预算调整只处理**剩余**预算，并且只能在已发布产物的阶段边界提出：

```text
published observation
-> budget proposal (evidence-bound)
-> BudgetValidator
-> immutable new plan revision
-> registered future execution
```

`AgentTask` 可选 `budget_limits` 对象，用于声明 `generation`、`candidate`、
`branch`、`search_steps`、`scoring`、`repeat_scoring`、`vertical_depth`、
`model_calls` 和 `time_seconds` 的硬上限。例如：

```json
{
  "budget_limits": {
    "generation": 12,
    "scoring": 18,
    "repeat_scoring": 4,
    "model_calls": 20
  }
}
```

每个 Agent run 的 `budget_ledger.json` 保留硬上限、消耗、剩余分配和追加事件；
每次注册工具调用都会留下账本记录，配置 `model_calls` 时会在调用前严格扣除。
观察器把各算子的 `validation_failed`、`not_applicable`、`score_increased`、
`score_decreased` 和可选评分方差整理成证据。只有守恒、未改历史消耗、未绕过校验/
评分、且不继续 `score_increased` 路径的建议才会通过 `BudgetValidator`。

运行目录中的 `budget_reallocation_proposals.jsonl`、
`budget_reallocation_decisions.jsonl` 和 `budget_reallocation_report.md` 分别保存
建议、校验结果和调整前后差异。批准的调整只会在正常控制器已经要求 `replan` 时
创建新的 `plans/plan_rNNN.json`；旧计划、已完成步骤、搜索状态和正式 Memory 均不被
改写。缺证据、完全排除算子或风险不明的建议保持 `needs_human_review`。

### 流程图
```text
Stage 0 输入
  data/data.jsonl
        |
        v
Round 0 baseline
  scoring.py
        |
        v
  round_0/scored.jsonl
  (得到 scoring_result / score_rate)
        |
        v
Round N 输入
  上一轮 scored.jsonl 或 state_updated.jsonl
        |
        v
  profile_samples.py -> 生成 sample_profile / overscore_diagnosis
        |
        v 
  select_evolution_candidates.py -> 决定 evolution_action
        |
        +------------------------------+
        |                              |
        | 不需要进化                   | 需要进化
        | pass_through / stop          | high-score overscore /
        |                              | low-score reconstruct / middle-score probe
        v                              v
  保留原题                       operator_router.py
  question_evolved=false         -> 选择 primary / backup / avoid operators
        |                              |
        |                              +<-- operator_memory_bank.jsonl
        |                              +<-- failure_memory_bank.jsonl
        |                              +<-- 上一轮 evolution_state
        |                              |
        |                              v
        |                        question_evolution.py
        |                        -> 按算子生成 1-N 个候选题
        |                              |
        |                              v
        |                        validate_evolved_question.py
        |                        -> 校验复杂度 / 可答性 / 重复题型
        |                              |
        |                              v
        |                        validate_difficulty_gain.py
        |                        -> 校验是否有真实难度收益 / 无线索泄漏
        |                              |
        |                              v
        |                        candidate_selection.py
        |                        -> 只在通过门禁的候选中选 1 条主链题
        |                              |
        |                +-------------+-------------+
        |                |                           |
        |                | 全部候选不合格            | 选中有效候选
        |                v                           v
        +------------> evolved.jsonl <--------------+
                             |
                             v
                    collect_answers.py
                    -> 为当前 prompt 生成参考答案
                             |
                             v
                    gen_rubric.py
                    -> 生成新 rubric / score_prompt
                             |
                             v
                    scoring.py
                    -> 候选模型重新作答并评分
                             |
                             v
                    analyze_evolution_effect.py
                    -> 比较前后 score_rate 与 focus 命中
                             |
                             v
                    update_sample_state.py
                    -> 更新 evolution_state
                    -> 写入 memory bank
                             |
                             +--> operator_memory_bank.jsonl
                             +--> failure_memory_bank.jsonl
                             +--> invalid_generation_cases.jsonl
                             +--> state_updated.jsonl
                                      |
                                      v
                           判断平均得分率是否低于
                           EARLY_STOP_RATE
                                      |
                        +-------------+-------------+
                        |                           |
                        | 否                        | 是
                        v                           v
                 进入下一轮 Round N+1        停止迭代并输出
                                            final/final_scored.jsonl
```


### 1.1 脚本职责速查

| 脚本 | 职责 | 典型输入 | 典型输出 |
| --- | --- | --- | --- |
| `scoring.py` | 调用候选模型生成答案，并用评分模型按 rubric 打分 | `*.jsonl` | `*_scored.jsonl` |
| `profile_samples.py` | 生成样本画像和虚高诊断 | `*_scored.jsonl` | `profiled.jsonl` |
| `select_evolution_candidates.py` | 输出 `evolution_action`，区分高分进化、低分重构、中分探测、透传和停止 | `profiled.jsonl` | `profiled_candidates.jsonl` |
| `operator_router.py` | 根据画像、状态和 memory 选择 operator | `profiled_candidates.jsonl` | `routed.jsonl` |
| `question_evolution.py` | 按 operator 生成 1-4 个候选题，支持 validate-retry | `routed.jsonl` | `candidates.jsonl` |
| `validate_evolved_question.py` | 校验可回答性、重复题型、格式风险、语义冗余与题面泄漏；字符数仅观察 | `candidates.jsonl` | `validated_candidates.jsonl` |
| `validate_difficulty_gain.py` | 校验候选题是否有真实难度收益、无线索泄漏且不靠格式变难 | `validated_candidates.jsonl` | `difficulty_validated_candidates.jsonl`, `difficulty_gain_report.json` |
| `candidate_selection.py` | 从通过复杂度和难度收益验证的候选中选择主链题目 | `difficulty_validated_candidates.jsonl` | `evolved.jsonl` |
| `collect_answers.py` | 调用强模型为题目生成参考答案 | `*.jsonl` | `*_with_answers.jsonl` |
| `gen_rubric.py` | 根据题目和参考答案生成 rubric 与 score_prompt | `*_with_answers.jsonl` | `*_rubric.jsonl` |
| `analyze_evolution_effect.py` | 统计轻量边界命中、operator 效果矩阵与语义经济/泄漏观察 | `*_scored.jsonl` | `effect_analysis.jsonl`, `semantic_economy_report.json` |
| `update_sample_state.py` | 更新跨轮状态并写入三类 memory bank | `effect_analysis.jsonl` | `state_updated.jsonl` |

---

## 2. 数据格式详解

数据采用 **JSONL** 格式，每行一个样本（一个 JSON 对象）。下文按流水线各阶段说明字段的"增删改"。

### 2.1 阶段 0：原始数据

默认入口是 `data/data.jsonl`，每行至少包含：

```json
{
  "index": 11112,
  "prompt": "在使用重合比较法（将两台摄像机画面重叠或将照片重叠比对）来研判嫌疑人身份时，对两张照片的拍摄角度有什么具体要求？",
  "meta_info": {
    "references": ["参考答案文本，来自 GPT-5.4"],
    "answer_from_book": "教材/资料中的简版答案",
    "source_file": "视频侦查技术-公大社-2015.jsonl",
    "labels": { "topic": ["情报研判"], "difficulty": "专业", ... },
    ...
  },
  "rubric": [
    { "title": "核心要点", "description": "...", "weight": 4 },
    { "title": "边界条件", "description": "...", "weight": 3 },
    { "title": "常见错误", "description": "...", "weight": -2 }
  ],
  "rubric_thought_process": "rubric 设计思路...",
  "score_prompt": "你是严格的模型评测打分员...<<<待评答案>>..."
}
```

#### 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `index` | int | 样本编号 |
| `prompt` | string | **题目文本**，是模型需要回答的问题 |
| `meta_info` | object | 元数据容器 |
| `meta_info.references` | list[string] | 参考答案列表，通常取 `[0]` 作为标准答案；由 GPT-5.4 生成 |
| `meta_info.answer_from_book` | string | 教材/资料原始答案，可作参考 |
| `meta_info.labels` | object | 题目标签：主题、难度、题型、场景等 |
| `rubric` | list[object] | 评分标准；`weight > 0` 为加分项，`weight < 0` 为扣分项 |
| `rubric[].title` | string | 评分维度标题 |
| `rubric[].description` | string | 评分细则 |
| `rubric[].weight` | int | 该项满分/扣分值 |
| `rubric_thought_process` | string | 生成 rubric 时的设计思路 |
| `score_prompt` | string | 给评分模型的完整提示词，其中 `<<<待评答案>>` 为占位符 |

---

### 2.2 阶段 1：`scoring.py` 输出

`scoring.py` 会在每条样本上新增一个 `scoring_result` 字段，记录候选模型（如 Qwen）的回答及评分结果。

```json
{
  "index": 11112,
  "prompt": "...",
  "meta_info": { "references": [...], ... },
  "rubric": [...],
  "score_prompt": "...",
  "scoring_result": {
    "answer_mode": "llm",
    "answer_model": "hjl_Qwen3.6-27B",
    "candidate_answer": "候选模型的完整回答文本...",
    "item_scores": [
      { "title": "核心要点", "weight": 4, "awarded": 4, "brief_reason": "覆盖了关键信息" },
      { "title": "边界条件", "weight": 3, "awarded": 2, "brief_reason": "提到了部分边界" },
      { "title": "常见错误", "weight": -2, "awarded": 0, "brief_reason": "未触发" }
    ],
    "overall_comment": "整体评价...",
    "total_awarded": 6,
    "total_possible": 7,
    "judge_model": "hjl_Qwen3.6-27B",
    "judge_raw_response_trace_id": "评分原始响应的 trace ID..."
  }
}
```

#### 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `scoring_result` | object | 评分结果容器 |
| `scoring_result.answer_mode` | string | `reference`（用参考答案自评）或 `llm`（调用候选模型） |
| `scoring_result.answer_model` | string | 生成 candidate_answer 的模型名 |
| `scoring_result.candidate_answer` | string | 候选模型对 `prompt` 的回答 |
| `scoring_result.item_scores` | list[object] | 逐条 rubric 得分；`title` 必须与 `rubric` 严格一致 |
| `scoring_result.total_awarded` | int | 实际总得分（含负分项扣分） |
| `scoring_result.total_possible` | int | 正分项满分之和 |
| `scoring_result.judge_model` | string | 执行评分的模型 |
| `scoring_result.judge_raw_response_trace_id` | string | 评分原始返回在 `*.judge_traces.jsonl.gz` 中的 trace ID；sidecar 摘要登记在 manifest |

#### 得分率计算

```text
score_rate = scoring_result.total_awarded / scoring_result.total_possible
```

新版主流程优先读取 `evolution_action` 决定是否进化；legacy 单脚本路径才继续使用 `score_rate >= 0.8` 作为触发条件。

---

### 2.3 主流程中间字段：画像、路由、进化、校验和候选选择

当前主流程不是直接把高分题送入统一 prompt，而是依次补充：

1. `profile_samples.py`：新增 `sample_profile` 与 `overscore_diagnosis`。
2. `select_evolution_candidates.py`：新增 `evolution_action`。
3. `operator_router.py`：新增 `operator_route`。
4. `question_evolution.py`：按 operator 生成候选题，新增 `candidate_group_id`、`candidate_id`、`candidate_operator`、`candidate_generation` 和 `meta_info.question_evolution_metadata`。
5. `validate_evolved_question.py`：新增 `validation_result`，可包含 LLM/mock 校验字段 `main_axis_clear`、`answerable`、`external_knowledge_required`、`repeated_pattern_with_previous_round`、`format_difficulty_dominant`，以及语义经济字段 `semantic_economy_mode`、`semantic_economy_risk`、`semantic_redundancy_dominant`、`shared_context_repeated`、`answer_hint_expansion`、`surface_leak_risk`、`surface_leak_type`。字符数、父子差值和增长比例仅用于观察，不参与准入或排序。
6. `candidate_selection.py`：在选中记录上新增 `candidate_selection`。

`evolution_action` 共有五类：`evolve_high_score_overscore`、
`reconstruct_low_score_boundary`、`probe_middle_score_boundary`、
`pass_through_or_scoring_noise` 和 `stop_evolution`。跨轮状态中的
`recommended_next_methods`、`rollback_and_reroute` 与局部探索状态优先于当前分数区间，
避免中间分样本或待换算子的样本被意外透传。

`question_evolution.py` 在需要进化时会把原 `prompt` 移到 `meta_info.prompt_old`，并把旧 `rubric` / `score_prompt` / `scoring_result` 移到 `meta_info.stale_*`；透传样本会保留 `question_evolved=false`。

实现还会写入一层 `meta_info.parent_snapshot`。当所有候选均无效或进化后分数反而升高时，
流水线据此恢复直接父题的 prompt、reference、rubric、score prompt、评分结果与得分率，
并在下一轮避开失败算子重新路由；它不会保存或遍历多层祖先分支。

> 为什么要把 rubric/score_prompt/scoring_result 移走？因为 `prompt` 变了，旧的 rubric 和评分结果已经失效，必须重新生成。

#### 进化后的样本示例

```json
{
  "index": 11112,
  "prompt": "升级后的新题目文本...",
  "question_evolved": true,
  "meta_info": {
    "references": ["原参考答案，此时已不适用新题"],
    "prompt_old": "原题文本...",
    "stale_rubric": [
      { "title": "核心要点", "description": "...", "weight": 4 }
    ],
    "stale_score_prompt": "旧的评分提示词...",
    "stale_scoring_result": {
      "total_awarded": 6,
      "total_possible": 7,
      ...
    },
    "question_evolution_metadata": {
      "question_evolved": true,
      "trigger_score_rate": 0.857,
      "question_evolution_model": "gpt-5.4",
      "evolution_strategy": "增加反事实条件与最小充分证据要求...",
      "notes_for_reference": "基本适用",
      "question_evolution_raw_response_trace_id": "进化原始响应的 trace ID..."
    },
    ...
  }
}
```

#### 字段说明

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `prompt` | string | **进化后的新题目**；后续所有步骤都基于这个 prompt |
| `question_evolved` | bool | 本题是否被进化 |
| `meta_info.prompt_old` | string | 原题文本，便于对比 |
| `meta_info.stale_rubric` | list[object] | 旧 rubric（对新题已失效，仅供参考） |
| `meta_info.stale_score_prompt` | string | 旧 score_prompt（对新题已失效） |
| `meta_info.stale_scoring_result` | object | 旧评分结果（对新题已失效） |
| `meta_info.question_evolution_metadata` | object | 进化元数据 |
| `meta_info.question_evolution_metadata.trigger_score_rate` | float | 触发进化的得分率 |
| `meta_info.question_evolution_metadata.evolution_strategy` | string | 采用的进化策略说明 |
| `meta_info.question_evolution_metadata.notes_for_reference` | string | 原参考答案是否仍适用 |

---

### 2.4 标准闭环：`collect_answers.py` 输出

`collect_answers.py` 会调用强模型（默认 GPT-5.4）为每个 `prompt` 生成参考答案，并覆盖 `meta_info.references`。

输出结构（仅保留关键字段）：

```json
{
  "index": 11112,
  "prompt": "升级后的新题目文本...",
  "meta_info": {
    "references": ["新的参考答案，由 GPT-5.4 针对 evolved_prompt 生成"],
    "prompt_old": "原题文本...",
    "stale_rubric": [...],
    "stale_score_prompt": "...",
    "stale_scoring_result": {...},
    "question_evolution_metadata": {...},
    ...
  }
}
```

注意：`collect_answers.py` 保留整条 pipeline record，只更新 `meta_info.references`，并移除采样过程的临时字段。`question_evolved=false` 的透传样本不会重新采集答案。

---

### 2.5 标准闭环：`gen_rubric.py` 输出

`gen_rubric.py` 读取 `meta_info.references[0]` 作为参考答案，为新的 `prompt` 生成 rubric。

输出会在 `collect_answers.py` 的基础上新增：

```json
{
  "index": 11112,
  "prompt": "升级后的新题目文本...",
  "meta_info": { "references": [...], "prompt_old": "...", ... },
  "rubric": [
    { "title": "...", "description": "...", "weight": 5 }
  ],
  "rubric_thought_process": "...",
  "score_prompt": "...<<<待评答案>>..."
}
```

---

### 2.6 标准闭环：第二次 `scoring.py` 输出

与 baseline scoring 相同，再次用候选模型（Qwen）回答新题，并用评分模型打分。

```json
{
  "index": 11112,
  "prompt": "升级后的新题目文本...",
  "meta_info": { "references": [...], "prompt_old": "...", ... },
  "rubric": [...],
  "score_prompt": "...",
  "scoring_result": {
    "answer_mode": "llm",
    "answer_model": "hjl_Qwen3.6-27B",
    "candidate_answer": "候选模型对新题的回答...",
    "item_scores": [...],
    "total_awarded": 4,
    "total_possible": 10,
    ...
  }
}
```

随后 `analyze_evolution_effect.py` 会新增 `effect_analysis`，`update_sample_state.py` 会新增下一轮使用的 `evolution_state`，并把有效、失败和无效生成经验写入 `memory/`。

---

## 3. 字段流转总表

| 字段 | Stage 0 输入 | scoring | profile/select/router | evolution/validation/selection | standard closure | effect/state |
| --- | --- | --- | --- | --- | --- | --- |
| `index` / `sample_id` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `prompt` | 原题 | 原题 | 原题 | 可能改写为候选/选中题 | 新题 | 新题 |
| `sample_profile` / `overscore_diagnosis` | 无 | 无 | 新增 | 保留 | 保留 | 保留 |
| `evolution_action` / `operator_route` | 无 | 无 | 新增 | 消费并保留 | 保留 | 保留 |
| `meta_info.question_evolution_metadata` | 无 | 无 | 无 | 新增 | 保留 | 保留 |
| `validation_result` / `candidate_selection` | 无 | 无 | 无 | 新增 | 保留 | 保留 |
| `rubric` / `score_prompt` | ✓ | ✓ | ✓ | 进化题移入 stale | 重新生成 | ✓ |
| `scoring_result` | 无 | 新增 | 保留 | 进化题移入 stale | 重新生成 | ✓ |
| `effect_analysis` / `evolution_state` | 无 | 无 | 可继承上一轮 | 可继承上一轮 | 无 | 新增 |

> 注：标准闭环脚本保留 pipeline record 的跨阶段字段；进化元数据、候选选择、效果分析和状态更新分别位于 `meta_info.question_evolution_metadata`、`candidate_selection`、`effect_analysis` 和 `evolution_state`。

---

## 4. 快速开始

### 4.1 单轮运行

先创建本地环境并安装依赖：

```bash
python -m venv .venv
pip install -r requirements.txt
```

真实运行前至少配置以下环境变量之一：

```bash
# profile/question evolution/answer/rubric 可共用 OpenAI-compatible 配置
export OPENAI_BASE_URL="https://your-openai-compatible-endpoint/v1"
export OPENAI_API_KEY="..."

# Router 默认复用 GPT/OpenAI provider；可选覆盖项
export ROUTER_MODEL="${GPT_MODEL}"
export ROUTER_CONCURRENCY=20
export ROUTER_TIMEOUT=60
export ROUTER_RETRIES=0

# 如需拆分配置，可分别设置
export PROFILE_API_KEYS="..."
export EVOLVE_API_KEYS="..."
export ANSWER_API_KEYS="..."
export RUBRIC_API_KEYS="..."

# 候选模型与 judge
export QWEN_BASE_URL="http://127.0.0.1:18011/v1"
export QWEN_API_KEY=""
export QWEN_MODEL="hjl_Qwen3.6-27B"
```

如果你更习惯原来的明文 Python 配置方式，可以直接在本地 `config.py` 中填写：

```python
BASE_URL = "https://hanbbq.labpilot.top/v1"
GPT_MODEL = "gpt-5.4"
HIAPI_KEYS_BIG = ["REPLACE_WITH_YOUR_LOCAL_KEY"]

QWEN_BASE_URL = "http://127.0.0.1:18011/v1"
QWEN_API_KEY = ""
QWEN_MODEL = "hjl_Qwen3.6-27B"
```

`config.py` 已被 `.gitignore` 忽略，脚本会按 `CLI 非空参数 > 环境变量 > config.py > 默认值` 的顺序读取配置。Qwen 本地服务不需要 key 时保持空字符串即可，`scoring.py` 会在 OpenAI SDK 需要参数时内部使用占位值。不要把真实 strong-model API key 写回受版本控制的源码文件；已经暴露过的 key 应在服务端人工轮换。

真实 Bash/API 验收前可先运行预检：

```bash
python check_runtime_environment.py
```

### 4.2 多轮循环运行（推荐）

如果你想让 question evolution 自动循环多轮，直到 Qwen 平均得分率低于阈值或达到最大轮数，使用：

```bash
bash run_loop.sh
```

中断后可把同一个实验目录传回入口，继续复用其中的 manifest、partial、
checkpoint 与 memory bank：

```bash
bash run_loop.sh --resume-exp-dir experiments/2026-07-18/exp1

# Windows PowerShell
.\run_loop.ps1 -ResumeExperimentDir experiments\2026-07-18\exp1
```

也可分别设置 `RESUME_EXP_DIR` 环境变量。未指定恢复目录时仍会创建新的
`exp/expN`；指定后不会创建新实验，也不会重置已有 `summary.txt`。

默认配置：

- 最大轮数：`MAX_ROUNDS=5`
- 提前停止阈值：`EARLY_STOP_RATE=0.5`（当某轮 Qwen 平均得分率 < 50% 时停止）
- 每轮触发进化的阈值：`MIN_SCORE_RATE=0.8`
- 每条样本最多候选：`NUM_CANDIDATES=2`
- 单轮候选总预算：`MAX_CANDIDATE_BUDGET=0`，表示自动使用待进化样本数 × 2

每轮结果保存在 `experiments/YYYY-MM-DD/exp*/round_N/` 子文件夹中：

```text
experiments/YYYY-MM-DD/exp/
├── round_0/
│   ├── input.jsonl       # 初始输入（从 data/data.jsonl 复制并写入 round=0）
│   └── scored.jsonl      # 初始 baseline 评分结果
├── round_1/
│   ├── input.jsonl
│   ├── profiled.jsonl
│   ├── profiled_candidates.jsonl
│   ├── routed.jsonl
│   ├── candidates.jsonl
│   ├── validated_candidates.jsonl
│   ├── evolved.jsonl
│   ├── with_answers.jsonl
│   ├── rubric.jsonl
│   ├── scored.jsonl
│   ├── effect_analysis.jsonl
│   ├── effect_matrix.jsonl
│   └── state_updated.jsonl
├── round_2/
│   └── ...
├── memory/
│   ├── operator_memory_bank.jsonl
│   ├── failure_memory_bank.jsonl
│   └── invalid_generation_cases.jsonl
├── summary.txt           # 各轮平均得分率汇总
└── final/
    └── final_scored.jsonl
```

`run_loop.sh` 会自动生成 `exp/summary.txt`，方便你观察得分率下降趋势：

```text
Round | Avg Score Rate | Status
------|----------------|--------
    0 |         0.7865 | baseline
    1 |         0.6123 | continue
    2 |         0.4532 | early_stop
```

若任一 API 阶段存在失败记录，脚本会保留对应 `*.failed` 文件并以非零状态退出；由于 `run_loop.sh` 启用 `set -e`，该次实验不会继续使用不完整产物。修复故障后用 `--resume-exp-dir` 指向原实验目录，即可从已确认的阶段产物或 checkpoint 继续。

#### 修改循环参数

直接编辑 `run_loop.sh` 顶部的配置区即可：

```bash
MAX_ROUNDS=5
EARLY_STOP_RATE=0.5
MIN_SCORE_RATE=0.8
NUM_CANDIDATES=2
MAX_CANDIDATE_BUDGET=0
VALIDATION_RETRIES=1
```

### 4.3 分步运行

```bash
# Round 0：Qwen/GPT 各自 3 次回答、各自 2 次自评
python round0_stability_probe.py \
  --input data/data.jsonl \
  --output round_0_scored.jsonl \
  --answer-mode llm \
  --answer-base-url "$QWEN_BASE_URL" \
  --answer-api-key "$QWEN_API_KEY" \
  --answer-model "$QWEN_MODEL" \
  --judge-base-url "$QWEN_BASE_URL" \
  --judge-api-key "$QWEN_API_KEY" \
  --judge-model "$QWEN_MODEL" \
  --qwen-judge-repeats 2 \
  --gpt-judge-base-url "$GPT_JUDGE_BASE_URL" \
  --gpt-judge-api-key "$GPT_JUDGE_API_KEY" \
  --gpt-judge-model "$GPT_JUDGE_MODEL" \
  --gpt-judge-repeats 2 \
  --gpt-answer-trials 3 \
  --gpt-answer-base-url "$GPT_ANSWER_BASE_URL" \
  --gpt-answer-api-key "$GPT_ANSWER_API_KEY" \
  --gpt-answer-model "$GPT_ANSWER_MODEL"

# Step 1：画像
python profile_samples.py \
  --input round_0_scored.jsonl \
  --output round_1_profiled.jsonl \
  --model "$PROFILE_MODEL" \
  --base-url "$PROFILE_BASE_URL"

# Step 2：候选分流
python select_evolution_candidates.py \
  --input round_1_profiled.jsonl \
  --output round_1_profiled_candidates.jsonl \
  --high-score-threshold 0.8

# Step 3：算子路由
# 正常运行固定为 hybrid + live：LLM 的全部合法候选会冻结成分支；
# timeout、网络或契约失败时才使用确定性规则回退。
python operator_router.py \
  --input round_1_profiled_candidates.jsonl \
  --output round_1_routed.jsonl \
  --memory-dir memory \
  --routing-mode hybrid \
  --assignment-mode live \
  --router-model "$ROUTER_MODEL" \
  --router-concurrency 20 \
  --router-timeout 60 \
  --router-retries 0

# Step 4：多候选进化，含 validate-retry
python question_evolution.py \
  --input round_1_routed.jsonl \
  --output round_1_candidates.jsonl \
  --min-score-rate 0.8 \
  --model "$EVOLVE_MODEL" \
  --base-url "$EVOLVE_BASE_URL" \
  --num-candidates 2 \
  --max-candidate-budget 0 \
  --validation-retries 1 \
  --max-semantic-retry-attempts 2

# Step 5：复杂度/可回答性校验
python validate_evolved_question.py \
  --input round_1_candidates.jsonl \
  --output round_1_validated_candidates.jsonl \
  --semantic-economy-mode enforce \
  --validate-schema

# Step 6：难度收益验证
python validate_difficulty_gain.py \
  --input round_1_validated_candidates.jsonl \
  --output round_1_difficulty_validated_candidates.jsonl \
  --report-output round_1_difficulty_gain_report.json \
  --model "$DIFFICULTY_GAIN_MODEL" \
  --base-url "$DIFFICULTY_GAIN_BASE_URL" \
  --concurrency 5 \
  --min-gain-score 0.75 \
  --borderline-gain-score 0.65 \
  --min-competitive-judgment-score 0.60

# 可选：开启弱模型 light probe
python validate_difficulty_gain.py \
  --input round_1_validated_candidates.jsonl \
  --output round_1_difficulty_validated_candidates.jsonl \
  --report-output round_1_difficulty_gain_report.json \
  --model "$DIFFICULTY_GAIN_MODEL" \
  --base-url "$DIFFICULTY_GAIN_BASE_URL" \
  --enable-weak-probe \
  --weak-probe-mode light \
  --weak-answer-model "$WEAK_ANSWER_MODEL" \
  --weak-answer-base-url "$WEAK_ANSWER_BASE_URL" \
  --weak-answer-api-key "$WEAK_ANSWER_API_KEY"

# Step 7：候选选择
python candidate_selection.py \
  --input round_1_difficulty_validated_candidates.jsonl \
  --output round_1_evolved.jsonl \
  --invalid-output round_1_invalid_generation_cases.jsonl

# Step 8：采集参考答案
python collect_answers.py \
  --input round_1_evolved.jsonl \
  --output round_1_with_answers.jsonl \
  --samples 1 \
  --model "$GPT_MODEL" \
  --base-url "$ANSWER_BASE_URL"

# Step 9：重新生成 rubric
python gen_rubric.py \
  --input round_1_with_answers.jsonl \
  --output round_1_rubric.jsonl \
  --model "$GPT_MODEL" \
  --base-url "$RUBRIC_BASE_URL"

# Step 10：再次评分
python scoring.py \
  --input round_1_rubric.jsonl \
  --output round_1_scored.jsonl \
  --answer-mode llm \
  --answer-base-url "$QWEN_BASE_URL" \
  --answer-api-key "$QWEN_API_KEY" \
  --answer-model "$QWEN_MODEL" \
  --judge-base-url "$QWEN_BASE_URL" \
  --judge-api-key "$QWEN_API_KEY" \
  --judge-model "$QWEN_MODEL" \
  --answer-trials 3 \
  --qwen-judge-repeats 2 \
  --gpt-judge-base-url "$GPT_JUDGE_BASE_URL" \
  --gpt-judge-api-key "$GPT_JUDGE_API_KEY" \
  --gpt-judge-model "$GPT_JUDGE_MODEL" \
  --gpt-judge-repeats 2 \
  --gpt-answer-trials 3 \
  --gpt-answer-base-url "$GPT_ANSWER_BASE_URL" \
  --gpt-answer-api-key "$GPT_ANSWER_API_KEY" \
  --gpt-answer-model "$GPT_ANSWER_MODEL"

# Step 11：效果统计
python analyze_evolution_effect.py \
  --before round_0_scored.jsonl \
  --input round_1_scored.jsonl \
  --output round_1_effect_analysis.jsonl \
  --matrix-output round_1_effect_matrix.jsonl \
  --semantic-report-output round_1_semantic_economy_report.json

# Step 12：状态更新和 memory bank 写入
python update_sample_state.py \
  --input round_1_effect_analysis.jsonl \
  --output round_1_state_updated.jsonl \
  --memory-dir memory \
  --preselection-invalid-input round_1_invalid_generation_cases.jsonl
```

---

## 5. 常见问题

### Q1：如何只看哪些题目被进化了？

`candidate_selection.py` 之后的 `evolved.jsonl` 或标准闭环后的记录中，过滤 `question_evolved == true`：

```python
import json

with open("round_1_evolved.jsonl", "r", encoding="utf-8") as f:
    for line in f:
        item = json.loads(line)
        if item.get("question_evolved"):
            print(item["meta_info"]["prompt_old"])
            print("→", item["prompt"])
            print()
```

### Q2：标准闭环之后如何知道哪些题是进化过的？

部分标准闭环脚本可能丢弃顶层 `question_evolved`，但会保留 `meta_info.question_evolution_metadata`：

```python
if item["meta_info"].get("question_evolution_metadata", {}).get("question_evolved"):
    print("本题是进化题")
```

### Q3：为什么进化后要把 rubric 移走而不是直接更新？

题目进化后，问题的核心、约束、推理要求都可能改变，旧的 rubric 可能不再匹配新题。因此本程序选择把旧 rubric 标记为 `stale_`，让 `gen_rubric.py` 针对新 prompt 和新 reference 重新生成 rubric，避免评分标准与新题脱节。

### Q4：`--min-score-rate` 设多少合适？

默认值 `0.8` 是一个经验值：得分率 80% 以上说明候选模型（Qwen）基本答对了这道题，题目对当前候选模型区分度不足。你可以根据实际得分分布调整：

- 想进化更多题：降到 `0.7`
- 只想进化满分题：提高到 `0.9` 或 `1.0`

### Q5：可以只对进化题重新采集参考答案吗？

可以。先用 `jq` 或 Python 过滤出 `question_evolved == true` 的样本，再喂给 `collect_answers.py`：

```bash
python -c "
import sys, json
for line in sys.stdin:
    j = json.loads(line)
    if j.get('question_evolved'):
        print(json.dumps(j, ensure_ascii=False))
" < data/questions_evolved.jsonl > data/questions_evolved_only.jsonl

python collect_answers.py --input data/questions_evolved_only.jsonl --output data/questions_evolved_only_with_answers.jsonl ...
```

---

## 6. 各脚本参数速查

### scoring.py

自由回答模式采用分阶段非对称协议。Round 0 中，Qwen 与 GPT 各生成 3 个
独立回答，并分别只对自己的回答评分 2 次；GPT 结果仅记录。进化后的题目中，
Qwen 与 GPT 仍各回答 3 次：Qwen 对自身每份回答评分 2 次，GPT 对自身每份
回答评分 2 次，同时对每份 Qwen 回答复评 2 次。Qwen 不评分 GPT 回答。
顶层 `score_rate` 始终只由“Qwen 回答 + Qwen 自评”的全部 repeat 聚合得到；
GPT 对 Qwen 的复评写入 `gpt_score_summary`，GPT 自身回答与自评分开写入
`gpt_answer_generation_summary`、`gpt_answer_score_summary` 和 trial 明细。
原始 judge 响应写入 `*.judge_traces.jsonl.gz`，校验信息登记在
`*.manifest.json`。未进化透传样本仍完全复用已有评分，不发起任何新请求。

所有正式阶段产物现在都由统一发布器生成。运行中先写
`*.partial` 与 `*.checkpoint.jsonl`，阶段完成后再原子发布正式 JSONL 和
`*.manifest.json`；编排脚本只会跳过摘要、记录数、输入和 sidecar 校验均通过的
产物。中断后直接以同一输入和配置重跑即可从已确认记录或候选组继续。每轮的
解析、计算、恢复、批量 flush、队列峰值、请求池峰值和 RSS 指标追加到
`performance_events.jsonl`。

`--concurrency` 表示样本 worker 数。答案采集另用
`--request-concurrency` 控制真实在途请求；评分的 Qwen answer/Qwen judge
共享 `--qwen-max-concurrent` 请求池，GPT answer/GPT judge 共享独立的
`--gpt-max-concurrent` 请求池。提高 worker 数不会绕过请求池上限。

```bash
python scoring.py \
  --input INPUT.jsonl \
  --output OUTPUT.jsonl \
  --answer-mode {reference,llm} \
  --answer-base-url URL \
  --answer-api-key KEY \
  --answer-model MODEL \
  --judge-base-url URL \
  --judge-api-key KEY \
  --judge-model MODEL \
  --answer-trials 3 \
  --qwen-judge-repeats 2 \
  --gpt-judge-base-url URL \
  --gpt-judge-api-key KEY \
  --gpt-judge-model MODEL \
  --gpt-judge-repeats 2 \
  --gpt-answer-trials 3 \
  --gpt-answer-base-url URL \
  --gpt-answer-api-key KEY \
  --gpt-answer-model MODEL \
  --qwen-max-concurrent 20 \
  --gpt-max-concurrent 20 \
  --concurrency N \
  --retries N
```

### profile / select / router

```bash
python profile_samples.py --input scored.jsonl --output profiled.jsonl --model "$PROFILE_MODEL" --base-url "$PROFILE_BASE_URL"
python select_evolution_candidates.py --input profiled.jsonl --output profiled_candidates.jsonl --high-score-threshold 0.8
python operator_router.py --input profiled_candidates.jsonl --output routed.jsonl --memory-dir memory
```

### question_evolution.py

```bash
python question_evolution.py \
  --input routed.jsonl \
  --output candidates.jsonl \
  --min-score-rate 0.8 \
  --model gpt-5.4 \
  --base-url "$EVOLVE_BASE_URL" \
  --concurrency 20 \
  --retries 3 \
  --prompt-version {v1,v2} \
  --num-candidates 2 \
  --max-candidate-budget 0 \
  --validation-retries 1 \
  --max-semantic-retry-attempts 2
```

### validate / select

```bash
python validate_evolved_question.py --input candidates.jsonl --output validated_candidates.jsonl --semantic-economy-mode enforce --validate-schema
python validate_difficulty_gain.py --input validated_candidates.jsonl --output difficulty_validated_candidates.jsonl --report-output difficulty_gain_report.json
python candidate_selection.py --input difficulty_validated_candidates.jsonl --output evolved.jsonl --invalid-output invalid_generation_cases.jsonl
```

### collect_answers.py

```bash
python collect_answers.py \
  --input INPUT.jsonl \
  --output OUTPUT_with_answers.jsonl \
  --samples 1 \
  --concurrency 100 \
  --request-concurrency 20 \
  --model gpt-5.4 \
  --base-url "$ANSWER_BASE_URL" \
  --retries 3
```

### gen_rubric.py

```bash
python gen_rubric.py \
  --input INPUT_with_answers.jsonl \
  --output OUTPUT_rubric.jsonl \
  --concurrency 30 \
  --model gpt-5.4 \
  --base-url "$RUBRIC_BASE_URL" \
  --prompt-version v4
```

### effect / state

```bash
python analyze_evolution_effect.py --before previous_scored.jsonl --input scored.jsonl --output effect_analysis.jsonl --matrix-output effect_matrix.jsonl --semantic-report-output semantic_economy_report.json
python update_sample_state.py --input effect_analysis.jsonl --output state_updated.jsonl --memory-dir memory
```

---

## 7. 调试建议

1. **先看少量样本**：不要直接跑整个数据集。先用 `head -n 5` 切一个小文件验证流程。
2. **检查进化质量**：重点看 `meta_info.prompt_old` → `prompt` 的变化是否合理，是否引入了外部未提供的知识。
3. **对比前后得分**：通过 `meta_info.stale_scoring_result.total_awarded/total_possible` 与新 `scoring_result` 对比，判断进化是否有效。
4. **查看失败文件**：每个脚本失败的数据会写入 `*.failed` 文件，里面包含错误信息。

---

## 8. 多算子分支搜索与效率优化

项目现已提供基于同一已评分父题的多算子横向分支搜索。默认的 `hybrid + live`
Router 只使用 LLM 返回并通过最小契约校验的冻结候选；Router 失败或没有合法候选
时才使用确定性回退。两条路径都不会自动把注册表其余算子追加到计划中。
同一父节点和算子使用稳定 `branch_id`，窗口按剩余边界名额、当前在途数和剩余候选数
动态领取。

启用方式：

```bash
SEARCH_MODE=multi_operator_branch
SEARCH_BRANCH_WINDOW=3
SEARCH_BOUNDARY_TARGET=5
SEARCH_PIPELINE_MODE=stream
SEARCH_ARTIFACT_RETENTION=compact
DEFER_GPT_EXPERIMENTAL_EVALUATION=true
SEARCH_OPERATOR_SORT_MODE=route
bash run_loop.sh
```

Windows 设置相同环境变量后运行 `.\run_loop.ps1`。

关键兼容开关：

```text
SEARCH_BRANCH_WINDOW=1                  恢复单分支窗口
SEARCH_PIPELINE_MODE=step               使用分步编排
SEARCH_ARTIFACT_RETENTION=full          调试时保留全部阶段文件
DEFER_GPT_EXPERIMENTAL_EVALUATION=false 恢复同步完整双 Judge
SEARCH_OPERATOR_SORT_MODE=route         恢复路由既有顺序
```

`SEARCH_PIPELINE_MODE=step` 复用按波次的阶段 CLI，`stream` 使用长期存活 worker、
有界队列和逐分支 checkpoint，并在一个分支完成决策后立即按动态窗口补位。两种模式
共享稳定分支 ID、状态归并和完整分支产物。

每个 live 路由都会记录 schema、route revision、provider 标识、策略版本和冻结候选的
无密钥指纹。恢复时，Router 产物、搜索状态、分支产物与 manifest 必须使用同一指纹；
模型、Memory、运行策略或路由版本变化时请新建实验目录，而不是复用旧目录。

评分支持 `complete`、`decision` 和 `experimental` 三种模式。`decision` 只发布
Qwen 在线决策检查点；`experimental` 从检查点补齐 GPT 对 Qwen 回答的复评和 GPT
自回答/自评。仅当 `DEFER_GPT_EXPERIMENTAL_EVALUATION=true` 时搜索调度不等待 GPT；
正式实验结束仍要求完整 GPT 产物或明确记录原有容错失败。设为 `false` 会恢复同步
完整双 Judge。

`SEARCH_ARTIFACT_RETENTION` 默认值为 `compact`。在该模式下，stream 分支到达终态后
立即删除已经被正式分支结果和终态 checkpoint 覆盖的 `stream_branches/<branch>` 阶段
文件；逐阶段 checkpoint 始终只保留最新可恢复记录，完成后只保留 `final.json`，失败后
只保留 `branch_error.json`。step 模式的 `wave_*` 文件会保留到最终搜索状态及 sidecar
成功发布，随后统一删除。需要逐文件调试时可设为 `full`，其行为与旧版全部保留一致。

compact 模式保留的主要产物：

```text
search_state_updated.jsonl             轻量搜索状态
search/branch_results.jsonl            追加式完整分支产物
search/stream_checkpoints/*/final.json stream 模式成功分支终态检查点
search/stream_checkpoints/*/branch_error.json stream 模式失败分支终态检查点
search/search_summary.json             搜索与完整实验耗时、吞吐和延迟摘要
search/performance_events.jsonl        阶段和调度性能事件
```

运行期间不要手工删除 checkpoint。compact 只会在新的原子 checkpoint 已落盘后清理旧
快照；已确认候选仍从最新阶段继续，终态分支可从 `final.json` 或 `branch_error.json`
恢复。只有“已领取但没有确认产物”的分支才恢复为 pending。状态或分支产物版本不一致
时会拒绝在原实验目录混写。

性能验收至少重复三次：

```bash
python search_performance.py \
  --baseline baseline_run1/search_summary.json \
  --baseline baseline_run2/search_summary.json \
  --baseline baseline_run3/search_summary.json \
  --optimized optimized_run1/search_summary.json \
  --optimized optimized_run2/search_summary.json \
  --optimized optimized_run3/search_summary.json \
  --output performance_report.json
```

如需启用单位时间收益排序，先从追加式分支产物生成统计：

```bash
python operator_ranking.py \
  --input experiments/.../search/branch_results.jsonl \
  --output operator_statistics.json

SEARCH_OPERATOR_SORT_MODE=yield_per_time \
SEARCH_OPERATOR_STATISTICS=operator_statistics.json \
bash run_loop.sh
```

代码和离线等价性/恢复性测试已经接入。正式标记验收前仍需使用相同模型、Prompt、
trial/repeat、请求上限和重试策略完成 3 至 5 条真实样本灰度，并至少重复三次性能对照。

## 9. 纵向算子叠加搜索

项目支持在横向分支搜索之上启用 `multi_operator_vertical_stack`。根节点使用已完成准入后的
最终路由候选列表；只有相对直接父节点真实降分且通过原有完整闭环的子节点，才会成为
depth 2 frontier。每个 frontier 会基于当前题面、当前参考答案、Rubric、Score Prompt、
候选回答和评分结果重新画像并重新路由，绝不继承根节点 `operator_plan`。在线扩展不读取
人工 `review_status`，也不改变答案、Rubric、Score Prompt、双 Judge 或 Memory 的业务准入规则。

推荐的首轮实验配置：

```bash
SEARCH_MODE=multi_operator_vertical_stack
SEARCH_MAX_DEPTH=3
SEARCH_SINGLE_OPERATOR_BOUNDARY_TARGET=5
SEARCH_STACKED_OPERATOR_BOUNDARY_TARGET=5
SEARCH_TOTAL_BOUNDARY_HARD_CAP=10
SEARCH_BRANCH_WINDOW=1
SEARCH_ALLOW_OPERATOR_REPEAT_IN_PATH=false
bash run_loop.sh
```

Windows 使用相同环境变量运行 `.\run_loop.ps1`。`max_depth` 使用节点层级：root 为 1，
第一层算子子节点为 2，第二次叠加后的节点为 3。因此默认值 3 只允许一次真正的纵向叠加。
若只验证横向兼容性，可设置 `SEARCH_MAX_DEPTH=2` 与
`SEARCH_STACKED_OPERATOR_BOUNDARY_TARGET=0`。旧的 `SEARCH_BOUNDARY_TARGET` 仍是兼容 alias：
未设置三个分层参数时，它会同时提供单算子和叠加层目标，并将总上限设为两者之和。

单算子达到目标后，根节点不再领取新算子，但已登记 frontier 会按稳定顺序串行完成 depth 3
扩展。路径默认不重复使用算子；路由候选以当前节点的 LLM/规则最终结果为唯一来源，不会额外
枚举注册表中的启用算子。上游 `ASSIGNMENT_MODE=live` 可以用于根节点和 frontier 的 LLM 路由；
纵向调度器会保存该新路由的候选计划，并以自己的确定性账本执行它。

可选系统保护参数：

```text
SEARCH_MAX_REQUEST_ATTEMPTS_PER_SAMPLE=0  0 表示不额外设置尝试上限
SEARCH_MAX_EVALUATIONS_PER_SAMPLE=0       0 表示不额外设置评分节点上限
SEARCH_SAMPLE_TIMEOUT_SECONDS=0           0 表示不额外设置样本超时
```

纵向模式在 `search/` 下保存：

```text
vertical_search_checkpoint.jsonl  可恢复的轻量样本状态
vertical_nodes.jsonl              root 与所有完成完整评分闭环的节点及完整证据
operator_attempts.jsonl           每个 frontier 的逐算子尝试状态
boundary_edges.jsonl              直接父子降分边
boundary_paths.jsonl              有序算子路径及累计分数变化
vertical_search_summary.json      样本、算子、组合、终止和预算指标
parents/                          各 frontier 复用横向完整闭环的恢复产物
parents/*/frontier_profiled_parent.jsonl  frontier 的可恢复新画像
```

稳定 `node_id` 同时作为横向 `branch_id`，因此现有 Memory 写入继续使用
`node_id + memory_type` 幂等，不会因断点恢复重复追加。达到 `max_depth` 的降分节点仍保留为
`boundary_candidate`，但不会进入下一层；它不会把样本终止原因伪装成
`max_depth_reached`。正常业务终止会明确区分 `operator_space_exhausted`、
`single_operator_boundary_target_reached`、`stacked_operator_boundary_target_reached` 和
`total_boundary_hard_cap_reached`；请求、评分、超时、恢复和致命错误使用独立系统终止原因。
