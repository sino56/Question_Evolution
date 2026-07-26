# Qwen/GPT 非对称多回答评测方案

- **文档版本**：v3.0
- **修订日期**：2026-07-18
- **项目**：Question_Evolution / PoliceQA 题目难度进化
- **读者**：维护评分协议、调整模型调用配置或分析评分产物的内部工程师。
- **读后可执行的动作**：正确配置 Round 0 与进化后两套非对称评测协议，并明确区分 Qwen 在线分数与 GPT 实验记录。

## 1. 目标与边界

非对称评测用于在保持既有 Qwen 在线评分口径的前提下，同时观察 Qwen 与 GPT 对题目的作答表现。GPT 既会生成自己的回答并自评，也会在进化后复评 Qwen 回答；这些结果全部只做实验记录，不参与主流程决策。

当前实现的边界如下：

- Round 0 中，Qwen 和 GPT 各生成 3 份独立回答，并且只评分各自的回答，每份回答评分 2 次；
- 进化后，Qwen 和 GPT 各生成 3 份独立回答；Qwen 每份回答自评 2 次，GPT 每份回答自评 2 次，同时 GPT 对每份 Qwen 回答复评 2 次；
- Qwen 绝不评分 GPT 回答；
- 只有“Qwen 回答 + Qwen judge”的全部成功评分均值写入在线 `score_rate`；GPT 自评及 GPT 对 Qwen 的复评均为实验结果；
- 每次评分都使用题目当前的 rubric 和 score prompt，评分前只将候选回答填入评分占位符；
- 多 trial、多次评分和并行调度只改变采样与执行方式，不改变题目进化、效果分析、状态更新、停止条件和 memory 写入所依据的业务规则；
- 未进化的透传样本沿用已有回答、rubric 和评分结果，不重新调用任一 answer 或 judge 服务。

## 2. 当前评测流程

Round 0 与进化后使用不同的评测矩阵，默认次数如下：

| 阶段 | 回答来源 | Judge | 每题回答数 | 每份回答评分数 | 是否影响流程 |
| --- | --- | --- | ---: | ---: | --- |
| Round 0 | Qwen | Qwen | 3 | 2 | 是，唯一在线分数 |
| Round 0 | GPT | GPT | 3 | 2 | 否，仅记录 |
| Round 0 | Qwen | GPT | 0 | 0 | 不执行 |
| 进化后 | Qwen | Qwen | 3 | 2 | 是，唯一在线分数 |
| 进化后 | Qwen | GPT | 3 | 2 | 否，仅记录 |
| 进化后 | GPT | GPT | 3 | 2 | 否，仅记录 |
| 任意阶段 | GPT | Qwen | 0 | 0 | 禁止执行 |

因此，每个样本在默认配置下，Round 0 发起 6 次回答调用和 12 次评分调用，共 18 次；进化后发起 6 次回答调用和 18 次评分调用，共 24 次。回答 trial 和评分 repeat 均独立调用，并按 `answer_source`、`trial_index`、`judge_source`、`repeat_index` 归位，网络完成顺序不会改变聚合结果。

## 3. 多 trial 与非对称评分

### 3.1 候选回答与评分材料

在自由回答模式下，Qwen 与 GPT 分别为同一题目生成多个独立回答。每个回答都是独立 trial，不能以一次回答重复计数。评分阶段为对应回答填充题目专属的 score prompt，再按上表路由到允许的 judge；所有 judge 均按同一 rubric 标题和权重规范化逐项得分。

Qwen/GPT answer model 均只接收待回答题目，不接收 reference、rubric、score prompt、算子、预期失败模式或历史评分等评测上下文。Qwen judge 只会看到 Qwen 回答；GPT judge 在 Round 0 只看 GPT 回答，在进化后分别评分 GPT 回答与 Qwen 回答。

### 3.2 独立重复与失败语义

每个 judge 的每次 repeat 都是一次独立网络调用，并沿用已有的解析、rubric 标题对齐和有限重试逻辑。Qwen 自评是必需结果：任一必需 Qwen 评分在重试后仍失败时，该样本记录为评分失败，不发布为完整评分产物。GPT 回答生成、GPT 自评和 GPT 对 Qwen 的复评都是实验结果：失败时记录错误，不计入相应均值，也不取消已经成功的 Qwen 在线评分。

这种区分的目的，是避免实验性外部复评的短暂不可用阻断主流程，同时禁止以缺失的 Qwen 评分生成看似完整的在线分数。

## 4. 聚合与兼容输出

### 4.1 Qwen 是唯一在线决策分数

每个 trial 先计算该 trial 内全部 Qwen repeat 的平均得分率；随后汇总所有 trial 的 Qwen repeat，生成 qwen_score_summary 的数量、均值、最小值和最大值。该总均值乘以 rubric 正向总分后写入顶层总分，再计算顶层 score_rate。

后续画像、候选分流、效果分析、状态更新、回滚与停止条件读取的都是这个 Qwen 派生的 score_rate。GPT 分数既不会与 Qwen 平均，也不会覆盖顶层总分或触发任何在线状态变化。这样保持了跨轮决策口径的单一性，避免外部实验信号改变已有进化策略。

### 4.2 代表 trial

多 trial 结果需要继续兼容只读取单份 candidate_answer、逐项得分和总体评价的下游逻辑。实现会选择 Qwen trial 均分最接近该样本 Qwen 总均分的回答；若距离相同，则选择较小的 trial 编号。顶层回答、逐项得分、总体评价和代表 Qwen 原始响应均投影自该 trial 的首个 Qwen repeat，而顶层总分和 score_rate 仍来自全部 Qwen repeat 的聚合。

这种分离使顶层记录既能代表典型回答，又不会把一次偶然的 trial 分数误当成整个样本的在线分数。

### 4.3 GPT 实验汇总

GPT 实验结果分为两条互不混算的轨道：

- `gpt_score_summary` / `qwen_answer_gpt_score_summary`：GPT 对 Qwen 回答的复评；Round 0 请求数为 0，进化后默认汇总 6 次评分；
- `gpt_answer_score_summary`：GPT 对自身回答的评分，默认汇总 6 次评分；
- `gpt_answer_generation_summary`：GPT 回答生成成功、失败与数量统计；
- `scoring_result.gpt_answer_trials`：GPT 回答及其 GPT judge repeat 明细；Round 0 另保留 `round0_gpt_answer_trials`。

每条轨道只对自身成功评分求均值。GPT 全部失败时对应均值为空，而 Qwen 决策仍可正常输出。这些汇总不会生成流程标签，也不会改变 memory 写入条件。

## 5. 受控并行与服务额度

评分运行将样本 worker、Qwen 请求池和 GPT 请求池分开配置。Qwen 回答请求与 Qwen judge 请求共享同一全局池；GPT 回答请求、GPT 自评及 GPT 对 Qwen 的复评共享独立 GPT 池。两类服务的默认在途上限均为 20，但应按实际服务的并发、QPM 和 token 限制调整。

```
评分样本 worker
        |
        +--> Qwen 回答 ------> Qwen 公平请求池
        |
        +--> Qwen judge ----> 同一个 Qwen 公平请求池
        |
        +--> GPT 回答 ------> GPT 公平请求池
        |
        +--> GPT judge -----> 同一个 GPT 公平请求池
```

请求名额只覆盖一次实际网络调用。成功或异常返回后立即释放，重试等待不占用名额，下一次重试重新申请。请求池按样本轮转分配名额：存在多个活跃样本时，优先让不同样本先获得一个请求机会，再使用空闲容量处理同一样本的后续 trial 或 repeat。

这种调度既保证 Qwen 回答与 Qwen judge 合计不超过其服务额度，也避免少数样本的多次评分长期占满请求池；GPT 的实验性并行不会挤占 Qwen 的在线评分容量。

## 6. 评分记录与可追溯性

评分记录包含评测协议、两类 answer trial、允许的 judge repeat 结果、各轨道聚合摘要，以及 Qwen 代表 trial 编号。每条结果通过 `answer_source` 与 `judge_source` 标明所属轨道。GPT 回答 trial 的 `qwen_judge_results` 必须为空；聚合时若发现 Qwen 评分 GPT 回答会直接报错，避免协议被静默破坏。

为避免原始 judge 文本在后续产物中反复复制，评分阶段会将代表 Qwen 响应和各 Qwen/GPT repeat 的原始响应写入压缩 trace sidecar，主 JSONL 仅保留相应的 trace 标识。正式评分产物的 manifest 会登记该 sidecar 的校验信息，因此实验分析可在保持主产物轻量的同时追溯原始评分依据。

## 7. 配置与分析方式

默认配置为 `SCORING_ANSWER_TRIALS=3`、`GPT_ANSWER_TRIALS=3`、`QWEN_JUDGE_REPEATS=2`、`GPT_JUDGE_REPEATS=2`。Round 0 默认固定 `ROUND0_INITIAL_TRIALS=3`、`ROUND0_EXTRA_TRIALS=0`、`ROUND0_MAX_TRIALS=3`，并在内部关闭 GPT 对 Qwen 回答的复评。`scoring.py` 的 `--no-gpt-score-qwen-answers` 可显式关闭进化后交叉复评。

GPT 回答端可用 `GPT_ANSWER_MODEL`、`GPT_ANSWER_BASE_URL`、`GPT_ANSWER_API_KEY` 独立配置；若未指定，运行脚本从 GPT judge 配置回退。分析时先使用顶层 `score_rate`、`qwen_score_summary` 和代表 trial 判断在线决策，再分别查看两个 GPT 评分摘要。不能用 GPT 汇总替换、修正或平均顶层 `score_rate`。

## 8. 已有验证

项目测试已覆盖：两阶段评测矩阵；每份回答恰好 2 次评分；Qwen 从不评分 GPT 回答；Qwen 多次评分均值驱动顶层得分和代表 trial 选择；GPT 生成或评分失败不阻塞 Qwen 决策；透传样本不触发新调用；Qwen/GPT 请求池均遵守上限。

运行评分相关回归可使用：

```bash
pytest -q tests/test_multitrial_scoring.py
```

执行真实实验时，应确认 Qwen 和 GPT 服务配置均可用、请求池峰值未超过配置上限、GPT 失败次数已在汇总中体现，并将在线决策与 GPT 实验观察分开解读。
