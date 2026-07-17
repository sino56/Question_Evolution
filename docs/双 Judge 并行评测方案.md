# 双 Judge 并行评测方案

- **文档版本**：v2.0
- **修订日期**：2026-07-17
- **项目**：Question_Evolution / PoliceQA 题目难度进化
- **读者**：维护评分协议、调整模型调用配置或分析评分产物的内部工程师。
- **读后可执行的动作**：正确配置多 trial 双 Judge 评分，并根据评分产物区分在线决策分数和 GPT 实验记录。

## 1. 目标与边界

双 Judge 评测用于在保持既有 Qwen 在线评分口径的前提下，为同一份候选回答保留 GPT 的独立复评结果。这样既能让进化流水线继续以稳定、单一的分数来源驱动分流和状态更新，也能在实验分析时查看两类 judge 对同一回答的评价差异。

当前实现的边界如下：

- Qwen 生成待评回答，Qwen judge 负责唯一的在线流程决策；
- GPT judge 对同一回答进行实验性复评，结果单独保存，不参与分数合成；
- 每次评分都使用题目当前的 rubric 和 score prompt，评分前只将候选回答填入评分占位符；
- 多 trial、多次评分和并行调度只改变采样与执行方式，不改变题目进化、效果分析、状态更新、停止条件和 memory 写入所依据的业务规则；
- 未进化的透传样本沿用已有回答、rubric 和评分结果，不重新调用任一 answer 或 judge 服务。

## 2. 当前评测流程

循环入口在 Round 0 和每个进化轮的评分阶段均使用同一协议。推荐配置为每题生成 3 个 Qwen 回答，每个回答分别接受 2 次 Qwen 评分和 2 次 GPT 实验评分；这些数量均可通过运行配置调整。

```
当前题目 + rubric + score prompt
                    |
                    v
          Qwen 生成多个独立回答 trial
                    |
                    v
       对每个 candidate_answer 构造同一份评分材料
                    |
        +-----------+------------+
        |                        |
        v                        v
Qwen judge 重复评分          GPT judge 重复复评
必需结果，失败使样本失败      实验结果，失败写入该次 error
        |                        |
        v                        v
Qwen trial 均分             GPT trial 均分或空值
        |                        |
        +-----------+------------+
                    |
                    v
Qwen 全部 repeat 聚合为唯一在线 score_rate
并选出代表 trial 兼容下游字段
                    |
        +-----------+------------+
        |                        |
        v                        v
画像、分流、效果、状态、memory     GPT 汇总及逐次记录留在评分产物中
仅读取 Qwen score_rate              供离线实验分析
```

同一 trial 内的 Qwen 与 GPT 评分在候选回答生成后并行启动；多个 trial 的评分也可同时进行。评分结果会按 trial_index 和 repeat_index 保存，因此网络返回的先后不会改变试次含义或聚合顺序。

## 3. 多 trial 与双 Judge 评分

### 3.1 候选回答与评分材料

在自由回答模式下，Qwen 为同一题目生成多个独立回答。每个回答都是一个独立 trial，不能以一次回答重复计数。评分阶段为该回答填充题目专属的 score prompt，再将完全相同的评分文本分别发送给 Qwen judge 与 GPT judge；两侧均按同一 rubric 标题和权重规范化逐项得分。

这样做的目的是将 judge 差异限制在评分服务本身，而不是让两侧看到不同题目、不同评分依据或不同回答。Qwen answer model 不接收 reference、rubric、score prompt、算子、预期失败模式或历史评分等评分上下文，避免回答阶段被评测目标反向引导。

### 3.2 独立重复与失败语义

每个 judge 的每次 repeat 都是一次独立网络调用，并沿用评分阶段已有的解析、rubric 标题对齐和有限重试逻辑。Qwen 评分是必需结果：任一必需 Qwen 评分在重试后仍失败时，该样本记录为评分失败，不发布为完整评分产物。GPT 评分是实验结果：单次调用在重试后失败时，该 repeat 记录错误信息，不计入 GPT 均值，也不取消已经成功的 Qwen 评分。

这种区分的目的，是避免实验性外部复评的短暂不可用阻断主流程，同时禁止以缺失的 Qwen 评分生成看似完整的在线分数。

## 4. 聚合与兼容输出

### 4.1 Qwen 是唯一在线决策分数

每个 trial 先计算该 trial 内全部 Qwen repeat 的平均得分率；随后汇总所有 trial 的 Qwen repeat，生成 qwen_score_summary 的数量、均值、最小值和最大值。该总均值乘以 rubric 正向总分后写入顶层总分，再计算顶层 score_rate。

后续画像、候选分流、效果分析、状态更新、回滚与停止条件读取的都是这个 Qwen 派生的 score_rate。GPT 分数既不会与 Qwen 平均，也不会覆盖顶层总分或触发任何在线状态变化。这样保持了跨轮决策口径的单一性，避免外部实验信号改变已有进化策略。

### 4.2 代表 trial

多 trial 结果需要继续兼容只读取单份 candidate_answer、逐项得分和总体评价的下游逻辑。实现会选择 Qwen trial 均分最接近该样本 Qwen 总均分的回答；若距离相同，则选择较小的 trial 编号。顶层回答、逐项得分、总体评价和代表 Qwen 原始响应均投影自该 trial 的首个 Qwen repeat，而顶层总分和 score_rate 仍来自全部 Qwen repeat 的聚合。

这种分离使顶层记录既能代表典型回答，又不会把一次偶然的 trial 分数误当成整个样本的在线分数。

### 4.3 GPT 实验汇总

每个 trial 保存其 GPT repeat 明细和成功评分的 trial 均值。样本级 gpt_score_summary 分别记录请求次数、成功次数、失败次数及成功评分的数量、均值、最小值和最大值，并标记为实验用途。GPT 全部失败时，均值为空而 Qwen 决策仍可正常输出。

该汇总用于离线比较两类 judge 的观察结果；它不会生成额外的流程标签或改变 memory 的写入条件。

## 5. 受控并行与服务额度

评分运行将样本 worker、Qwen 请求池和 GPT 请求池分开配置。Qwen 回答请求与 Qwen judge 请求共享同一全局池，GPT judge 使用独立池；两类服务的默认在途上限均为 20，但应按实际服务的并发、QPM 和 token 限制调整。

```
评分样本 worker
        |
        +--> Qwen 回答 ------> Qwen 公平请求池
        |
        +--> Qwen judge ----> 同一个 Qwen 公平请求池
        |
        +--> GPT judge -----> GPT 公平请求池
```

请求名额只覆盖一次实际网络调用。成功或异常返回后立即释放，重试等待不占用名额，下一次重试重新申请。请求池按样本轮转分配名额：存在多个活跃样本时，优先让不同样本先获得一个请求机会，再使用空闲容量处理同一样本的后续 trial 或 repeat。

这种调度既保证 Qwen 回答与 Qwen judge 合计不超过其服务额度，也避免少数样本的多次评分长期占满请求池；GPT 的实验性并行不会挤占 Qwen 的在线评分容量。

## 6. 评分记录与可追溯性

评分记录包含评测协议、所有 answer trial、两侧 judge 的 repeat 结果、Qwen 和 GPT 聚合摘要，以及代表 trial 编号。每条成功 judge 结果保存模型标识、逐项得分、总体评价、总分和得分率；GPT 失败结果保留 repeat 编号、模型标识和错误信息。

为避免原始 judge 文本在后续产物中反复复制，评分阶段会将代表 Qwen 响应和各 Qwen/GPT repeat 的原始响应写入压缩 trace sidecar，主 JSONL 仅保留相应的 trace 标识。正式评分产物的 manifest 会登记该 sidecar 的校验信息，因此实验分析可在保持主产物轻量的同时追溯原始评分依据。

## 7. 配置与分析方式

> 暂时无法在飞书文档外展示此内容

分析实验结果时，先使用顶层 score_rate、qwen_score_summary 和代表 trial 判断当前流水线的实际决策依据；再查看 gpt_score_summary 与各 trial 的 gpt_judge_results，了解 GPT 的独立复评是否完整以及与 Qwen 结果是否存在值得人工分析的差异。不能用 GPT 汇总替换或平均顶层 score_rate。

## 8. 已有验证

项目测试已覆盖：Qwen 多次评分均值驱动顶层得分和代表 trial 选择；GPT 成功结果仅作为实验记录；GPT 全部失败时 Qwen 决策仍可生成；透传样本不触发新的 answer 或 judge 调用；Qwen/GPT 请求池均遵守上限，且同题并行与跨样本公平调度不会改变 trial 数量。

运行评分相关回归可使用：

```bash
pytest -q tests/test_multitrial_scoring.py
```

执行真实实验时，应确认 Qwen 和 GPT 服务配置均可用、请求池峰值未超过配置上限、GPT 失败次数已在汇总中体现，并将在线决策与 GPT 实验观察分开解读。
