# 02. K×M×R 算子实验方案


> 文档版本：v1.0  
> 制定日期：2026-07-11  
> 项目：Question_Evolution / PoliceQA 题目难度进化  
> 目标弱模型：Qwen3.6-27B  
> 强模型：GPT-5.4  
> 设计基线：仓库 `main` 当前主流程、`后续思路整理.md`、`样本分类画像.md` 以及本轮已确认决策。



## 1. 方案定位

K×M×R 不是简单“多生成、多评分”，而是把评估单位从单道候选题升级为一次 operator 批次实验。

- `K`：同一父节点、同一 operator 独立生成的候选数量；首轮固定 3。
- `M`：通过硬校验、事实检查和去重后进入真实压测的候选数量；首轮固定 2。
- `R`：每道候选独立重新作答并评分的 trial 数；首轮正式值 3，必要时扩展到 5。

## 2. 一期固定规则

```text
K = 3
M = 2
R_initial = 1
R_confirmed = 3
R_max = 5
concurrency = 10
```

- `R=1` 仅产生 `provisional_positive_signal`；
- `R>=3` 且候选 `score_mean < parent_score_mean`，产生 `effective_score_drop_signal`；
- 暂不设置最低降分幅度；
- 两个 M 候选均扩展到 R=3，用于验证算子稳定性；
- 固定参考答案和 rubric；每个 trial 都重新生成 Qwen 答案，再用 Qwen judge 评分；
- Seed 不确定，不作为独立性保证。

## 3. 两种实验模式

### 3.1 Forced Qualification

输入 `forced_operator_manifest.jsonl`：

```text
样本 + 指定 operator + 适配性理由 + Prompt 版本 + K/M/R
```

要求：

- 只对适配样本强制；
- 不要求一个样本测试全部 operator；
- 单样本首轮最多分配给 2 个 operator；
- 保存 Router shadow result；
- 使用隔离 Memory 和报告。

### 3.2 Natural Router Validation

- 使用 Router 原生 primary/backup；
- 不允许 forced override；
- 使用独立 holdout；
- 与 forced 模式保持同样 K/M/R 和评分配置。

## 4. 执行漏斗

```text
父节点 P
→ assignment 确定 operator
→ 同 operator 独立生成 K=3
→ 复杂度/可回答性/题外事实/泄漏硬校验
→ 结构化去重和表面重复检查
→ 预选择 M=2
→ 每道评分 1 次
→ 两道均补齐到 R=3
→ 高波动、边界关键或准备入库者补齐到 R=5
→ 评分后最终选择主链
→ 记录 operator 批次效果
```

## 5. K 个独立候选如何生成

当前项目需要增加 `generation_replica_id`。候选主键建议：

```text
candidate_id =
sample_id / parent_node_id / operator_id / prompt_version / replica_index
```

三个 replica 必须：

- 使用相同父题、operator 和 Prompt recipe；
- 分别调用生成模型；
- 不允许将上一道候选文本作为下一道候选输入；
- 保存每次 raw response 和 generation config；
- 若 API seed 不确定，可改变采样过程，但不得宣称统计独立。

## 6. K → M 的预选择

预选择只处理质量和差异性，不根据真实弱模型分数选题。

### 6.1 硬拒绝

- 不可回答；
- 关键事实来源不明；
- 题外事实依赖；
- 直接泄漏答案路径；
- 多主轴；
- 主要靠复杂格式制造难度；
- 明显偏离原题能力轴；
- GPT-5.4 保护性作答明确无法完成。

### 6.2 差异性预选

初期采用结构化字段，不依赖复杂 embedding：

```text
target_subclaim
surface_form_family
new_fact_map
question_shape
expected_qwen_failure
```

若两个候选仅实体、数字或措辞不同，则只保留质量更高者。

### 6.3 M 不足时

- 合格候选 2 道及以上：保留 2 道；
- 只有 1 道：保留 1 道并标记 `preselection_shortfall`；
- 0 道：本 operator 批次失败，回父节点或换 operator；
- 不为了凑 M 强行放行硬风险候选。

## 7. 通用评分包

建议抽象 `stability_evaluation.py`，从 Round 0 稳定评分机制中复用：

```json
{
  "evaluation_bundle_id": "eval_...",
  "node_id": "...",
  "trial_target": 3,
  "trial_completed": 3,
  "score_mean": 0.76,
  "score_std": 0.05,
  "score_min": 0.70,
  "score_max": 0.82,
  "answer_model": "qwen3.6-27b",
  "judge_model": "qwen3.6-27b",
  "reference_hash": "...",
  "rubric_hash": "...",
  "score_prompt_hash": "...",
  "config_hash": "..."
}
```

### 7.1 缓存键

至少包含：

```text
node prompt hash
reference hash
rubric hash
score prompt hash
answer model
judge model
temperature
top_p
trial index
评分代码版本
```

### 7.2 父题评分解释

父题不需要每次重复跑。规则是：

```text
已有同配置且 trial 数足够
→ 复用 evaluation bundle

配置不同或 trial 数不足
→ 只补缺少的 trial
```

父题与候选必须在同一配置下比较，否则平均分差不可解释。

## 8. 一期效果计算

### 8.1 候选级

```text
score_drop_mean = parent_score_mean - candidate_score_mean
```

状态：

```text
R=1 且 score_drop_mean > 0
→ provisional_positive_signal

R>=3 且 score_drop_mean > 0
→ effective_score_drop_signal

R>=3 且 score_drop_mean <= 0
→ effect_failure / no_drop
```

### 8.2 Operator-样本级

```text
candidate_positive_count
operator_batch_candidate_mean
operator_batch_drop
```

其中：

```text
operator_batch_drop
= parent_score_mean
- mean(M 个合格候选的 score_mean)
```

一期不据此自动淘汰 operator，只作为报告证据。

### 8.3 观测但不硬门禁的指标

- `score_std`；
- `score_range`；
- `median_score_drop`；
- `candidate_valid_rate`；
- `candidate_positive_rate`；
- `hard_failure_distribution`；
- `within_batch_diversity`；
- GPT-5.4 保护性分数，0.85 作为监控线。

## 9. 评分后最终选择

最终选择必须发生在 M 道候选获得真实评分后。

一期排序建议保持简单：

1. 硬校验通过；
2. `R>=3`；
3. `score_mean` 低于父题；
4. 平均分更低者优先；
5. 若平均分接近，优先 `score_std` 更小者；
6. 若仍接近，优先 prompt 更短、事实变化更少者；
7. GPT-5.4 无法回答者不得入选。

暂不引入复杂加权总分。

## 10. 强模型保护检查

最终入围候选由 GPT-5.4 回答一次：

- 明确无法回答、事实不足或多解：硬失败；
- Qwen judge 对 GPT-5.4 回答低于 0.85：记录 `strong_model_monitor_warning`，进入人工复核，不立即统一拒绝；
- 正常回答：继续。

保护检查不能替代 Qwen 多次评分，也不参与 operator 主排名。

## 11. 调用预算

### 11.1 单个 operator-sample 对

M=2、两道均 R=3：

```text
候选回答与评分：2 × 3 × 2 = 12 次 Qwen 调用
父题若无缓存：3 × 2 = 6 次 Qwen 调用
合计最高约 18 次 Qwen 调用
```

不含 GPT 生成候选、参考答案和保护性作答。

### 11.2 建议档位

| 档位 | 规模 | 适用目的 |
| --- | ---: | --- |
| 冒烟 | 每 operator 1–2 个适配样本 | 检查 Prompt/脚本是否明显失效 |
| Pilot | 每 operator 尽量 3 个适配样本 | 初步判断 O10–O18 是否有潜力 |
| 场景泛化 | 每 operator 5–6 个，多场景 | 判断 scene-limited 与跨场景效果 |
| 扩大验证 | 90 个以上 operator-sample 对 | 构建初步 operator-scene 矩阵 |

首轮推荐 Pilot。若某 operator 找不到 3 个强适配样本，照实标记 `insufficient_evidence`。

## 12. 失败归因

必须区分：

```text
assignment_mismatch
生成后发现样本并不适合该 operator；不计入效果失败分母。

generation_failure
operator 适配，但 Prompt 未正确执行，或题目硬校验失败。

effect_failure
样本适配、题目合格，但 R>=3 后平均分没有下降。
```

这三类不能合并成“operator 失败”。

## 13. 产物

```text
candidates_k.jsonl
preselected_candidates_m.jsonl
candidate_evaluation_trials.jsonl
evaluation_bundles.jsonl
final_selected_candidates.jsonl
forced_operator_validation_report.json
natural_router_validation_report.json
forced_vs_router_comparison.json
```

## 14. 脚本改造

```text
question_evolution.py
  --generation-replicas-per-operator 3
  --assignment-mode forced|router

candidate_preselection.py
  --keep-per-operator 2

stability_evaluation.py
  --initial-trials 1
  --confirmed-trials 3
  --max-trials 5

candidate_final_selection.py
  --parent-bundles ...
  --candidate-bundles ...
```

`candidate_selection.py` 可保留兼容入口，内部转发到 preselection 或 final selection。

## 15. 验收标准

- 同一 operator 对同一父题能生成 3 个独立候选记录；
- 预选择不使用真实评分结果；
- 两个 M 候选均可达到 R=3；
- 父题缓存可复用，配置不一致时不能误复用；
- 每个 trial 都有新 Qwen 答案；
- R=1 不写强成功 Memory；
- 最终选择发生在评分之后；
- Forced/Natural 报告和 Memory 隔离；
- assignment、generation、effect 三类失败可分开统计；
- 断点续跑不会重复写 trial 或 Memory。
