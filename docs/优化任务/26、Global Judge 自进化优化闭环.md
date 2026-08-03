# Global Judge 自进化优化闭环

## Summary

本方案将 Global Judge 定位为 Question Evolution 的实验后优化控制层。它不是单个"看完实验就改 Prompt"的模型，而是一套由确定性统计、结构化审计、LLM 归因、Shadow 验证、版本发布和回滚治理组成的闭环机制。

读者是维护 Question Evolution 实验链路、算子体系、Router Prompt、Rubric/Judge 和 Memory 机制的内部工程师。读完后应能设计一个不会直接污染正式实验的自进化流程：每次实验结束后，系统先分析结果和失败原因，再生成优化候选，经过 Shadow 回放和门禁验证后，才允许部分低风险策略进入后续实验。

## Problem Frame

当前 Question Evolution 已经具备完整实验闭环：样本画像、候选筛选、算子路由、题目生成、复杂度校验、作答、Rubric 生成、评分、效果分析、状态更新、局部 Memory 和实验统计。这个链路能够回答"本次实验发生了什么"，但还不能自动回答"下一轮应该如何改得更好"。

如果每次实验后只靠人工阅读总结，再手动修改 Router Prompt、operator card、算子生成 Prompt 或 Rubric，会存在三个问题：

1. 反馈周期长。 实验一次成本较高，人工复盘再手改会拖慢迭代。
2. 归因不稳定。 同一个失败现象可能来自 Router、算子、生成题、Rubric、Judge、样本质量或搜索策略，人工判断容易受少数样本影响。
3. 经验难以治理。 即便找到有价值的优化点，也缺少统一的状态流转、版本控制、Shadow 验证和回滚路径。

Global Judge 的目标是把实验结果转化为受控优化建议，而不是让系统自由修改自身。它应该先成为可靠的"实验审计和优化提案生成器"，再逐步扩展为有限自动发布的自进化控制层。

## Design Position

**Global Judge answers why the experiment behaved this way**

Global Judge 面向实验完成后的全量记录，回答：

- 哪些分支真实压到了模型能力边界；
- 哪些分支只是题目变清楚、Rubric 变严、Judge 波动或题面不可比；
- Router 是否因为主题相似误召回算子；
- 算子内容是否存在适用条件过宽、生成闭包过大或题面泄漏；
- Rubric 或 Judge 是否让自动分数不能支持能力结论；
- 哪些优化建议值得进入 Shadow，哪些必须丢弃或人工复核。

**Global Judge should not directly mutate production prompts**

首版禁止 Global Judge 在实验结束后直接修改正式 Router Prompt、operator generation prompt、Rubric prompt 或全局 Memory `active` 卡片。它只能输出结构化优化候选，并将候选标记为 `proposed` 或 `shadow`。

原因是 Global Judge 一旦归因错误，下一轮实验会按错误策略继续采样、路由和生成，形成自我强化闭环。自进化系统的关键不是"自动改"，而是"自动发现可验证的改动，并控制它何时生效"。

**Global Judge is a mechanism, not a model**

Global Judge 可以包含 LLM，但不能完全依赖 LLM。建议拆成四层：

1. 确定性统计层。 聚合分数、分支状态、effect label、`not_applicable`、`score_increased`、Judge 分歧、调用成本和耗时。
2. 证据打包层。 为每个样本、节点和算子构造可审计 evidence pack。
3. LLM 归因层。 在证据受限的上下文中判断失败原因、改动层级和优化建议。
4. 发布治理层。 用规则门禁、Shadow 回放、人工复核和状态流转决定建议是否进入后续实验。

## Current Evidence

近期多算子搜索实验已经暴露出 Global Judge 应优先处理的问题。一次典型实验中，3 个进入进化的样本产生 22 个二层分支，记录到 334 次模型调用，平均每个进化样本超过 100 次调用。多数可评分分支为 `score_increased`，部分结构算子在生成阶段返回 `not_applicable`，未形成有效边界。

这类结果不能简单解释为"算子无效"。更合理的候选归因包括：

1. Router 过召回，把主题相关但硬槽位不足的算子放进执行候选；
2. operator card 对 required slots、reject conditions 和近邻边界表达不够强；
3. 生成 Prompt 把原本隐含的结论边界说得更清楚，反而让弱模型更容易答对；
4. Rubric 或 Judge 没有稳定扣到目标错误；
5. 当前样本对某些能力轴本身不适合继续进化；
6. 搜索成本集中消耗在低价值分支上。

Global Judge 的第一价值就是把这些原因分开，并给出"应该改 Router、改 operator card、改生成 Prompt、改 Rubric/Judge、改 Memory 策略，还是停止该类样本"的受控建议。

## Control Loop

推荐闭环如下：

```text
complete experiment
  -> build evidence pack
  -> deterministic metrics
  -> global judge diagnosis
  -> optimization proposals
  -> shadow apply
  -> replay / holdout verification
  -> publish, reject, downgrade, or retire
  -> next experiment uses frozen snapshot
```

### Step 1. Complete the experiment first

Global Judge 不应在主实验尚未完成时介入正式决策。主实验应先完整产出评分、effect、state、branch、attempt、summary、statistics 和局部 Memory。未完成实验只允许做恢复和完整性检查，不应生成正式优化建议。

### Step 2. Build evidence pack

每次 Global Judge 运行先构造实验证据包。证据包应按 root sample、node、branch 和 operator 聚合，而不是把所有 JSONL 原样送入 LLM。

证据包至少包含：

- 实验配置摘要：搜索模式、路由模式、模型配置、Judge repeat、Prompt 版本、operator policy 版本、Memory snapshot；
- 样本级记录：原题、进化题、参考材料、Rubric、score rate、answer trials、Qwen/GPT Judge 明细摘要；
- 路由记录：primary/backup/avoid、LLM Router candidates、`operator_decision_audit`、cache/latency/status；
- 分支记录：operator、branch status、validation result、generation attempts、score delta、effect label；
- 成本记录：每个样本、节点和算子的调用次数与耗时；
- Memory 记录：本轮新增 operator memory、failure memory、invalid generation cases；
- 人工复核记录：若存在，则作为最高优先级证据。

### Step 3. Run deterministic metrics

在调用 LLM 归因前，规则层先产出不可争议的统计信号：

- `selected_then_not_applicable_rate`：被 Router 选中但生成阶段不适用的比例；
- `score_increased_rate`：评分升高的可评分分支比例；
- `full_score_no_drop_rate`：满分未破或无明显变化比例；
- `effective_boundary_rate`：自动标记为有效边界的比例；
- `invalid_generation_rate`：未通过复杂度或可回答性校验的比例；
- `judge_disagreement_rate`：Qwen/GPT 或同 Judge repeat 的方向性分歧；
- `cost_per_confirmed_boundary`：每个高可信边界消耗的调用量和耗时；
- `operator_confusion_matrix`：近邻算子重复召回、同一失败机制多开分支的情况。

这些指标用于限制 LLM 解释空间，避免它在噪声上自由编故事。

### Step 4. Diagnose by failure layer

Global Judge 的诊断必须先选择失败层级，再给具体建议。建议固定以下层级：

### Step 5. Generate optimization proposals

Global Judge 的输出不是自然语言建议，而是结构化 proposal。每条 proposal 必须包含：

- `proposal_id`；
- `target_layer`：Router、operator card、operator prompt、validation、rubric/judge、memory、search policy、sample policy；
- `problem_statement`：观察到的具体问题；
- `evidence_refs`：样本、算子、分支、effect label、评分或成本证据；
- `proposed_change`：建议修改的意图，而不是直接覆盖正式内容；
- `expected_improvement`：预期降低什么错误或成本；
- `risk_level`：`low`、`medium`、`high`；
- `verification_plan`：需要如何 Shadow、回放或人工复核；
- `rollback_plan`：失败时如何撤回；
- `publish_gate`：进入 `active` 的最低指标条件。

### Step 6. Shadow apply only

所有 proposal 默认进入 Shadow。Shadow 生效方式包括：

- 生成候选 Router Prompt 版本，但不替换正式版本；
- 生成候选 operator card 摘要，但只用于对照路由；
- 生成候选全局 Memory strategy card，但只进入 `shadow` serving；
- 生成候选 runtime policy，但只在 forced/shadow 实验中使用；
- 生成候选 Rubric/Judge 校准建议，但只用于复评对照。

Shadow 结果必须与正式结果并行记录，不能改变本次实验的正式 state、score、Memory `active` 卡片或终止判断。

### Step 7. Replay and holdout verification

候选优化至少经过一种验证：

1. 历史回放。 使用历史样本画像和诊断，比较旧策略与新策略会召回哪些算子。
2. 冻结实验复跑。 在固定输入、模型、Judge 和 Memory snapshot 下复跑小批样本。
3. Holdout 验证。 在未参与 proposal 生成的样本上验证是否真的降低误召回或成本。
4. 人工复核。 对高风险 Prompt 修改、Rubric 修改和全局 `active` 策略卡做人工确认。

只有验证通过后，proposal 才能从 `shadow` 升级到 `active`。

### Step 8. Publish with frozen snapshot

发布后产生新的优化快照。下一次实验启动时固定该快照，并记录到实验 manifest 或搜索状态中。实验运行中不得读取持续变化的 Global Judge 输出或全局 Memory。

## Proposal Types

### P1. Router Prompt or Router Policy Proposal

适用于候选过宽、近邻重复召回、硬槽位不足、target failure 没有压住候选范围的情况。

典型改动：

- 收紧 `operator_candidates` 定义；
- 增强 task contract、evidence topology 和 hard-slot gate；
- 强化 `uncertain_operator_rationales` 与 `not_selected_operator_rationales` 的归档规则；
- 调整 operator ordering 或 exploration ratio；
- 增加某些高混淆算子的自然路由 gate。

发布风险较高，必须经过 replay 或 holdout。

### P2. Operator Card Proposal

适用于 Router 容易误解某个算子的 required slots、reject conditions 或 adjacent boundaries 的情况。

典型改动：

- 补充 required slots；
- 明确 reject-if-missing；
- 增加近邻边界反例；
- 标记某个算子只适合 qualification 或 shadow。

发布风险中等。若只影响 Router 摘要，可先 Shadow；若会改变正式执行候选，需要回放验证。

### P3. Operator Generation Prompt Proposal

适用于算子被正确选择，但生成题无法形成目标压力的情况。

典型改动：

- 降低题面泄漏；
- 避免显式写出结论边界；
- 防止把开放业务判断改成材料分类题；
- 加强竞争判断、反事实或近邻干扰的内容控制；
- 防止格式复杂度、题长和选项数量成为主要难度来源。

发布风险高。每次只允许改一个算子或一个算子族，并必须保留旧版本对照。

### P4. Rubric and Judge Proposal

适用于分数变化无法稳定映射到目标错误，或 Qwen/GPT Judge 分歧影响结论的情况。

典型改动：

- 增加可执行的负向扣分项；
- 降低格式、关键词或表面完整性权重；
- 区分结论正确但论证不成立；
- 标记题目不可比、Rubric 风险或 Judge 不稳定样本；
- 改进复评触发条件。

发布风险高。不得用 GPT 对照分数直接替换正式 Qwen 决策分数，只能作为复核和校准证据。

### P5. Global Memory Strategy Proposal

适用于多次实验中出现可迁移的 场景类型 × 题型结构 × 推理机制 × 算子策略。

典型改动：

- 新增 `proposed`/`shadow` strategy card；
- 补充适用条件、排除条件和反例；
- 降级过宽或冲突的策略卡；
- 拆分旧版本下有效、新版本下无效的卡片。

发布风险低到中等。低风险策略卡可先进入 `shadow` serving，但不得直接 hard gate Router。

### P6. Search and Cost Proposal

适用于高成本低收益的搜索行为。

典型改动：

- 调整 branch window；
- 调整单题 request/evaluation budget；
- 对高失败率算子降低优先级；
- 对低历史证据算子保留少量探索而不是完全关闭；
- 对连续 `score_increased` 或 `not_applicable` 的分支提前停止。

发布风险中等。必须同时监控机会损失，避免因省成本错过真实边界。

## Risk Register

## Data Products

### Global Judge Run Report

每次运行输出一份实验级报告，内容包括：

- 实验范围和配置；
- 样本、节点、分支和算子总体统计；
- 成功、失败、无效、待确认和评分风险分类；
- 主要 root cause；
- 优化 proposal 列表；
- 不应发布的反例和证据缺口；
- 下一次 Shadow 验证建议。

### Diagnosis Record

每个样本或分支输出一条结构化诊断记录：

```json
{
  "record_type": "global_judge_diagnosis",
  "diagnosis_version": "global-judge-diagnosis-v1",
  "sample_id": "<sample>",
  "node_id": "<node>",
  "operator_id": "<operator>",
  "effect_label": "score_increased",
  "failure_layer": "router",
  "failure_reason": "候选算子硬槽位不足但被放入执行候选",
  "confidence": "medium",
  "evidence": [
    "Router audit 显示该算子仅主题相关",
    "生成阶段返回 not_applicable",
    "同类算子在多个样本上重复出现"
  ],
  "recommended_action": "strengthen_operator_card_or_router_gate"
}
```

### Optimization Proposal

每条 proposal 独立保存，支持状态流转：

```json
{
  "proposal_id": "GJ-PROP-000001",
  "proposal_version": "global-judge-proposal-v1",
  "target_layer": "router_prompt",
  "status": "proposed",
  "risk_level": "high",
  "problem_statement": "Router 对缺硬槽位的路径类算子误召回",
  "proposed_change": "强化路径拓扑算子的 required slots 与 reject-if-missing",
  "expected_improvement": [
    "降低 selected_then_not_applicable_rate",
    "降低低价值分支调用成本"
  ],
  "verification_plan": "在历史样本上 replay Router 候选，并在 holdout 小批实验中对比",
  "publish_gate": {
    "selected_then_not_applicable_rate_delta": "<= -0.30",
    "effective_boundary_rate_not_lower": true,
    "manual_review_required": true
  }
}
```

### Publish Decision

发布决策记录用于审计和回滚：

- `accepted_to_shadow`；
- `accepted_to_active`；
- `rejected`；
- `downgraded`；
- `retired`；
- `needs_manual_review`。

每次决策必须记录证据、指标、版本和负责人。

## State Lifecycle

Global Judge 产物建议使用统一状态：

```text
proposed -> shadow -> active -> retired
              |         |
              v         v
           rejected  downgraded
```

### Proposed

由一次实验或一次分析生成。只表示"值得验证"，不影响正式实验。

### Shadow

进入并行对照路径。可以被 Router 或 Memory 检索用于审计，但不能改变正式候选、排序、评分或状态机。

### Active

通过回放、holdout 或人工复核。可以影响后续正式实验，但必须记录 snapshot，并保留回滚路径。

### Downgraded or Retired

新版本下连续失败、反例增加、版本不兼容或人工复核否定后，降级或退役。退役不删除历史证据，只从 serving 侧移除或降权。

## Verification Metrics

每次 Shadow 验证至少比较旧策略和新策略的以下指标：

不能只用单一指标判断发布。特别是有效边界率上升但题面质量下降、Judge 分歧上升或人工确认率下降时，不应发布。

## Phased Implementation Plan

### Phase 0. Define offline evidence and report contract

目标是先让 Global Judge 成为稳定的离线审计器。

范围：

- 定义 evidence pack 的字段边界；
- 定义诊断记录和 proposal schema；
- 聚合实验统计、分支状态、effect label、Memory 写入和 Judge 分歧；
- 输出只读报告，不改变任何正式实验产物。

退出条件：

- 能对一个完整实验输出样本级、算子级和实验级诊断；
- 每条诊断能回溯到具体 evidence；
- 不需要人工阅读全量 JSONL 才能判断主要失败层级。

### Phase 1. Add report-only Global Judge

目标是在不改变流程的情况下，验证 LLM 归因质量。

范围：

- 对高价值样本和异常分支调用 LLM 归因；
- 生成 `proposed` optimization proposals；
- 标记证据不足和需要人工复核的样本；
- 与人工审核 prompt 的结论做对照。

退出条件：

- LLM 归因能稳定区分 Router、算子、Rubric/Judge 和样本问题；
- 泛化套话比例可控；
- 人工抽检认为 proposal 可执行，而不是空泛建议。

### Phase 2. Shadow proposal generation

目标是让低风险建议进入 Shadow，但不影响正式实验。

范围：

- 生成 shadow strategy cards；
- 生成候选 operator card 摘要；
- 生成候选 Router Prompt 片段；
- 生成候选 search/cost policy；
- 记录与正式策略的差异。

退出条件：

- Shadow 输出不改变正式 score、state、Memory `active` 卡片或终止判断；
- 能解释每个差异来自哪条 proposal；
- 能对 proposal 进行 replay。

### Phase 3. Replay and holdout evaluator

目标是用冻结样本证明 proposal 是否真的改善。

范围：

- 历史 Router replay；
- 小批 forced/shadow 复跑；
- holdout 样本验证；
- 成本和机会损失分析；
- 发布门禁报告。

退出条件：

- 每条准备发布的 proposal 都有旧版/新版对照指标；
- 能识别"省成本但漏掉有效算子"的负面结果；
- 高风险 Prompt 修改有人工复核记录。

### Phase 4. Limited active publication

目标是只允许低风险、可回滚的优化进入正式下一轮实验。

首批允许自动或半自动 `active` 的类型：

- `shadow`/global Memory strategy card；
- operator card 摘要中的 reject-if-missing 和 evidence notes；
- search/cost policy 的保守阈值；
- sample stop/defer policy。

首批不允许自动 `active` 的类型：

- Router system prompt 大改；
- operator generation prompt 大改；
- Rubric/Judge 决策口径改变；
- 全局 Memory hard gate；
- 关闭某一类算子的执行资格。

退出条件：

- `active` 后的实验记录固定 snapshot；
- 可一键回退到上一版策略；
- `active` 策略没有提高 Judge 风险、无效生成率或机会损失。

### Phase 5. Iterative self-evolution loop

目标是在多轮实验中形成受控自进化。

范围：

- 每轮实验后自动生成 Global Judge 报告；
- 自动维护 proposal 状态；
- 定期编译 Global Memory Serving Snapshot；
- 定期回放历史实验；
- 对持续失败的 `active` 策略自动降级或退役。

退出条件：

- 系统能持续降低误召回和单个有效边界成本；
- 新策略不会压缩探索空间；
- 人工复核负担下降，而不是转移到更多无效建议上。

## Integration with Existing Plans

本方案不是替代已有方案，而是把已有优化方向串成上层控制环：

- 与全局 Memory 方案的关系：Global Judge 负责从实验结果中判断哪些事实可以进入 `proposed`/`shadow`/`active` 策略卡，并触发 Serving Memory 重新编译。
- 与 Router Prompt 精度优化方案的关系：Global Judge 负责判断 Router 误召回是否仍存在，并提出可验证的 Prompt 或 operator card 修改。
- 与组内相对评分机制方案的关系：Global Judge 可读取题目行为机制诊断 sidecar，把回答差异和 Judge 稳定性作为是否发布能力机制的前置证据。
- 与人工审核 prompt 的关系：首版 Global Judge 应先复用人工审核口径，将人工报告中的判断结构转化为机器可检查的 proposal 和 gate。

## Acceptance Criteria

1. Global Judge 首版只读实验产物，不改变正式实验结果。
2. 每条诊断都能明确归入 Router、Operator、Validation、Rubric/Judge、Sample/Data、Search/Cost 或 Memory 中的一个主层级。
3. 每条 optimization proposal 都有证据、风险等级、验证计划、发布门禁和回滚计划。
4. Shadow proposal 不改变正式候选、评分、state、终止判断或 `active` Memory。
5. 至少能在一个历史实验上输出可解释的成本浪费归因，例如过召回、`not_applicable`、`score_increased` 和 Judge/Rubric 风险。
6. Replay 能比较旧策略和新策略的候选差异、成本差异和机会损失。
7. 高风险 Prompt 修改必须有人工复核和旧版并行对照。
8. `Active` 发布必须生成新的 snapshot，并支持回滚到上一版。
9. 全局 Memory 不因单次偶然降分、题面泄漏、Rubric 风险或 Judge 不稳定而写入 `active` 成功策略。
10. 后续实验能在 summary 或 manifest 中记录所使用的 Global Judge / Memory / Prompt / policy 版本。

## Non-Goals

首版明确不做以下事项：

- 不训练策略模型；
- 不用 Global Judge 直接更新模型参数；
- 不让 LLM 直接覆盖正式 Prompt；
- 不把 GPT 对照评分直接替换 Qwen 自动决策分数；
- 不把一次实验的一条降分记录直接发布为 `active` 全局策略；
- 不让全局 Memory 删除合法候选，只提供受预算约束的参考；
- 不在实验运行中动态改变已经冻结的路由、Memory 或 Prompt 快照；
- 不追求完全无人审的 Prompt 自修改。

## Open Questions

1. Global Judge 的首版输入包是否只覆盖多算子搜索实验，还是同时覆盖 single-branch 实验？
2. 人工复核状态应由独立审核文档导入，还是在 Global Judge UI/报告中直接标注？
3. Shadow proposal 的存放位置应与实验目录绑定，还是进入统一全局 proposal ledger？
4. 低风险策略卡进入 `shadow` serving 是否需要人工确认，还是由规则门禁自动完成？
5. 高风险 Prompt 修改的最小 holdout 规模和人工确认比例应如何配置？
6. `Active` 发布的负责人、审批记录和回滚命令是否需要单独治理文档？

## Recommended First Slice

第一阶段建议只做"离线 Global Judge Report + Proposal 草案"，不要做自动发布。

最小切片：

1. 从一个完整实验中聚合样本、分支、算子、effect、Judge 和成本摘要；
2. 规则层生成失败信号：`not_applicable`、`score_increased`、`invalid_generation`、Judge 分歧和高成本分支；
3. LLM 只对异常样本生成失败层级归因；
4. 输出 proposal，但全部保持 `proposed`；
5. 人工抽检 proposal 是否真的能指导 Router、operator card、生成 Prompt、Rubric/Judge 或 Memory 的后续优化。

这个切片能先验证 Global Judge 的归因质量，不会污染正式实验，也不会把自进化风险提前引入主链路
