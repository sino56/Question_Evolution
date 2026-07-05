# Round0 稳定评分改造方案 V1.1

## 1. 背景与核心问题

当前题目进化流水线中，round0 的单次评分结果会直接影响样本是否进入后续进化阶段。现有问题主要集中在：

1. 同一题、同一 rubric、同一 score prompt 下，Qwen 每次回答覆盖点不同。
2. 单次 Qwen 作答 + 单次 judge 评分会把后续进化准入建立在随机波动上。
3. 某些样本在一次实验中得分低于进化阈值，但在其他实验中可以被压出能力边界。
4. 当前多个下游模块会读取顶层 `score_rate`、`scoring_result.candidate_answer`，如果 round0 稳定评分后不重定义这些字段，下游仍可能误用某一次随机 trial。
5. 如果稳定探测直接循环调用现有 scoring 流程，可能因为已有 `scoring_result.candidate_answer` 被复用，导致多次 trial 实际评的是同一个答案。
6. 当前候选预算主要由全局 `--num-candidates` 控制，如果 round0 输出的 `recommended_evolution_budget` 不被 question evolution 阶段消费，该字段不会真正影响后续流程。

因此，round0 改造不能只新增统计摘要，还必须把稳定评分结果贯通到下游字段语义、代表性答案、候选预算和准入逻辑中。

---

## 2. 改造目标

本阶段目标是让 round0 从“单点分数”升级为“稳定评分基线”，并保证旧模块不会继续误用随机 trial。

具体目标：

1. 对每个原始样本进行多次 Qwen 作答与 judge 评分。
2. 默认 3 次 trial，边界或高波动样本追加到 5 次。
3. 输出完整 trial 列表和聚合后的 `round0_score_summary`。
4. 将顶层 `score_rate` 明确重定义为 `stable_score`。
5. 将顶层 `scoring_result` 设置为最接近 `stable_score` 的代表性 trial。
6. 保证每个 trial 都重新生成答案，而不是复用已有 `candidate_answer`。
7. 输出 `representative_round0_answer`，并与顶层 `scoring_result.candidate_answer` 保持一致。
8. 输出可被后续阶段消费的 `recommended_evolution_budget`。
9. 保留 legacy fallback，使旧数据没有 `round0_score_summary` 时仍可运行。
10. 增加成本与稳定性验收指标，判断参数是否过松或过紧。

---

## 3. 非目标

本阶段不解决以下问题：

1. 不判断进化题是否比原题更难。
2. 不做候选题 difficulty gain validation。
3. 不做进化失败回退。
4. 不重构完整 tree search。
5. 不改变 judge 的 rubric 评分规则。
6. 不修改 question_evolution.py 的核心生成 prompt。

本阶段只负责提供稳定、可复用、可向下游贯通的 round0 基线。

---

## 4. 总体流程

原始流程：

```text
原始题 → Qwen 作答 1 次 → judge 评分 1 次 → 得到 score_rate → 按阈值决定是否进化
```

改造后：

```text
原始题
  ↓
默认执行 3 次 Qwen 作答 + judge 评分
  ↓
计算 score summary
  ↓
如果处于边界区间或波动较大，追加 2 次
  ↓
最多形成 5 次 trial
  ↓
计算 stable_score、admission_score、volatility_level、admission_status
  ↓
选择最接近 stable_score 的代表性 trial
  ↓
重写顶层 score_rate = stable_score
  ↓
重写顶层 scoring_result = 代表性 trial 的 scoring_result
  ↓
写入 round0_score_trials 与 round0_score_summary
  ↓
下游 profile、candidate selection、question evolution 使用稳定后的字段
```

关键原则：

1. 顶层字段必须服务于旧模块兼容。
2. 完整 trial 信息必须保留，供分析和回溯。
3. 稳定基线和代表性答案必须对齐。
4. 每个 trial 必须重新生成答案。
5. 推荐候选预算必须被后续生成模块读取。

---

## 5. 顶层 score_rate 的新语义

### 5.1 必须重定义顶层 score_rate

round0 稳定评分完成后，顶层 `score_rate` 必须设置为：

```text
score_rate = round0_score_summary.stable_score
```

建议：

```text
stable_score = score_median
```

这样旧模块即使继续读取顶层 `score_rate`，读到的也是稳定基线，而不是某一次随机 trial 的分数。

### 5.2 原始单次分数不得放在顶层

每次 trial 的分数只放在：

```json
"round0_score_trials": [
  {
    "trial_id": 1,
    "score_rate": 0.7727
  },
  {
    "trial_id": 2,
    "score_rate": 0.8636
  }
]
```

不要把某一次 trial 的分数继续保留为顶层 `score_rate`。否则 `profile_samples.py`、`analyze_evolution_effect.py`、`select_evolution_candidates.py` 等模块可能继续误用随机分数。

### 5.3 保留原始字段备份

为了可回溯，建议在 `meta_info` 中记录稳定评分前的旧字段：

```json
"meta_info": {
  "pre_stability_score_rate": 0.7727,
  "pre_stability_scoring_result": {}
}
```

如果输入样本本来没有顶层 `score_rate`，则不写该字段。

---

## 6. 代表性答案与 scoring_result 对齐

### 6.1 必须选择代表性 trial

round0 稳定评分后，顶层 `scoring_result` 不能随便保留第一次、最后一次或最高分 trial。

应选择最接近 `stable_score` 的 trial 作为代表性 trial：

```text
representative_trial = argmin(abs(trial.score_rate - stable_score))
```

如果多个 trial 距离相同，按以下优先级选择：

1. rubric item 命中模式最接近所有 trial 的平均命中模式。
2. 答案长度处于中位附近，避免过短或异常冗长。
3. trial_id 更小者优先，保证确定性。

### 6.2 顶层 scoring_result 使用代表性 trial

稳定评分后：

```text
item.scoring_result = representative_trial.scoring_result
item.scoring_result.candidate_answer = representative_trial.candidate_answer
item.candidate_answer = representative_trial.candidate_answer
item.score_rate = stable_score
```

这样现有依赖 `scoring_result.candidate_answer` 的模块可以自然兼容。

### 6.3 新增 representative_round0_answer

同时建议写入：

```json
"representative_round0_answer": {
  "trial_id": 3,
  "score_rate": 0.8182,
  "candidate_answer": "...",
  "selection_reason": "closest_to_stable_score"
}
```

后续 `profile_samples.py` 和 `question_evolution.py` 可以继续使用顶层 `scoring_result.candidate_answer`，也可以显式读取该字段。

---

## 7. 每个 trial 必须重新生成答案

### 7.1 当前风险

现有评分流程在 answer-mode llm 下，如果样本已经有：

```json
"scoring_result": {
  "candidate_answer": "..."
}
```

就可能直接复用已有答案。稳定探测如果简单循环调用 `process_item`，可能 3–5 次 trial 实际评的是同一个答案。

这会导致 round0 stability probe 失效。

### 7.2 必须增加强制重新生成机制

建议在 scoring 相关逻辑中增加参数：

```text
force_generate_answer = true
```

或：

```text
ignore_existing_answer = true
```

在 round0 stability probe 中，每个 trial 都必须使用该参数。

### 7.3 兼容实现方式

如果短期不想改 scoring.py 的函数签名，可以在每个 trial 前创建干净副本：

```python
trial_item = copy.deepcopy(item)
trial_item.pop("candidate_answer", None)

if isinstance(trial_item.get("scoring_result"), dict):
    trial_item["scoring_result"] = {
        key: value
        for key, value in trial_item["scoring_result"].items()
        if key != "candidate_answer"
    }
```

更推荐的长期方式是在 scoring.py 中显式支持：

```python
process_item(
    item,
    answer_mode="llm",
    force_generate_answer=True,
)
```

### 7.4 trial 内部仍可复用 rubric 和 score prompt

每次 trial 只强制重新生成 candidate answer。  
rubric、score prompt、judge 解析逻辑必须保持一致，否则分数波动无法归因于 Qwen 作答差异。

---

## 8. 采样参数与可复现性记录

### 8.1 必须记录采样参数

每个 trial 必须记录：

```json
{
  "answer_model": "qwen-xxx",
  "judge_model": "gpt-xxx",
  "answer_temperature": 0.7,
  "answer_top_p": 0.95,
  "answer_seed": null,
  "judge_temperature": 0.0,
  "trial_id": 1
}
```

如果当前 answer 生成没有显式设置 temperature、top_p、seed，也要记录为：

```json
"answer_temperature": null,
"answer_top_p": null,
"answer_seed": null
```

避免将来无法解释实验差异。

### 8.2 CLI 参数建议

`round0_stability_probe.py` 建议支持：

```bash
--answer-temperature 0.7
--answer-top-p 0.95
--answer-seed-base 20260704
--judge-temperature 0.0
```

如果底层模型 API 不支持 seed，也应记录：

```json
"seed_supported": false
```

### 8.3 seed 设计

如果支持 seed，建议：

```text
trial_seed = answer_seed_base + trial_id
```

这样同一样本同一 trial 在可控模型上可复现。  
如果不支持 seed，则依赖自然采样差异，但必须记录采样参数和模型版本。

---

## 9. 复评次数策略

### 9.1 默认 trial 数

```text
INITIAL_TRIALS = 3
```

### 9.2 追加 trial 数

如果前 3 次结果不稳定或接近准入边界，追加 2 次：

```text
EXTRA_TRIALS = 2
MAX_TRIALS = 5
```

### 9.3 追加条件

满足任一条件则追加：

```text
0.72 <= score_median <= 0.83
```

或：

```text
score_std >= 0.06
```

或：

```text
score_range >= 0.15
```

或：

```text
score_max >= 0.85 and score_median < 0.80
```

或：

```text
full_score_count >= 1 and score_min <= 0.80
```

追加原因写入：

```json
"extra_trials_reason": [
  "score_median_near_threshold",
  "score_range_large"
]
```

---

## 10. 分数聚合规则

### 10.1 基础统计字段

对所有 trial 计算：

```text
score_mean
score_median
score_std
score_min
score_max
score_p75
score_range
full_score_count
high_score_count
trial_count
```

定义：

```text
score_range = score_max - score_min
full_score_count = count(score_rate >= 0.999)
high_score_count = count(score_rate >= 0.80)
```

### 10.2 stable_score

```text
stable_score = score_median
```

原因：

1. 抗离群。
2. 不会被一次异常低分误杀。
3. 不会被一次偶然满分抬高。
4. 适合作为后续进化效果比较基线。

### 10.3 admission_score

建议：

```text
admission_score = max(score_median, score_p75 - 0.03)
```

但必须配合 unstable_high 的附加约束，防止一次高分过度放大。

### 10.4 防止 p75 被单次高分放大

对于 3 或 5 个 trial，`score_p75` 可能被一个高分影响。  
因此不能仅凭：

```text
score_p75 >= 0.80
```

就判定为 unstable_high。

unstable_high 至少需要满足以下条件之一：

```text
high_score_count >= 2
```

或：

```text
score_p75 >= 0.80
and score_max >= 0.85
and score_median >= 0.70
```

这样可以避免类似：

```text
[0.62, 0.68, 0.81, 0.66, 0.64]
```

仅因一次 0.81 被送入正常预算。

---

## 11. 波动等级

定义：

```text
low:
  score_std < 0.04 and score_range < 0.10

medium:
  0.04 <= score_std < 0.08 or 0.10 <= score_range < 0.20

high:
  score_std >= 0.08 or score_range >= 0.20
```

如果分数样本数量不足 3，不进行稳定分类，直接标记：

```text
volatility_level = "insufficient_trials"
admission_status = "review_needed"
```

---

## 12. 样本分类规则

### 12.1 分类顺序

必须按以下顺序分类：

```text
stable_high
→ unstable_high
→ borderline_probe
→ stable_low
→ uncertain_low
→ review_needed
```

分类顺序很重要。  
如果不规定优先级，模糊样本容易被误归为 stable_low。

### 12.2 stable_high

稳定高分样本，正常进入进化。

条件：

```text
stable_score >= 0.80
and high_score_count >= ceil(trial_count / 2)
```

输出：

```json
"admission_status": "stable_high",
"recommended_evolution_budget": 2
```

如果系统默认候选数是 3，也可以设为 3。

### 12.3 unstable_high

波动高分样本，应进入进化，但需要标记波动。

条件一：

```text
stable_score < 0.80
and score_max >= 0.85
and score_p75 >= 0.80
and high_score_count >= 2
```

条件二：

```text
stable_score >= 0.70
and score_max >= 0.85
and score_p75 >= 0.80
and volatility_level in {"medium", "high"}
```

输出：

```json
"admission_status": "unstable_high",
"recommended_evolution_budget": 2
```

### 12.4 borderline_probe

边界探测样本，只给低预算。

条件：

```text
0.70 <= stable_score < 0.80
and score_max >= 0.80
```

或：

```text
0.70 <= admission_score < 0.80
and sample_profile 存在明确 overscore cause
```

输出：

```json
"admission_status": "borderline_probe",
"recommended_evolution_budget": 1
```

### 12.5 stable_low

稳定低分样本，不进入进化。

条件：

```text
stable_score < 0.70
and score_max < 0.80
and volatility_level == "low"
```

输出：

```json
"admission_status": "stable_low",
"recommended_evolution_budget": 0
```

### 12.6 uncertain_low

低分但存在一次较高分或中高波动，不直接进入正常进化。

条件示例：

```text
stable_score < 0.70
and score_max >= 0.80
```

或：

```text
stable_score < 0.70
and volatility_level in {"medium", "high"}
```

输出：

```json
"admission_status": "uncertain_low",
"recommended_evolution_budget": 0,
"needs_manual_review": true
```

对于类似：

```text
[0.62, 0.68, 0.81, 0.66, 0.64]
```

应归为 `uncertain_low`，而不是 `unstable_high` 或 `stable_low`。

### 12.7 review_needed

无法稳定分类的兜底类。

例如：

1. trial 数不足。
2. scoring_result 解析失败。
3. total_possible 为 0。
4. 分数字段异常。
5. rubric item 结构不一致。

输出：

```json
"admission_status": "review_needed",
"recommended_evolution_budget": 0,
"needs_manual_review": true
```

---

## 13. 输出数据结构

### 13.1 顶层字段

round0 稳定评分后，样本顶层必须写入：

```json
{
  "score_rate": 0.8182,
  "scoring_result": {
    "candidate_answer": "...",
    "total_awarded": 9,
    "total_possible": 11
  },
  "candidate_answer": "...",
  "round0_score_trials": [],
  "round0_score_summary": {},
  "representative_round0_answer": {}
}
```

其中：

```text
score_rate = stable_score
scoring_result = representative_trial.scoring_result
candidate_answer = representative_trial.candidate_answer
```

### 13.2 round0_score_trials

每个 trial：

```json
{
  "trial_id": 1,
  "answer_model": "qwen-xxx",
  "judge_model": "gpt-xxx",
  "answer_temperature": 0.7,
  "answer_top_p": 0.95,
  "answer_seed": 20260705,
  "judge_temperature": 0.0,
  "force_generate_answer": true,
  "candidate_answer": "...",
  "candidate_answer_hash": "...",
  "score_rate": 0.8182,
  "scoring_result": {
    "total_awarded": 9,
    "total_possible": 11,
    "candidate_answer": "...",
    "rubric_item_results": []
  },
  "rubric_item_awards": [1, 1, 0, 1],
  "created_at": "2026-07-04T00:00:00+09:00"
}
```

### 13.3 round0_score_summary

```json
{
  "trial_count": 5,
  "score_mean": 0.8018,
  "score_median": 0.8182,
  "score_std": 0.0571,
  "score_min": 0.7273,
  "score_max": 0.9091,
  "score_p75": 0.8636,
  "score_range": 0.1818,
  "stable_score": 0.8182,
  "admission_score": 0.8336,
  "full_score_count": 0,
  "high_score_count": 3,
  "volatility_level": "medium",
  "admission_status": "stable_high",
  "needs_extra_trials": true,
  "extra_trials_reason": [
    "score_median_near_threshold",
    "score_range_large"
  ],
  "recommended_evolution_budget": 2,
  "representative_trial_id": 3,
  "needs_manual_review": false
}
```

### 13.4 rubric_item_stability

```json
"rubric_item_stability": [
  {
    "item_index": 0,
    "award_mean": 1.0,
    "award_std": 0.0,
    "hit_count": 5,
    "miss_count": 0
  },
  {
    "item_index": 1,
    "award_mean": 0.4,
    "award_std": 0.49,
    "hit_count": 2,
    "miss_count": 3
  }
]
```

该字段用于后续分析 Qwen 哪些评分点覆盖不稳定。

---

## 14. 新增脚本设计

新增：

```text
round0_stability_probe.py
```

### 14.1 CLI 参数

```bash
python round0_stability_probe.py \
  --input data/data.jsonl \
  --output data/round0_stable_scored.jsonl \
  --initial-trials 3 \
  --extra-trials 2 \
  --max-concurrent 10 \
  --answer-model qwen-xxx \
  --judge-model gpt-xxx \
  --answer-temperature 0.7 \
  --answer-top-p 0.95 \
  --answer-seed-base 20260704 \
  --judge-temperature 0.0 \
  --score-threshold 0.80 \
  --edge-low 0.72 \
  --edge-high 0.83 \
  --force
```

### 14.2 核心函数

```python
async def run_answer_and_score_trial(item, trial_id, config) -> dict:
    ...

def compute_score_summary(trials: list[dict]) -> dict:
    ...

def needs_extra_trials(summary: dict) -> tuple[bool, list[str]]:
    ...

def select_representative_trial(trials: list[dict], stable_score: float) -> dict:
    ...

def classify_round0_admission(summary: dict, item: dict) -> dict:
    ...

def apply_round0_stable_top_level_fields(item: dict, trials: list[dict], summary: dict) -> dict:
    ...

async def process_item_with_stability_probe(item, config) -> dict:
    ...
```

### 14.3 与 scoring.py 的关系

优先复用 scoring.py 的答题和评分逻辑，但必须支持：

```text
force_generate_answer=True
```

如果 scoring.py 暂时不方便重构，round0_stability_probe.py 必须在 trial 前清理：

```text
candidate_answer
scoring_result.candidate_answer
```

避免答案复用。

---

## 15. select_evolution_candidates.py 改造

### 15.1 读取优先级

准入时按以下优先级读取：

```text
round0_score_summary.admission_status
round0_score_summary.recommended_evolution_budget
round0_score_summary.admission_score
round0_score_summary.stable_score
顶层 score_rate
```

### 15.2 推荐逻辑

```python
summary = item.get("round0_score_summary") or {}

if summary:
    status = summary.get("admission_status")
    if status in {"stable_high", "unstable_high"}:
        evolution_action = EVOLVE_HIGH_SCORE_OVERSCORE
    elif status == "borderline_probe":
        evolution_action = RECONSTRUCT_LOW_SCORE_BOUNDARY
    elif status in {"stable_low", "uncertain_low", "review_needed"}:
        evolution_action = STOP_EVOLUTION
    else:
        evolution_action = STOP_EVOLUTION
else:
    # legacy fallback
    score_rate = get_score_rate(item)
```

### 15.3 将预算写入 operator_route 或顶层 metadata

建议写入：

```json
"evolution_budget": {
  "recommended_num_candidates": 1,
  "source": "round0_score_summary",
  "admission_status": "borderline_probe"
}
```

也可以写入 `operator_route`：

```json
"operator_route": {
  "primary_operator": "O2",
  "backup_operators": ["O3"],
  "recommended_num_candidates": 1
}
```

---

## 16. question_evolution.py 候选预算贯通

### 16.1 当前问题

当前 question_evolution.py 主要读取全局：

```text
--num-candidates
--max-candidate-budget
```

如果不改造，`round0_score_summary.recommended_evolution_budget` 不会真正影响候选生成。

### 16.2 建议增加 per-item budget

在 `process_item_candidates` 中增加：

```python
def resolve_requested_candidates_for_item(item, default_num_candidates):
    summary = item.get("round0_score_summary") or {}
    budget = summary.get("recommended_evolution_budget")

    route = item.get("operator_route") or {}
    route_budget = route.get("recommended_num_candidates")

    value = route_budget or budget or default_num_candidates
    return max(0, int(value))
```

然后：

```python
candidate_count = requested_candidates or resolve_requested_candidates_for_item(item, self.num_candidates)
```

### 16.3 预算规则

```text
stable_high: 2 或 3 个候选
unstable_high: 2 个候选
borderline_probe: 1 个候选
stable_low: 0 个候选
uncertain_low: 0 个候选，必要时人工复核
review_needed: 0 个候选
```

这样 round0 的分类结果才能真正控制后续成本。

---

## 17. profile_samples.py 兼容规则

profile 阶段应优先使用：

```text
representative_round0_answer.candidate_answer
```

其次使用：

```text
scoring_result.candidate_answer
```

由于 round0 稳定评分后会将顶层 `scoring_result` 设置为代表性 trial，旧逻辑也能兼容。

建议在 profile 输出中记录：

```json
"profile_source": {
  "answer_source": "representative_round0_answer",
  "score_source": "round0_score_summary.stable_score"
}
```

---

## 18. analyze_evolution_effect.py 兼容规则

进化效果分析时，原题基线应优先使用：

```text
round0_score_summary.stable_score
```

而不是旧单次 score_rate。

但由于顶层 `score_rate` 已被重写为 stable_score，旧逻辑即使读顶层也不会出错。

建议新增：

```json
"baseline_score_source": "round0_score_summary.stable_score"
```

用于明确 score drop 的基线来源。

---

## 19. run_loop.sh 改造建议

推荐流程：

```text
Stage 0A: round0_stability_probe.py
Stage 0B: profile_samples.py
Stage 0C: select_evolution_candidates.py
Stage 0D: operator_router.py
Stage 1: question_evolution.py
```

输入输出示例：

```text
data/data.jsonl
  ↓
data/round0_stable_scored.jsonl
  ↓
data/round0_profiled.jsonl
  ↓
data/evolution_candidates.jsonl
  ↓
data/operator_routed.jsonl
  ↓
data/evolved_candidates.jsonl
```

---

## 20. 缓存策略

### 20.1 cache key

```text
cache_key = sha1(
  sample_id +
  prompt_hash +
  rubric_hash +
  score_prompt_hash +
  answer_model +
  judge_model +
  answer_temperature +
  answer_top_p +
  answer_seed +
  judge_temperature +
  round0_probe_version +
  trial_id
)
```

### 20.2 缓存内容

缓存：

1. candidate_answer
2. scoring_result
3. score_rate
4. answer_model
5. judge_model
6. sampling params
7. prompt hash
8. rubric hash
9. score prompt hash
10. created_at

### 20.3 force 参数

默认命中 cache 时复用。  
指定：

```bash
--force
```

时重新生成。

---

## 21. 成本与稳定性验收指标

除了功能验收，必须输出整体统计报告：

```json
{
  "total_samples": 1000,
  "average_trial_count": 3.42,
  "extra_trial_rate": 0.21,
  "estimated_cost_per_100_samples": "...",
  "classification_distribution": {
    "stable_high": 210,
    "unstable_high": 86,
    "borderline_probe": 140,
    "stable_low": 480,
    "uncertain_low": 62,
    "review_needed": 22
  },
  "legacy_vs_stable_admission": {
    "legacy_admit_count": 260,
    "stable_admit_count": 296,
    "rescued_by_stability": 48,
    "removed_by_stability": 12
  },
  "score_shift_summary": {
    "mean_abs_difference_between_legacy_score_and_stable_score": 0.041,
    "max_difference": 0.227
  }
}
```

### 21.1 必须关注的指标

1. 平均 trial 数。
2. 追加复评比例。
3. 每 100 条样本成本。
4. 各 admission_status 分布。
5. legacy 单次准入 vs stable 准入差异。
6. 被 stable 机制救回的样本数。
7. 被 stable 机制剔除的偶然高分样本数。
8. 典型样本，例如 6406，是否被归为 `unstable_high` 或 `borderline_probe`。
9. `uncertain_low` 和 `review_needed` 是否比例过高。
10. 稳定评分后进入进化的样本是否更符合人工预期。

### 21.2 参数调优依据

如果 `unstable_high` 过多，说明准入过松。  
如果 `stable_low` 过多且大量历史可压样本被排除，说明准入过紧。  
如果 `extra_trial_rate` 超过 40%，说明边界条件过宽，成本可能不可控。  
如果 `uncertain_low` 超过 15%，说明分类规则或阈值需要细化。

---

## 22. 测试用例设计

新增：

```text
tests/test_round0_stability_probe.py
```

### 22.1 稳定高分

输入：

```text
[0.82, 0.86, 0.84]
```

期望：

```text
stable_score = 0.84
admission_status = stable_high
needs_extra_trials = false
```

### 22.2 一次低分误差

输入：

```text
[0.91, 0.86, 0.73]
```

期望：

```text
needs_extra_trials = true
追加后若 high_score_count >= 2，则 stable_high 或 unstable_high
```

### 22.3 波动可压样本

输入：

```text
[0.77, 0.91, 0.74, 0.86, 0.79]
```

期望：

```text
volatility_level = medium 或 high
admission_status = unstable_high
```

### 22.4 稳定低分

输入：

```text
[0.55, 0.59, 0.61]
```

期望：

```text
admission_status = stable_low
recommended_evolution_budget = 0
```

### 22.5 边界探测样本

输入：

```text
[0.71, 0.78, 0.82, 0.76, 0.74]
```

期望：

```text
admission_status = borderline_probe
recommended_evolution_budget = 1
```

### 22.6 单次高分但整体偏低

输入：

```text
[0.62, 0.68, 0.81, 0.66, 0.64]
```

期望：

```text
admission_status = uncertain_low
recommended_evolution_budget = 0
needs_manual_review = true
```

### 22.7 顶层 score_rate 重写

输入 trial：

```text
[0.72, 0.82, 0.91]
```

期望：

```text
item.score_rate = 0.82
```

### 22.8 代表性答案对齐

输入：

```text
stable_score = 0.82
trial1.score_rate = 0.72
trial2.score_rate = 0.82
trial3.score_rate = 0.91
```

期望：

```text
item.scoring_result = trial2.scoring_result
item.scoring_result.candidate_answer = trial2.candidate_answer
item.representative_round0_answer.trial_id = 2
```

### 22.9 每次 trial 强制重新生成

构造已有 `scoring_result.candidate_answer` 的样本，执行 3 次 trial。  
期望：

```text
每个 trial 都调用 answer generation
trial candidate_answer hash 不完全相同，或至少 generation_call_count = 3
```

### 22.10 候选预算贯通

输入：

```json
"round0_score_summary": {
  "admission_status": "borderline_probe",
  "recommended_evolution_budget": 1
}
```

期望：

```text
question_evolution.py 实际只生成 1 个候选
```

### 22.11 legacy fallback

没有 `round0_score_summary` 时，select_evolution_candidates.py 仍使用原来的 `score_rate` 逻辑。

---

## 23. 验收标准

本阶段完成后，应满足：

1. 每条 round0 输出样本都有 `round0_score_trials` 和 `round0_score_summary`。
2. 每条样本至少 3 次 trial，边界或波动样本最多 5 次。
3. 顶层 `score_rate` 必须等于 `round0_score_summary.stable_score`。
4. 顶层 `scoring_result` 必须来自最接近 stable_score 的代表性 trial。
5. 顶层 `scoring_result.candidate_answer` 必须等于 `representative_round0_answer.candidate_answer`。
6. 每个 trial 必须强制重新生成 candidate answer，不能复用已有答案。
7. trial 中必须记录 answer/judge 模型和采样参数。
8. 分类规则必须包含 `uncertain_low` 和 `review_needed` 兜底类。
9. `unstable_high` 必须防止单次高分过度放大。
10. `recommended_evolution_budget` 必须被 question_evolution.py 实际消费。
11. `select_evolution_candidates.py` 优先读取 `round0_score_summary`，没有时才 fallback 到旧 score_rate。
12. `profile_samples.py` 和 `analyze_evolution_effect.py` 读取到的 score 与 answer 必须是稳定后的代表性字段。
13. 输出成本与稳定性统计报告。
14. 小样本实验中，6406 这类单次低分但可压样本应能进入 `unstable_high` 或 `borderline_probe`，而不是被直接停止。
15. 所有新增逻辑有单元测试覆盖。

---

## 24. 推荐实施顺序

### 第一步：实现纯函数

先实现并测试：

```text
compute_score_summary
needs_extra_trials
classify_round0_admission
select_representative_trial
apply_round0_stable_top_level_fields
```

### 第二步：改造 scoring.py 的复用逻辑

新增：

```text
force_generate_answer
ignore_existing_answer
```

保证 round0 stability probe 每个 trial 都重新生成答案。

### 第三步：实现 round0_stability_probe.py

完成多 trial 评分、追加复评、summary 输出、代表性 trial 写回。

### 第四步：改造 select_evolution_candidates.py

优先读取 `round0_score_summary`，并写入 evolution budget。

### 第五步：改造 question_evolution.py

让 `recommended_evolution_budget` 真正控制每条样本的候选生成数。

### 第六步：改造 profile_samples.py 和 analyze_evolution_effect.py

显式优先读取 stable score 和 representative answer。

### 第七步：改造 run_loop.sh

把 round0 单次评分替换为稳定评分阶段。

### 第八步：跑小样本校验

先跑 20–50 条，检查：

1. trial 数是否合理。
2. 追加复评比例是否合理。
3. 6406 类样本是否被救回。
4. `uncertain_low` 是否过多。
5. 下游是否读到 stable_score 和 representative answer。
6. borderline_probe 是否只生成 1 个候选。

---

## 25. 最终原则

这次改造的关键不是“多跑几次评分”，而是让整个流水线从此不再依赖随机 trial。

必须做到：

1. 顶层 `score_rate` 是稳定基线。
2. 顶层 `scoring_result` 是代表性 trial。
3. 每个 trial 都重新生成答案。
4. 准入使用分数分布，而不是单点分数。
5. 候选预算从 round0 分类传递到 question evolution。
6. 成本与稳定性指标可观测。

只有这几项贯通，round0 稳定评分才真正生效。
