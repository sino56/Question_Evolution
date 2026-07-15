# 04. 调整后的候选生成与 K×R 实验方案

## 1. 调整原因

原 K×M×R 思路中，候选在真实评分前经过硬校验和 K→M 预选，容易导致：

- 有效边界题在真实评分前被误拒绝；
- 无法计算 validator 的 false reject；
- 未入选候选没有真实效果数据；
- operator 批次效果被静态预测替代。

因此，建议先实现 K×R，再根据首次真实评分结果执行评分后晋级。

## 2. 核心定义

- `K`：同一父节点、同一 operator 独立生成的候选数；
- `R_initial`：每个非致命候选首次评分次数；
- `R_confirmed`：需要确认的候选累计评分次数；
- `R_max`：高波动或关键边界候选最大评分次数。

推荐一期参数：

```text
K = 3
R_initial = 1
R_confirmed = 3
R_max = 5
```

## 3. 调整后的执行流程

```text
父节点
→ operator
→ 独立生成 K=3
→ fatal check
→ 所有非致命候选完成 R=1
→ 计算 provisional score drop
→ 选择确认候选
→ 补齐到 R=3
→ 必要时扩展到 R=5
→ 评分后最终选择
```

## 4. 候选独立生成

K 个候选必须：

- 使用相同父题；
- 使用相同 operator；
- 使用相同 Prompt version；
- 分别调用生成模型；
- 不把上一道候选作为下一道候选输入；
- 保存各自 raw response；
- 使用独立 `generation_replica_id`；
- 不宣称随机 seed 能保证统计独立。

候选 ID 建议：

```text
candidate_id =
sample_id / parent_node_id / operator_id / prompt_version / replica_index
```

## 5. 首次真实评分覆盖

核心不变量：

```text
任何非致命候选都至少完成 R_initial=1
```

不能因为以下原因跳过首次评分：

- 多主轴；
- 题目较长；
- validator 预测难度不足；
- possible match；
- 事实映射不完整；
- 与历史表面形态相似；
- GPT 保护性分数较低但仍可作答。

## 6. 评分后确认候选选择

K=3 全部完成 R=1 后，再选择部分候选补齐 R=3。

推荐保留两个确认名额：

### 6.1 exploitation candidate

选择当前真实 score drop 最大的候选。

### 6.2 exploration candidate

从以下集合中选择一个：

- validator 风险高但非致命；
- 与 exploitation 的表面形态不同；
- score 接近但波动较大；
- GPT 预测较差但真实结果不确定；
- 随机候选；
- 不同 target subclaim 或 boundary hypothesis。

这样既利用当前最佳结果，也保留对 validator 和搜索策略的校准能力。

## 7. 状态定义

### R=1

```text
score_drop > 0
→ provisional_positive_signal

score_drop <= 0
→ provisional_no_drop
```

### R>=3

```text
candidate_score_mean < parent_score_mean
→ confirmed_positive_signal

candidate_score_mean >= parent_score_mean
→ confirmed_no_drop
```

### R=5

用于：

- score_std 过高；
- 候选准备进入 boundary bank；
- 父题与候选均接近阈值；
- 不同 trial 结论明显冲突；
- 人工指定的重要样本。

## 8. 效果指标

候选级：

```text
score_drop_mean
score_std
score_min
score_max
positive_trial_count
```

operator 批次级：

```text
candidate_count
fatal_count
r1_completed_count
r3_completed_count
positive_candidate_count
batch_mean_drop
best_candidate_drop
exploration_candidate_result
```

观察指标：

```text
validator_false_reject
within_batch_diversity
cost_per_positive_candidate
```

## 9. 最终选择

候选完成 R=3 后，主链候选选择优先级：

1. 无致命错误；
2. 配置一致；
3. `score_mean < parent_score_mean`；
4. score drop 更大；
5. score_std 更小；
6. 与已有边界差异更大；
7. 题目更短、事实变化更少可作为次级偏好；
8. 后验质量风险不影响实验价值，但影响是否进入正式数据集。

## 10. GPT 强模型保护检查

GPT-5.4 保护性作答只能用于：

- 判断明确不可回答；
- 标记事实不足；
- 标记明显多解；
- 支持后验人工复核。

不能：

- 替代 Qwen 真实评分；
- 直接参与 operator 主排名；
- 因分数低于固定阈值统一拒绝所有候选。

## 11. 成本控制

如果 K=3 全部 R=3 成本过高，可采用：

```text
3 个候选全部 R=1
→ 2 个候选补齐 R=3
→ 1 个最佳或高波动候选补齐 R=5
```

预算不足时，不应退回评分前静态筛选。

## 12. 验收标准

1. 每个非致命候选至少有一次真实评分；
2. K 个候选均可独立追踪；
3. R=1、R=3、R=5 状态明确；
4. exploitation 与 exploration 候选逻辑可审计；
5. 最终选择发生在真实评分后；
6. 可统计 validator false reject；
7. 父题缓存可以复用；
8. 同配置重复运行不会重复生成 trial。
