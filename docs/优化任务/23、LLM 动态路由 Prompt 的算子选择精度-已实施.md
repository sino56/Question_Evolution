# 提升 LLM 动态路由 Prompt 的算子选择精度

## Summary

本方案优化 LLM 动态路由 Prompt，使 Router 从"相关算子尽量召回"转为"硬结构满足且直接压测目标失败机制才开分支"。目标不是把路由改成场景规则，而是让 LLM 在 Prompt 内完成更严格的任务画像、证据拓扑、失败机制和算子硬槽位判断。

读者是负责维护 Question Evolution 路由链路和算子 Prompt 的内部工程师。读完后应能修改 Router Prompt 与 operator card 摘要，并在现有完整实验链路下判断修改是否真正降低误召回。

## Problem Frame

当前 Router Prompt 的主要问题不是"完全选错算子"，而是高召回、低精度：

1. 会把结构主题相似误判为算子硬结构成立。
2. 会把缺少关键事实槽位的近邻算子放进 `operator_candidates`，再由生成阶段返回 `not_applicable`。
3. 会把多个相邻算子全部开分支，导致搜索成本上升，但有效边界命中没有提升。
4. 对 `overscore_diagnosis.target_failure_mode` 的锚定不够强，容易围绕同一题的表面主题扩散。
5. 不确定算子虽有审计字段，但 Prompt 没有足够强制模型把"接近但缺硬槽位"的算子移出候选。

2026-07-29 的 exp1 暴露了该问题：4 道输入题中 3 道进入进化，Router 分别选择 10、7、5 个算子，共产生 22 个分支；其中 4 个分支在生成阶段 `not_applicable`，18 个可评分分支里 17 个 `score_increased`，没有 effective_boundary。这说明 Router 能找到相关方向，但开分支过宽，后续生成和评测成本被大量消耗在弱相关或已把答案边界显性化的分支上。

## Data and Scenario Observations

本次参考的题目集覆盖两类来源：四场景 discovery 数据和 v2.8 测试集。它们不是单一案件模板，而是混合了视频追踪、盲区前后同一性、轨迹闭环、多实体协同、物品/车辆流转、观测可靠性、程序测量、阈值判断、行动/事实结论边界等问题。

因此 Router Prompt 不能写成"某个具体场景选某个算子"的规则。数据对 Prompt 的启发应抽象成可迁移分析方法：

- 先判断题目要求的是事实说明、比较区分、路径闭环、身份连续、角色绑定、程序步骤、阈值判断还是结论层级校准。
- 再判断题面是否真的给出完成该推理所需的主体、对象、时间、地点/点位、来源、规则、阈值、候选解释和竞争事实。
- 最后判断当前高分答案的失败是否正落在某个算子的能力轴上，而不是只与该算子的常见业务主题相似。

## Current Failure Modes

### F1. 主题相似替代硬结构满足

当题面出现"盲区、消失、再出现、路径、多人、角色"等词时，Router 倾向召回对应结构算子。但这些词只能说明存在表面相关性，不能证明算子可构题。

典型表现：

- `O11` 被选中，但题面没有不可见区间的入口时间、预期出口窗口、实际出口/缺席信息、候选假设比较时间窗。
- `O19` 被选中，但题面没有可竞争同类实体、跨观测节点的局部身份线索、节点绑定差异或定向动作事实。
- `O22` 被选中，但题面没有路径图节点、边通行限制、端点时间窗、竞争路径或联合可达性约束。

### F2. 失败机制没有压住候选范围

Router 已接收 `overscore_diagnosis.target_failure_mode`，但 Prompt 仍允许围绕题目表面结构扩散。比如目标失败只是"把视频线索上推为确定性结论"，最直接的压测应是跨层结论校准；但 Router 也会选出路径、盲区、多实体和观测累积等近邻分支。

### F3. 不确定算子没有被强制降级到审计

当前 Prompt 要求记录 `uncertain_operator_rationales`，但没有把"缺少硬槽位"定义为必须移出 `operator_candidates` 的强约束。结果是 LLM 会把"不确定但看起来有价值"的算子仍包装成 applicable。

### F4. 近邻算子边界不够可执行

部分算子具有重叠结构，例如：

- `O10` 与 `O31`：最小充分事实集合 vs 重复/相关观测的可靠度累积。
- `O20` 与 `O28`：多阶段事件链断点 vs 多跳链路闭合。
- `O22` 与 `O28`：路径拓扑可达性 vs 多跳链路闭合。
- `O23` 与 `O27`：观测可靠性限制 vs 跨层结论校准。

Prompt 没有要求 Router 明确"为什么选更具体的一个、为什么另一个降为不选或不确定"，导致同一缺口被多个算子同时开分支。

### F5. "不限制候选数量"被模型理解成尽量召回

原意是不要人为截断真正适用的候选，但模型容易理解为"所有相关候选都应该返回"。在树搜索中，这会直接放大分支成本：每个误召回算子都会触发生成、校验、作答、评分和效果分析。

## Optimization Principles

1. **路由判断只基于结构，不基于场景名。** 不能写"拉车门选 Oxx""笑气选 Oyy"这类规则。
2. **候选必须同时满足失败机制匹配和硬槽位满足。** 只满足其一不能进入 `operator_candidates`。
3. **缺硬槽位的近邻算子进入审计，不进入执行。** 审计只服务人工复盘，不参与后续判断。
4. **优先选择最贴近目标失败机制的算子。** 近邻算子除非压测不同真实缺口，否则不并列开分支。
5. **优化只发生在 Router Prompt 判断层。** Router Prompt 应完成语义判断；本方案不设计额外实验阶段、二次修正环节或运行时拦截逻辑。
6. **召回数量不是目标。** 目标是召回所有"硬结构满足且有独立压测价值"的算子。

## Required Router Prompt Changes

### P1. 将 Prompt 内部判断改为四阶段门禁

Router Prompt 应明确要求按以下顺序分析：

1. **任务契约重建：** 当前题目要求的输出类型、允许的最高结论层级、可用事实范围、不可补造事实范围。
2. **目标失败机制定位：** 高分答案具体发生了什么错判，例如错误闭环、错误绑定、忽略替代解释、把线索上推为事实、把事实上推为处置结论。
3. **算子硬槽位检查：** 对每个可能相关算子，列出已满足硬槽位和缺失硬槽位。
4. **候选归类：** 只有"目标失败机制匹配 + 硬槽位满足 + 不需补造事实"的算子才能进入 `operator_candidates`。

### P2. 重写候选定义

`operator_candidates` 的定义应从"真正匹配的合法候选"收紧为：

当前题面已提供该算子构题所需硬结构，且该算子直接压测 `target_failure_mode` 或另一个明确存在的真实失败机制；生成器无需补造实体、时间窗、路径、节点、来源、规则、阈值或竞争解释即可构造题目。

同时明确以下内容不能进入候选：

- 与主题、对象、业务词相似但硬槽位不全。
- 需要生成阶段补写关键节点或约束才可成题。
- 只是在同一题上重复压测另一个已选算子的同一失败机制。
- 只能作为人工复盘参考的不确定方向。

### P3. 强化三类审计归档

`operator_decision_audit` 只做记录，不参与执行。Prompt 需要把审计归类写得更硬：

- `selected_operator_rationales`：每个候选必须有"压测失败机制 + 已满足硬槽位 + 不需补造事实"的依据。
- `not_selected_operator_rationales`：记录近邻或表面相关但不应开分支的算子，说明它与已选算子的边界差异。
- `uncertain_operator_rationales`：记录接近但缺硬槽位的算子，说明缺什么事实、如果强行生成会补造什么。
- `operator_improvement_notes`：记录算子卡片本身不清楚的问题，例如适用条件过宽、边界不清、required slot 不显式、容易诱发表面匹配。

这些字段不得被执行环节用于执行或跳过分支，只作为人工判断依据。

### P4. 将"不限制数量"改为"不截断硬满足候选"

原 Prompt 中"所有真正匹配的合法候选都应返回"容易诱发过召回。建议改为：

不设置固定候选数量上限；但数量只由硬槽位满足和独立失败机制决定。若多个算子压测同一失败机制，优先保留最具体、最直接的算子，其余进入不选或不确定审计。

### P5. 增加近邻算子选择策略

Prompt 应要求 Router 对近邻算子执行 tie-break：

1. **更贴近失败机制者优先。** 如果失败是结论层级越界，优先 `O27`；只有观测质量本身是决定性冲突时才选 `O23`。
2. **更小构题闭包者优先。** 如果 `O20` 足以表达阶段断点，不要升级到 `O28` 的多跳链路闭合。
3. **同一能力轴不重复开分支。** 除非题面中存在两个独立失败机制，否则不要用多个算子压测同一判断。
4. **缺硬槽位不允许用泛化结构替代。** 缺路径图不能用"有路径相关词"选 `O22`；缺竞争实体不能用"多人出现"选 `O19`。

## Operator Family Gates

下面不是场景规则，而是 Router Prompt 内可复用的硬槽位检查方式。

## Proposed Prompt Skeleton

Router Prompt 可按以下结构调整：

先输出内部判断，不写入最终 JSON：

```text
1. task_contract
   - answer_task
   - available_fact_types
   - maximum_supported_claim_level
   - forbidden_added_facts
2. target_failure
   - failed_claim
   - failure_mechanism
   - required_pressure_point
3. operator_gate_table
   - operator_id
   - matched_failure_mechanism
   - satisfied_hard_slots
   - missing_hard_slots
   - would_need_fabricated_fact
   - decision: select / not_select / uncertain
4. overlap_resolution
   - if multiple operators target same failure, keep the most specific one
   - put near-duplicates into audit
```

最终 JSON：

- `operator_candidates` only contains `decision=select`
- `operator_decision_audit` records select / not_select / uncertain / improvement notes

最终输出不需要新增执行字段；已有 `operator_decision_audit` 足够承载人工复盘信息。

## Optimization Items

### OI1. Rewrite Router Prompt gate wording

- **Goal:** 将候选输出从相关性召回改为硬槽位门禁。
- **Scope:** Router system prompt；不改变实验流程、分支调度或执行逻辑。
- **Changes:**
  - 重写 `operator_candidates` 定义。
  - 增加 Prompt 内四阶段门禁判断。
  - 明确"缺硬槽位必须进入 uncertain 或 not_selected audit"。
  - 将"不限制数量"改为"不截断硬满足候选"。
- **Verification:** Prompt 测试应检查新版本包含 hard-slot gate、overlap resolution、audit-only 说明，并不包含按具体场景选算子的规则。

### OI2. Add route decision fixtures

- **Goal:** 用实验暴露的误召回模式固定 Prompt 行为预期。
- **Scope:** Router Prompt 行为夹具。
- **Fixture types:**
  - 有盲区词但缺端点时间窗时，`O11` 不进入候选，进入不确定审计。
  - 有多人词但缺竞争实体绑定事实时，`O19` 不进入候选。
  - 有路径词但缺节点/边/时间窗时，`O22` 不进入候选。
  - 目标失败是结论层级越界时，`O27` 优先于只表面相关的 `O23`/`O31`。
- **Verification:** `operator_candidates` 数量下降，但 `operator_decision_audit` 能解释被排除方向。

### OI3. Strengthen operator card summaries

- **Goal:** 让 Router 可读到每个算子的 required hard slots 和 near-boundary examples。
- **Scope:** operator card 渲染摘要，不改变算子生成 Prompt。
- **Changes:**
  - 每张卡显式包含 `required_slots`、`reject_if_missing`、`adjacent_boundaries`。
  - 对 `O11`/`O19`/`O22`/`O23`/`O27`/`O28`/`O31` 等高混淆算子优先补齐。
- **Verification:** Router Prompt 中不需要硬编码单个算子的长规则，也能通过卡片完成硬槽位判断。

### OI4. Keep audit as information-only prompt output

- **Goal:** 保证分析记录只服务人工复盘，不影响实验流程或后续执行。
- **Scope:** Prompt 输出要求；不新增利用审计字段的流程逻辑。
- **Changes:**
  - 候选执行仍只由 `operator_candidates` 决定。
  - `operator_decision_audit` 只记录选择、排除和不确定依据。
  - 不把 `operator_decision_audit` 用于分支选择、排序或任何二次过滤逻辑。
  - 记录不确定、不选和算子改进建议。
- **Verification:** 单测覆盖"审计字段存在但候选为空/候选不变"的行为。

### OI5. Verify with complete experiments

- **Goal:** 在现有完整实验链路下证明 Router 精度提升带来成本下降和误召回减少。
- **Scope:** 复跑完整实验，不拆分成局部替代验证，也不跳过生成、校验、作答、评分和效果分析。
- **Metrics:**
  - 平均每题选中算子数下降。
  - `selected_then_not_applicable_rate` 下降。
  - `O11`/`O19`/`O22` 这类硬槽位缺失误召回下降。
  - `operator_decision_audit` 对每个候选和关键排除方向都有依据。
  - 有效边界命中或人工复核价值提升；至少不再以更多分支换来更多 `score_increased`。

## Acceptance Criteria

1. Router 不再因"盲区/路径/多人"等表面词直接召回结构算子。
2. `operator_candidates` 中每个算子都有明确的目标失败机制和硬槽位满足依据。
3. 缺硬槽位但值得人工看一眼的方向进入 `uncertain_operator_rationales`，不进入执行候选。
4. 近邻算子的排除依据进入 `not_selected_operator_rationales`，可供人工判断算子边界是否需要改。
5. 审计字段不参与后续执行、排序或二次判断。
6. 复跑实验中误召回和平均分支数下降，不以牺牲真实适用算子为代价。

## Non-goals

- 不把具体案件、业务类型或题目场景写成路由规则。
- 不通过新增运行时拦截或人工门禁承担复杂语义判断。
- 不删除人工审计信息，也不把审计信息变成流程输入。
- 不通过固定候选数量上限压缩分支。
- 不改变算子本身的生成能力或题面构造逻辑；算子生成泄漏问题由语义预算和题面泄漏方案处理。

## Risks and Mitigations

## Validation Plan

### Unit and contract tests

- Router Prompt 版本更新后，测试必须覆盖候选定义、hard-slot gate、uncertain audit、near-boundary audit 和 information-only 行为。
- 构造 `O11`/`O19`/`O22` 的 hard-slot 缺失样本，确认它们不进入 `operator_candidates`。
- 构造目标失败为跨层结论越界的样本，确认 `O27` 优先，`O23`/`O31` 只有在观测质量或观测累积本身是目标失败时才进入候选。

### Complete experiment verification

- 复跑 2026-07-29 exp1 的完整实验链路，比较 Router 选中算子数、`not_applicable` 分支、`score_increased` 分支和耗时。
- 对四场景 discovery 数据和 v2.8 测试集抽样，人工检查 audit 是否能解释"为什么选、为什么不选、为什么不确定"。
- 对比改动前后不要只看候选数量，还要看漏召回：如果某个算子硬槽位满足且失败机制匹配，被遗漏必须作为 Prompt 缺陷修正。
- 验证方式不改实验流程：仍执行完整路由、生成、校验、作答、评分和效果分析。

### Experiment success signals

- 平均每道演化题的选中算子数下降到可人工解释的范围。
- `not_applicable` 主要来自真实生成阶段无法构造，而不是 Router 已可见的硬槽位缺失。
- `operator_improvement_notes` 能发现算子卡片本身的边界问题。
- 生成阶段的 `score_increased` 不再由"Router 过宽 + 题面泄漏"共同放大。

## Implementation Boundary

本方案只要求优化 Router Prompt 和 operator card 摘要，不设计新的实验阶段、替代评估流程或运行时拦截逻辑。实验验证按现有完整流程执行，优化效果通过完整实验产物中的算子选择、生成适用性、评分变化、耗时和审计记录判断。

## Sources and Evidence

- 当前 Router Prompt：已具备任务契约、证据拓扑、失败机制和审计字段，但候选定义仍偏召回。
- 当前 Router contract：`operator_decision_audit` 已能承载人工复盘信息，且不需要参与执行。
- 2026-07-29 exp1：暴露 `O11`/`O19`/`O22` 的硬槽位缺失误召回，以及分支数高但有效边界命中为零的问题。
- 四场景 discovery 数据与 v2.8 测试集：显示题目结构横跨路径、身份、角色、程序、阈值和结论层级，支持用可迁移分析方法而不是场景规则优化 Prompt。
