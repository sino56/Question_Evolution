# 建立全局 Memory 题型场景策略库

## 摘要

本方案将全局 Memory 定位为"题型场景策略库"，而不是局部 Memory 的跨实验拼接。局部 Memory 继续记录单次实验中样本、节点、算子、评分和失败的事实；全局 Memory 只保存经过归纳、准入和版本治理后的 场景类型 × 题型结构 × 推理机制/虚高机制 × 算子策略。

读者是维护 Question Evolution 实验链路、路由策略和 Memory 机制的内部工程师。读完后应能设计全局 Memory 的写入准入、在线读取、索引缓存、更新发布、准确性评估和效率治理方案。

本方案的首版定位是受控证据层，而不是自动决策层。全局 Memory 可以为路由提供参考，也可以为算子 Prompt、Router Prompt、校验器和 taxonomy 的后续优化提供证据，但在没有离线回放和跨实验验证前，不应直接成为硬门禁。

## 问题背景

当前实验已经会在进化成功、失败或生成无效时写入 Memory。问题在于这些 Memory 更接近实验内事件账本：它们围绕样本、轮次、算子、前后分数和效果标签记录"本次发生了什么"。这种形态适合恢复、审计和局部复盘，但不适合作为跨实验的长期经验库。

如果把每次实验的局部 Memory 直接追加成全局 Memory，会产生以下问题：

1. 语义粒度错误。 全局库会按样本索引和算子结果增长，而不是按题型、场景和能力机制沉淀。
2. 复用价值有限。 新题仍需要从一堆历史样本中重新判断相似性，不能直接得到"这类题优先用什么策略"。
3. 噪声不断累积。 单次偶然降分、题面质量问题、Rubric 风险或 Judge 不稳定可能被误当成成功经验。
4. 读取成本失控。 全局 Memory 越来越大后，在线全量扫描或全量注入 Prompt 都不可行。
5. 恢复不可复现。 实验运行中如果读取不断变化的全局 Memory，已冻结路由和搜索计划会被隐式改变。

全局 Memory 的目标不是保存更多历史记录，而是让每次新实验都能快速获得可解释、可检索、可退役的题型-算子策略参考。

## 设计定位

### 局部 Memory 回答发生了什么

局部 Memory 是实验事实账本，回答：

- 哪个样本在第几轮使用了哪个算子；
- 生成题是否通过复杂度和可回答性校验；
- 前后 `score_rate` 如何变化；
- 结果是有效边界、失败、分数升高还是无效生成；
- 哪个分支、节点或候选产生了该事实。

局部 Memory 必须保留样本级和分支级溯源，便于恢复、审计和实验复盘。

### 全局 Memory 回答什么通常有效

全局 Memory 是策略知识库，回答：

- 遇到某类场景和题型结构时，历史上哪些推理机制最常导致虚高；
- 哪些算子对该机制更稳定有效；
- 哪些算子在相似题型上容易失败或生成无效；
- 这条策略的适用条件、排除条件、证据强度和版本边界是什么。

全局 Memory 不以单条样本或单个 branch 作为主要索引，而以可迁移的题型语义作为主索引。

### 低成功率实验仍能产生有价值的 Memory

如果当前实验中有效进化样本很少，全局 Memory 的正向推荐能力会很弱。此时不能因为少数偶然成功就升级 active 策略。

低成功率阶段的全局 Memory 应优先沉淀：

- 哪些题型和算子组合反复 `not_applicable`；
- 哪些算子导致 `score_increased` 或没有形成目标机制降分；
- 哪些失败来自题面泄漏、Rubric 风险、Judge 不稳定或格式负担；
- 哪些题型长期无法通过现有算子体系产生有效边界；
- 哪些系统组件更可能需要优化，例如 Router Prompt、算子内容、校验器或题型 taxonomy。

因此，全局 Memory 首版的价值不是"推荐哪个算子一定成功"，而是"避免重复失败、识别风险、定位系统瓶颈"。只有当某类正向策略被跨样本或跨实验验证后，才允许进入 active。

## 当前实现对齐

当前项目已经有局部 Memory、路由快照和算子收益统计的雏形。全局 Memory 首版必须接入这些现有契约，而不是另起一套并行状态机。

### 现有局部 Memory 产物

每次实验目录下的 `memory` 目录当前至少包含以下本地事实账本：

这些文件的职责仍然是局部实验事实记录。全局 Memory 发布流程只读取它们并编译候选，不应改变局部 Memory 的写入语义、幂等账本或恢复行为。

### 现有匹配键

当前本地 Memory 的相似匹配键是 `sample_signature`，主要由以下字段组成：

- `core_capability`；
- `claim_level`；
- `problem_shape`；
- `candidate_overscore_cause`。

因此，方案中的 `scene_family`、`question_form`、`reasoning_mechanism` 和 `overscore_pattern` 不能直接假定已经存在。首版必须增加一个确定性的 taxonomy 映射层，把现有画像和虚高诊断字段映射为全局检索键。该映射层需要版本化，并写入策略卡、运行时索引和发布报告。

### 现有路由契约

当前路由中，历史 Memory 和推荐算子只作为非绑定审计提示。它们可以进入审计解释，但不能自动覆盖确定性路由、LLM Router 返回的合法候选、运行时资格门禁或已冻结的 `operator_plan`。

全局 Memory 必须继承该约束：

- `shadow` 策略卡只进入审计，不改变 `selected_operator_ids`；
- `negative_strategy` 首版只提示风险或降权建议，不硬阻断；
- LLM Router 成功时，不把 Memory 推荐但 Router 未召回的算子追加到本次候选；
- 已发布 `operator_plan` 是恢复时的唯一执行来源，不能因新 Memory 重建。

### 现有快照和恢复锚点

当前路由已经有 `route_versions.memory_snapshot` 和 `search_state.memory_snapshot_id`。全局 Memory 不应新增一套脱离现有搜索状态的快照字段。首版应复用现有字段：

- `route_versions.memory_snapshot` 写入本次路由实际使用的 Memory 快照；
- `search_state.memory_snapshot_id` 从路由版本继承，作为恢复锚点；
- 如果同时使用局部 Memory 和全局运行时 Memory，快照 ID 应由局部 Memory 内容哈希与全局运行时快照 ID 共同组成；
- Router cache key 必须包含该快照 ID；
- 恢复时如果找不到原始全局运行时快照，应明确失败或降级为 `no-global-memory`，并在发布报告中记录，不能静默读取最新 Memory。

### 现有算子收益统计边界

`operator_performance.jsonl` 已经支持按历史收益排序候选算子。它回答的是"某算子在某类上下文中的历史产出效率如何"，而全局策略卡回答的是"某题型机制下哪些算子为什么适合、为什么不适合、证据强度如何"。

首版规则：

- 全局 Memory 不覆盖 `operator_performance` 排序；
- `operator_performance` 可以作为策略卡证据摘要的一项统计来源，但不能单独生成 `positive_strategy`；
- 二者冲突时，MVP 只在审计中并列展示冲突，不改变执行顺序；
- 后续 active 阶段若要合并二者，必须单独定义合并策略、权重和回放评估。

## 分类映射与字段所有权

全局 Memory 的主索引必须稳定，但当前项目的画像和诊断字段仍偏自由文本。首版不应让检索时的 LLM 临时自由分类，而应在发布阶段做确定性归一化。

映射层本身需要版本号，例如 `global-memory-taxonomy-v1`。策略卡、运行时 Memory、快照元数据和发布报告都必须记录该版本，方便后续 taxonomy 调整后重新编译原始账本。

## 数据契约

首版应新增全局 Memory 发布层 schema，但不替换现有局部 Memory schema。

现有 `operator_memory_entry`、`failure_memory_entry` 和 `invalid_generation_case` 仍然只表达局部事实。全局发布流程读取这些事实后生成发布候选；发布候选再经准入、分组和合成生成策略卡。

## MVP 边界

首版 MVP 的目标是验证"局部事实能否稳定编译为可检索、可解释的全局策略证据"，不是让全局 Memory 直接优化在线实验。

MVP 明确包含：

- 离线局部到全局抽取；
- `proposed`/`shadow` 策略卡、风险卡、系统诊断卡和优化信号卡；
- 运行时 Memory 紧凑摘要和倒排索引；
- 固定快照 ID；
- 仅审计的单样本检索；
- 发布报告和基础冲突列表。

MVP 明确不包含：

- active 策略；
- Router 执行结果变更；
- 自动发布到正式实验；
- 自动修改 Prompt、算子卡、Validator 或 Judge/Rubric；
- 向量数据库、学习排序或外部检索服务；
- 用来源实验自证策略有效。

MVP 的退出条件是：对同一批输入和同一快照，能稳定返回相同 Top-K 全局策略参考，并能解释每张卡来自哪些局部事实、为什么未进入 active、是否存在冲突或风险。

## Memory 分层

### L1. 局部实验 Memory

每次实验目录下保留本次实验独立的局部 Memory。它仍按当前实验链路写入，用于本次恢复、本次审计和实验结束后的发布输入。

局部 Memory 不直接等于全局 Memory。它只是全局归纳的原始证据来源之一。

### L2. 全局原始账本

全局原始账本是跨实验追加账本，保存所有被提交到全局发布流程的局部事实和溯源信息。

它应保存：

- 来源实验、轮次、样本、节点、分支和候选身份；
- 原始局部 Memory 事件；
- 评分配置、Judge 配置、Prompt 版本、schema 版本、operator policy 版本；
- 人工复核状态和发布状态；
- 发布批次、发布时间和准入策略版本。

原始账本只用于审计、回放、重新编译和策略卡溯源，不作为在线路由的直接检索对象。

### L3. 全局运行时 Memory

全局运行时 Memory 是运行时读取的归纳结果。它由全局原始账本和局部实验事实离线编译而来，形态是短小、可索引、可解释的策略卡。

在线路由、LLM Router 和策略分析只读取运行时 Memory 的索引、摘要和 Top-K 策略卡，不读取完整原始账本。

## 全局策略卡

全局 Memory 的核心单元是卡片。策略卡是其中一类，服务在线路由；风险卡、诊断卡和优化信号卡服务质量治理和离线研发。建议策略卡采用以下业务字段：

```json
{
  "memory_card_id": "GMEM-000001",
  "memory_type": "question_type_operator_strategy",
  "scene_family": "交通执法",
  "question_form": "多条件共同必要判断",
  "reasoning_mechanism": "共同必要条件遗漏",
  "overscore_pattern": "模型抓住显眼条件后忽略另一个必要条件，导致虚高得分",
  "recommended_operators": [
    {
      "operator_id": "O12_conjunctive_necessity",
      "priority": "high",
      "why": "适合把单条件命中改造成多条件共同必要判断"
    }
  ],
  "backup_operators": [
    {
      "operator_id": "O10_evidence_sufficiency_ladder",
      "why": "当题面更像最小充分事实集合时可作为备选"
    }
  ],
  "avoid_operators": [
    {
      "operator_id": "O16_close_alternative_normalization",
      "why": "该类题主要不是近似项混淆"
    }
  ],
  "applicability_conditions": [
    "题面存在两个以上同时必要条件",
    "候选回答只满足其中一个条件仍给出确定结论",
    "Rubric 对遗漏条件有明确扣分项"
  ],
  "negative_conditions": [
    "题面信息不足以判断任一必要条件",
    "低分来自格式负担、题面歧义或 Judge 不稳定"
  ],
  "evidence_summary": {
    "supporting_experiments": 3,
    "supporting_root_samples": 12,
    "effective_rate": 0.58,
    "invalid_generation_rate": 0.11,
    "average_delta_score_rate": -0.16,
    "manual_confirmation_rate": 0.67
  },
  "confidence": "medium",
  "status": "shadow",
  "version": "global-memory-card-v1"
}
```

该卡片只表达可迁移策略，不保存完整样本正文、完整评分明细或完整推理长文。

### 卡片类型

运行时 Memory 应区分不同卡片类型，避免把所有历史信号都塞进同一种"成功策略"。

`positive_strategy` 和 `negative_strategy` 主要影响下一次实验参考；`risk_pattern` 防止错误归因；`system_diagnosis` 指出是否应暂停盲目搜索并转向系统修复；`optimization_signal` 将实验结果转化为后续工程优化项。

### 优化信号卡

优化信号卡不直接参与路由。它记录"为什么应该优化某个系统组件"，用于后续方案制定和排期。

```json
{
  "card_id": "OPT-000001",
  "card_type": "optimization_signal",
  "signal_type": "router_over_recall",
  "affected_component": "router_prompt",
  "scene_family": "视频轨迹核查",
  "question_form": "路径/身份连续性判断",
  "reasoning_mechanism": "局部线索上推为闭环结论",
  "observed_issue": "Router 在缺少硬槽位时仍同时召回 O11、O19、O22 和 O28",
  "evidence_summary": {
    "supporting_experiments": 2,
    "supporting_root_samples": 7,
    "branches": 24,
    "not_applicable_rate": 0.33,
    "score_increased_rate": 0.58
  },
  "recommended_actions": [
    "在 Router Prompt 中增加硬槽位门禁",
    "强化 O11/O19/O22/O28 的不选条件",
    "把缺槽位算子写入审计记录，而不是 operator_candidates"
  ],
  "priority": "high",
  "status": "open"
}
```

首版建议支持以下 `signal_type`：

- `router_over_recall`：Router 召回过宽；
- `router_under_recall`：Router 漏召回有效算子；
- `operator_prompt_leak`：算子生成题面泄漏答案边界；
- `operator_not_applicable`：算子适用条件过宽；
- `operator_boundary_confusion`：相邻算子边界混淆；
- `validation_gap`：校验器没有拦住无效题；
- `taxonomy_gap`：画像分类太粗或不稳定；
- `judge_or_rubric_risk`：评分系统风险影响策略判断；
- `new_operator_needed`：稳定出现的机制没有合适算子覆盖。

## 分类维度

全局 Memory 的准确性依赖稳定分类维度。首版建议固定以下维度。

### 场景类型

场景类型用于约束业务上下文，但不能成为硬编码路由规则。可从已有题库和画像中逐步收敛，例如：

- 交通执法；
- 治安处置；
- 接警研判；
- 视频轨迹核查；
- 证据可靠性判断；
- 程序/规则适用；
- 多主体关系判断。

### 题型结构

题型结构描述题目要求回答者完成什么判断，例如：

- 多条件共同必要；
- 最小充分证据；
- 反事实阈值变化；
- 实体同一性冲突；
- 多实体角色绑定；
- 多阶段事件链断点；
- 路径拓扑联合可达；
- 观测可靠性冲突；
- 跨层结论校准。

### 推理机制

推理机制描述能力缺口，例如：

- 忽略共同必要条件；
- 把线索上推为事实；
- 把事实上推为处置结论；
- 近似项未分层；
- 竞争解释排序失败；
- 重复观测当作独立证据；
- 局部绑定冲突未消解。

### 虚高模式

虚高模式描述候选模型为什么能在原题得高分，例如：

- 抓住显眼线索但漏掉关键约束；
- 复述题面事实但没有完成关系闭合；
- 选择最安全措辞规避真正判断；
- 依赖题面泄漏的答案边界；
- 被 Rubric 或题型格式奖励了表面完整性。

## 读取路径

全局 Memory 的读取不采用"每道题全量扫描全部内容"，也不采用"实验启动时把所有完整 Memory 都送入 Prompt"。推荐采用实验级快照、索引常驻、卡片懒加载和 Top-K 注入。

```text
全局原始账本
  -> 离线编译
  -> 全局运行时 Memory + 索引
  -> 实验启动时固定 memory_snapshot_id
  -> 加载紧凑索引和热卡摘要
  -> 每道题基于画像和诊断构造查询键
  -> 检索 Top-K 策略卡
  -> 只把紧凑卡片摘要注入 Router 或审计上下文
```

### 实验快照

每次实验启动时固定一个 Memory 快照。当前项目已有 `route_versions.memory_snapshot` 和 `search_state.memory_snapshot_id`，因此全局 Memory 首版应复用这两个字段，而不是新增脱离路由和搜索状态的 `global_memory_snapshot_id`。

该快照包括：

- 运行时 Memory 文件版本；
- 索引版本；
- 编译策略版本；
- 策略卡 schema 版本；
- operator registry 和运行策略版本兼容信息。

实验运行期间，不因全局 Memory 后续更新而改变本次快照。恢复实验时必须继续使用同一快照。

如果本次实验同时读取局部 Memory 和全局运行时 Memory，`memory_snapshot_id` 应能同时区分两类来源。可采用以下组合形态：

```text
local:<local_memory_hash>|global:<global_serving_snapshot_id>
```

其中 `local_memory_hash` 来自本次路由实际加载的局部 Memory bank 内容，`global_serving_snapshot_id` 来自离线发布的运行时 Memory 快照。Router cache、路由版本、搜索状态和发布报告都应记录同一个组合 ID。

恢复规则：

- 已发布 `operator_plan` 存在时，不重新检索全局 Memory，不重建候选集合；
- 如果恢复时找不到原 snapshot，默认应失败并提示缺失，而不是静默读取最新 snapshot；
- 允许显式配置 `GLOBAL_MEMORY_MISSING_POLICY=no_global_memory` 做降级续跑，但必须写入审计字段；
- 快照 ID 变化后，旧 Router cache 不可复用。

### 启动时加载的索引

实验启动时加载轻量索引和热卡摘要，而不是加载完整原始账本。首版建议索引包括：

- `scene_family` -> `memory_card_ids`；
- `question_form` -> `memory_card_ids`；
- `reasoning_mechanism` -> `memory_card_ids`；
- `overscore_pattern` -> `memory_card_ids`；
- `operator_id` -> `memory_card_ids`；
- `risk_label` -> `memory_card_ids`；
- `status`/`confidence`/`version` -> `memory_card_ids`。

这些索引用于快速召回候选策略卡。

### 单样本检索

每道题完成画像和虚高诊断后，生成查询对象：

```json
{
  "scene_family": "交通执法",
  "question_form": "多条件共同必要判断",
  "reasoning_mechanism": "共同必要条件遗漏",
  "overscore_pattern": "抓显眼条件漏关键约束",
  "risk_labels": ["rubric_stable", "judge_stable"]
}
```

检索流程：

1. 使用倒排索引召回候选策略卡 ID；
2. 根据版本、状态、置信度和 stale 状态过滤；
3. 检查 `negative_conditions` 是否明显命中；
4. 综合匹配分、证据强度、失败率和新鲜度排序；
5. 只返回 Top-K 卡片摘要；
6. 需要人工解释或审计时再懒加载完整证据。

Top-K 首版建议为 3 到 8 张。超过该范围会增加 Router 负担，且可能把弱相关历史经验混入上下文。

## 排序策略

策略卡排序不应只看文本相似度，也不应只看历史有效率。建议首版采用可解释的加权规则：

```text
match_score =
  scene_match * 0.15
+ question_form_match * 0.25
+ reasoning_mechanism_match * 0.30
+ overscore_pattern_match * 0.15
+ evidence_strength * 0.10
+ version_freshness * 0.05
- risk_penalty
- stale_penalty
- contradiction_penalty
```

其中：

- `scene_match` 表示业务场景是否一致或相近；
- `question_form_match` 表示题型结构是否一致；
- `reasoning_mechanism_match` 是最高权重，因为它最接近算子选择原因；
- `overscore_pattern_match` 防止只按题型召回但虚高机制不同；
- `evidence_strength` 综合支持实验数、根样本数、有效率、失败率和人工确认率；
- `version_freshness` 降权旧 Prompt、旧 Judge 或旧 operator policy 下的经验；
- `risk_penalty` 用于题面质量、Rubric 风险或 Judge 不稳定；
- `contradiction_penalty` 用于同类卡片存在强反例时降权。

后续可引入 BM25、embedding 或学习排序，但首版应保留可解释规则，便于审计错误推荐。

## 写入与发布路径

全局 Memory 的写入不是实时写入，而是实验后发布。

```text
局部实验产物
  -> 局部 Memory 事实
  -> 发布候选抽取
  -> 质量门槛
  -> 分组与合成
  -> 策略卡更新
  -> 运行时索引重建
  -> 快照发布
```

### 局部到全局抽取映射

发布候选抽取必须从现有实验产物出发，并保留事实来源。首版建议固定以下输入和抽取口径：

抽取结果统一写为发布候选。每条发布候选至少包含：

- `candidate_id`；
- `candidate_type`：`success` / `failure` / `invalid` / `risk` / `optimization_signal`；
- 来源实验、轮次、根样本、node、branch、operator；
- 归一化后的 taxonomy key；
- 原始局部事实摘要；
- 证据类型和证据强度；
- Prompt、schema、Judge、operator registry 和 policy 版本；
- 人工复核状态；
- 推荐写入目标卡片类型；
- 拒绝或降级原因。

发布候选是原始账本和策略卡之间的中间层。这样可以先审计"抽取是否正确"，再审计"合成是否正确"，避免直接从局部 JSONL 跳到策略卡。

### 步骤 1：抽取发布候选

实验结束后读取局部 Memory、分支结果、效果分析、搜索摘要、算子表现和人工复核结果，抽取候选事实。

候选事实包括：

- 成功经验；
- 明确失败经验；
- 无效生成案例；
- 题面/Rubric/Judge 风险；
- 算子表现统计；
- Router 召回和算子边界混淆；
- 校验器漏拦、taxonomy 粒度不足和新算子缺口；
- 需要人工复核的争议样本。

### 步骤 2：应用准入门槛

不同类型 Memory 使用不同准入门槛。

成功策略候选至少要求：

- 进化题真实生成并通过复杂度/可回答性校验；
- 父子题直接比较形成稳定降分；
- 命中目标机制而不是题面泄漏或评分漂移；
- `hit_confidence` 不低于 medium；
- 不处于人工复核未通过状态。

失败策略候选至少要求：

- 失败类型明确；
- 能区分算子不匹配、生成失败、题型不适配、评分风险；
- 有可复用的避免条件或替代建议。

风险类候选至少要求：

- 能明确归类为题面质量、Rubric 风险、Judge 不稳定、格式负担或外部知识风险；
- 不被误写入成功策略卡。

优化信号候选至少要求：

- 能指向明确组件，例如 Router Prompt、算子卡、算子生成 Prompt、Validator、Judge/Rubric 或 taxonomy；
- 能说明观察到的是召回问题、生成问题、校验问题、评分问题还是分类问题；
- 至少包含样本数、分支数、失败率、无效生成率或人工复核结论中的一种证据；
- 不把单次失败直接写成优化结论，只能写成 `proposed` 或 `open` 信号。

### 步骤 2.5：决定卡片归属

准入后必须先判断候选事实应该进入哪类卡片，不能默认进入成功策略。

该分流是全局 Memory 准确性的关键。尤其在有效进化样本很少时，系统应更多写入 `negative_strategy`、`risk_pattern`、`system_diagnosis` 和 `optimization_signal`，而不是制造低证据成功策略。

### 步骤 3：按语义键分组

候选事实按以下语义键分组：

```text
scene_family
+ question_form
+ reasoning_mechanism
+ overscore_pattern
+ operator_id
+ version compatibility group
```

同一组内聚合成功、失败、无效生成、风险和反例证据。

### 步骤 4：更新策略卡

对已有策略卡：

- 增加新证据；
- 更新有效率、失败率、平均降分和人工确认率；
- 补充适用条件或排除条件；
- 如果新证据冲突，降低置信度或拆分子卡；
- 如果版本不兼容，创建新版本卡而不是覆盖旧卡。

对新模式：

- 首次进入 `proposed` 或 `shadow`；
- 不直接进入 active；
- 等待跨样本、跨实验或人工确认后升级。

### 步骤 5：重建运行时索引

发布完成后，离线重建运行时 Memory 和索引，产生新的全局运行时快照 ID，并由实验启动阶段组合进 `memory_snapshot_id`。新快照只影响后续新实验，不影响已运行或恢复中的实验。

### 步骤 6：生成发布报告

每次发布都应生成面向维护者的报告。报告至少包含：

- 新增、更新、降级、退役的卡片数量；
- 新增 active 策略及其证据摘要；
- 被拒绝写入成功策略的风险样本；
- 主要冲突和未解决问题；
- 新增优化信号及优先级；
- 回放或留出集评估结果；
- 下次实验建议使用的快照 ID。

## Memory 状态生命周期

全局策略卡必须有状态，而不是永久有效。

```text
proposed -> shadow -> active -> retired
              |         |
              v         v
           rejected  downgraded
```

### Proposed（提议）

单次或少量证据发现的新模式。只用于离线观察和人工复核，不进入在线 Router 上下文。

### Shadow（影子）

证据初步成立，但还不应影响正式路由。可进入 Router 审计上下文，但不能强制推荐、排序或 avoid。

### Active（活跃）

跨样本或跨实验验证后进入活跃策略库。可作为 Router 和规则路由的重要参考，但首版仍不建议直接硬门禁候选集合。

### Retired（退役）

以下情况应退役或降权：

- 新版本下连续失败；
- 适用条件被证明过宽；
- 同类反例明显增加；
- operator、Prompt、Judge 或 schema 版本不再兼容；
- 人工复核认为该模式混入题面或评分风险。

## 冲突处理

全局 Memory 必须允许冲突存在，不能简单覆盖。

典型冲突：

- 同一题型下某算子在一批实验有效，在另一批实验失败；
- 同一推理机制可由多个相邻算子压测；
- 相同场景下不同题型结构导致策略相反；
- 旧版本 Prompt 下有效，新版本下无效；
- 自动评分显示有效，但人工复核认为题面泄漏。

处理规则：

1. 先保留冲突证据，不删除任何来源事实；
2. 判断冲突来自场景、题型结构、机制、版本、生成质量还是评分风险；
3. 如果能解释，拆分策略卡或补充排除条件；
4. 如果不能解释，降低置信度并保持 shadow；
5. 只有经回放或人工确认后，才允许 active 策略升级或退役。

## 准确性治理

### 准入质量

全局 Memory 准确性首先取决于写入质量。禁止把以下情况写成成功策略：

- 因题面泄漏导致的降分；
- 因选项过于明显导致的低分；
- 因 Rubric 负权重异常导致的降分；
- 因 Judge 不稳定导致的偶然低分；
- 因生成题格式负担、题长负担或不可回答导致的低分；
- 因外部知识缺失导致的低分。

这些情况应进入风险 Memory 或失败 Memory。

### 证据阈值

首版可使用保守阈值：

- `proposed`：单次清晰证据即可进入；
- `shadow`：至少 2 个根样本或 1 次人工确认；
- `active`：至少 2 次实验、5 个根样本，且有效率、无效率和人工确认率达到配置阈值；
- `retired`：连续新证据反向，或版本不兼容，或人工复核判定不可迁移。

阈值应是配置项，不应写死在 Prompt 中。

### 探索比例

为避免确认偏差，活跃全局 Memory 不应让 Router 永远选择历史推荐算子。建议保留小比例探索：

- 对高置信 active 策略，允许大部分样本优先使用推荐算子；
- 对 shadow 策略，只作为审计和候选提示；
- 对低证据或冲突策略，保留非推荐算子探索；
- 定期检查"未命中 Memory 但实验成功"的样本，用于发现新策略。

### 离线回放

定期做离线回放评估：

```text
历史样本画像
  -> 使用当前全局 Memory 检索策略
  -> 模拟当时会推荐的算子
  -> 与真实实验结果比较
  -> 评估命中率、误导率和机会损失
```

回放指标包括：

- Memory 命中率；
- 命中后有效边界率；
- 命中后无效生成率；
- 推荐算子未成功但备选成功的比例；
- 未命中 Memory 但实验成功的比例；
- active 策略相对无 Memory 路由的提升。

### 留出集与自证控制

不能用生成某条 Memory 的同一批实验来证明该 Memory 有效。否则系统会形成指标循环验证。

评估应至少满足一种切分方式：

- 时间切分： 用较早实验生成 Memory，用后续实验评估；
- 样本切分： 只用部分根样本生成 Memory，用留出样本评估；
- 场景切分： 在同一机制跨不同场景时，用未参与归纳的场景验证泛化；
- 版本切分： Prompt、Judge 或算子版本变化后，旧 Memory 只能作为候选假设重新验证。

active 升级必须依赖切分后的回放、人工复核或后续实验结果，不能只依赖训练来源自证。

### 运行时安全规则

首版运行时必须遵守以下保护规则：

- 全局 Memory 只能影响审计、提示和排序参考，不直接删除合法候选；
- `negative_strategy` 只能降权或提示风险，不能默认硬阻断；
- `risk_pattern` 可以触发人工复核或 shadow 记录，不能证明能力边界；
- `optimization_signal` 不进入在线 Router 上下文；
- Top-K 摘要不得包含原始答案边界、标准答案或完整样本正文；
- 如果全局 Memory 检索失败，实验应回退到无全局 Memory 路径，而不是中断主流程。

## 效率治理

### 原始账本与运行时分离

全局原始账本可以很大，但在线运行时 Memory 必须小而稳定。运行时只加载：

- 索引；
- 策略卡摘要；
- active 和 shadow 的热数据；
- 与当前版本兼容的卡片。

完整证据、原始样本和长解释只在审计时加载。

### 缓存策略

采用三层缓存：

1. 实验级快照缓存。 启动时固定全局 Memory 快照和索引。
2. 进程内查询缓存。 相同 `scene_family` + `question_form` + `mechanism` + `overscore_pattern` 的查询复用 Top-K 结果。
3. 卡片懒加载缓存。 Top-K 命中后加载完整卡片，避免重复 IO。

### 规模阶段

不同规模使用不同实现复杂度：

首版不需要向量数据库。优先用结构化字段和倒排索引，因为题型、机制和算子本身已经是强结构信号。

### Prompt 预算

每道题注入的 Memory 内容必须受预算控制：

- 最多 3 到 8 张策略卡；
- 每张卡只注入推荐算子、避免算子、适用条件、排除条件、证据强度和风险提示；
- 不注入完整历史样本、完整分数明细、长推理过程；
- 对 Router 明确说明 Memory 是参考，不是候选硬门禁。

## 集成点

### 实验启动

新增全局 Memory 快照解析逻辑。每次实验启动时：

1. 解析全局 Memory serving path；
2. 读取运行时快照元数据；
3. 与本次实际加载的局部 Memory hash 组成 `memory_snapshot_id`；
4. 写入 `route_versions.memory_snapshot`、Router cache key 和搜索状态；
5. 记录到实验 manifest 或搜索状态；
6. 加载索引和热卡摘要。

首版建议通过显式配置启用全局 Memory，例如：

```text
GLOBAL_MEMORY_SERVING_PATH=...
GLOBAL_MEMORY_MODE=off|audit|router_context
GLOBAL_MEMORY_MISSING_POLICY=fail|no_global_memory
```

默认应为 `off` 或 `audit`，避免未验证的全局 Memory 改变正式实验行为。

### 画像与诊断

画像阶段需要输出更稳定的全局检索键：

- 场景类型；
- 题型结构；
- 推理机制；
- 虚高模式；
- 风险标签；
- 题型结构的版本化 taxonomy。

这些字段应尽量来自现有画像和虚高诊断，不应让全局 Memory 检索重新自由生成一套分类。

首版不要求画像 Prompt 立刻新增全部全局字段。更低风险的方式是：

1. 保持当前画像和虚高诊断输出不变；
2. 在离线发布和在线检索前增加 taxonomy normalization；
3. 将归一化结果写入全局 Memory audit 字段；
4. 只有当归一化稳定后，再考虑把字段前移到画像 Prompt 或 schema。

### 算子路由

路由阶段读取 Top-K 策略卡，并在路由上下文中暴露：

- 推荐算子及原因；
- 避免算子及原因；
- 适用条件是否满足；
- 排除条件是否命中；
- 证据强度和状态；
- 冲突或风险提示。

首版仍建议保持非绑定：Memory 影响解释和候选提示，不直接删除合法候选。

### Router 集成契约

当 `GLOBAL_MEMORY_MODE=router_context` 时，Router payload 可以新增一个紧凑字段，例如：

```json
{
  "global_memory_hints": {
    "snapshot_id": "local:<hash>|global:gmem-snapshot-20260730-001",
    "retrieval_status": "succeeded",
    "cards": [
      {
        "card_id": "GMEM-000001",
        "card_type": "positive_strategy",
        "status": "shadow",
        "confidence": "medium",
        "matched_keys": {
          "scene_family": "交通执法",
          "question_form": "多条件共同必要判断",
          "reasoning_mechanism": "共同必要条件遗漏"
        },
        "recommended_operator_ids": ["O12_conjunctive_necessity"],
        "backup_operator_ids": ["O10_evidence_sufficiency_ladder"],
        "avoid_operator_ids": [],
        "applicability_summary": "题面存在两个以上同时必要条件，且候选答案只满足其中一个条件。",
        "negative_condition_summary": "若低分来自题面泄漏、格式负担或 Judge 不稳定，不应视为成功策略。",
        "evidence_strength": "supporting_experiments=2; supporting_root_samples=6; effective_rate=0.50",
        "route_instruction": "audit_only"
      }
    ]
  }
}
```

约束：

- 不传完整原题、完整历史样本、标准答案、候选答案长文或完整评分明细；
- 不传可直接复制到生成题面、答案键或 Rubric 的提示句；
- `route_instruction=audit_only` 时，Router 只能在审计中引用，不得把推荐算子强行加入 `operator_candidates`；
- `shadow` 卡不能改变 `selected_operator_ids`、`avoid_operators` 或运行时资格门禁；
- active 卡即使后续启用，也只能进入已定义的合并策略，不能绕过 LLM Router、运行时资格和 `operator_plan` 恢复语义；
- Router payload 和测试必须确认长 Memory、原始响应和完整样本正文不会进入 Prompt。

如果 `GLOBAL_MEMORY_MODE=audit`，检索结果只写入路由审计或单独 sidecar，不进入 Router Prompt。MVP 应优先采用该模式。

### 实验收尾

实验结束后增加全局 Memory 发布步骤。该步骤可以先是独立离线命令，不阻塞主实验完成。

发布步骤完成后产生：

- 新原始账本批次；
- 新或更新的策略卡；
- 新运行时 Memory；
- 新快照 ID；
- 发布报告和质量指标。

MVP 阶段该步骤默认是独立离线命令，不由 `run_loop` 自动触发。自动触发只有在离线报告稳定、发布过程幂等、缺失输入可恢复、且不会影响主实验完成后才考虑。

### 离线优化闭环

全局 Memory 还应驱动离线优化，不只驱动在线路由。每批实验结束后，优化信号卡应汇总为以下研发输入：

- Router Prompt 是否需要收紧硬槽位、近邻算子边界或候选数量语义；
- 算子卡是否需要补充适用条件、排除条件和相邻算子差异；
- 算子生成 Prompt 是否存在题面泄漏、答案边界外显或格式负担；
- Validator 是否漏拦无效题、泄漏题或不可回答题；
- Rubric/Judge 是否存在不稳定、负权重异常或评分尺度漂移；
- taxonomy 是否需要新增或拆分 `scene_family`、`question_form` 或 `reasoning_mechanism`。

这些输出不应直接修改代码或 Prompt，而应进入后续优化方案、人工评审或 shadow 实验。

## 实施单元

### U0. 对齐现有契约与 taxonomy

- 目标： 先把全局 Memory 语义接到现有画像、虚高诊断、路由快照和局部 Memory。
- 范围： taxonomy normalization、字段来源表、`memory_snapshot_id` 组合规则、局部 Memory 与全局 Memory 的职责边界。
- 验证： 同一条实验记录能确定性生成 `scene_family`、`question_form`、`reasoning_mechanism`、`overscore_pattern` 和版本兼容组；缺字段时有明确 `unknown` 或低置信处理。

### U1. 定义全局 Memory schema

- 目标： 定义发布候选、策略卡、运行时索引、快照和发布报告的数据契约。
- 范围： 新增全局发布层 schema、状态枚举、证据摘要字段、版本字段和敏感内容禁止字段。
- 验证： schema 能表达推荐、备选、避免、适用条件、排除条件、证据强度、状态、版本和来源溯源；同时不能包含完整样本正文、标准答案或长答案边界。

### U2. 构建局部到全局的发布候选抽取

- 目标： 从局部实验产物抽取可发布候选事实。
- 范围： 读取局部 Memory、分支结果、效果分析、搜索摘要、Router 审计、`operator_performance` 和人工复核结果。
- 验证： 成功、失败、无效生成、风险和优化信号能分开输出；每条候选都有来源实验、分支、算子、版本和证据类型。

### U3. 增加全局准入门槛

- 目标： 防止偶然降分、题面泄漏、Rubric 风险或 Judge 不稳定污染全局 Memory。
- 范围： 准入规则、置信度门槛、人工复核状态、版本兼容检查。
- 验证： 低置信、需要人工复核、题面/Rubric/Judge 风险不会进入正向策略；MVP 不产生 active。

### U4. 实现策略卡合成

- 目标： 按语义键聚合发布候选，生成 `proposed`/`shadow` 策略卡、风险卡、诊断卡和优化信号卡。
- 范围： 分组、统计、冲突识别、适用条件补充、排除条件补充、状态流转。
- 验证： 多条局部事实能聚合为一张可读卡片；冲突证据不会被覆盖；`operator_performance` 只能作为辅助统计。

### U5. 编译运行时 Memory 与索引

- 目标： 生成运行时可高效读取的 Memory。
- 范围： 倒排索引、紧凑摘要、Top-K 检索输入、快照元数据。
- 验证： 不读取原始账本即可完成每道题的策略卡召回；运行时摘要不含原始答案边界或完整样本正文。

### U6. 增加仅审计的单样本检索

- 目标： 在不改变路由结果的情况下，为每道题输出 Top-K 全局策略参考。
- 范围： 查询构造、索引召回、过滤、排序、Top-K 摘要、检索 sidecar 或路由审计写入。
- 验证： 同一输入和 snapshot 重复检索结果稳定；缺失全局 Memory 时能按配置失败或降级；当前路由结果不变。

### U7. 增加实验快照加载

- 目标： 让每次实验固定读取同一份全局 Memory 快照。
- 范围： 实验启动、恢复、manifest、搜索状态、Router cache key 和路由版本。
- 验证： 恢复实验时不会因为全局 Memory 更新而改变已有路由或搜索计划；已发布 `operator_plan` 不被重建。

### U8. 增加 Router payload 集成

- 目标： 在 audit-only 稳定后，将紧凑的全局 Memory 提示暴露给 Router。
- 范围： Router payload 字段、Prompt 说明、schema/contract 测试、原始 Memory 过滤、`route_instruction` 语义。
- 验证： Router Prompt 不包含长 Memory、完整历史样本或答案边界；`shadow` 卡不改变 `selected_operator_ids`。

### U9. 增加离线回放评估

- 目标： 用历史实验验证全局 Memory 是否提升策略选择。
- 范围： 回放样本画像、模拟 Memory 推荐、对比真实实验结果。
- 验证： 输出命中率、误导率、无效生成率、机会损失和相对无 Memory 路由提升；明确留出集切分方式。

### U10. 增加优化信号卡生成

- 目标： 将实验失败模式转化为 Router、算子、Validator、Judge/Rubric 和 taxonomy 的优化依据。
- 范围： 识别过召回、漏召回、算子泄漏、边界混淆、校验缺口和新算子缺口。
- 验证： 优化信号卡不进入在线路由，但发布报告能按组件聚合优化建议和证据强度。

### U11. 增加发布报告与复核流程

- 目标： 让每次全局 Memory 更新可审计、可复核、可回滚。
- 范围： 新增/更新/降级/退役摘要、拒绝原因、冲突列表、回放结果、建议快照。
- 验证： 维护者能从发布报告判断本次更新是否应被后续实验采用。

### U12. 增加留出回放保护与 active 升级策略

- 目标： 防止用生成 Memory 的同一批数据自证有效。
- 范围： 时间切分、样本切分、场景切分或版本切分的回放评估，以及 active 升级阈值。
- 验证： active 升级报告必须说明使用了哪种切分，且不只引用来源实验自身结果；无留出集时只能维持 shadow。

### U13. 增加退役与降级流程

- 目标： 让错误或过期策略退出 active。
- 范围： 新证据反向、版本不兼容、人工复核失败、冲突增多后的状态流转。
- 验证： active 策略可以被降级、拆分或 retired，而不是永久有效。

## 验收标准

1. 全局 Memory 的主数据单元是题型场景策略卡，不是样本级局部 Memory 条目。
2. 局部 Memory 与全局 Memory 的职责边界清晰：局部记录事实，全局记录可迁移策略。
3. 全局策略卡包含场景、题型结构、推理机制、虚高模式、推荐算子、避免算子、适用条件、排除条件和证据强度。
4. 实验启动时复用 `route_versions.memory_snapshot` 和 `search_state.memory_snapshot_id` 固定 Memory 快照，恢复时不会因全局 Memory 变化重建已冻结路由。
5. 单题读取使用索引召回和 Top-K 策略卡，不全量扫描原始账本，不全量注入 Prompt。
6. 成功、失败、无效生成、题面风险、Rubric 风险和 Judge 不稳定分开治理。
7. 低置信、待人工复核或版本不兼容的策略不能进入 active 强参考。
8. 全局 Memory 有 proposed、shadow、active、retired 状态流转。
9. 发布流程能处理冲突证据，不用覆盖方式丢失反例。
10. 离线回放能评估 Memory 命中率、误导率和相对无 Memory 路由的收益。
11. 有效进化样本很少时，系统不会强行生成 active 正向策略，而是沉淀失败、风险、系统诊断和优化信号。
12. 优化信号卡能为 Router Prompt、算子内容、Validator、Judge/Rubric 和 taxonomy 优化提供证据，但不参与在线路由。
13. active 升级不能使用来源实验自证，必须依赖切分回放、后续实验或人工复核。
14. 每次发布都有报告说明新增、更新、降级、退役、冲突、拒绝原因和推荐快照。
15. 能从一次已完成实验产物生成发布候选，但不修改原实验产物、局部 Memory 或搜索状态。
16. 能编译出运行时 Memory、倒排索引和快照元数据，并生成稳定快照 ID。
17. 同一输入记录、同一 taxonomy 版本和同一 snapshot 下，Top-K 检索结果稳定。
18. 缺失全局 Memory 文件时，系统按配置失败或降级为 `no-global-memory`，不能静默读取最新 snapshot。
19. Router payload 不包含原始答案边界、完整历史样本正文、完整评分明细或原始 Memory。
20. MVP 不产生 active 策略，只产生 `proposed`/`shadow` 卡片、风险/诊断/优化信号和发布报告。
21. 现有"Memory 不覆盖确定性路由、LLM 合法候选和已冻结 `operator_plan`"的语义保持成立。
22. `operator_performance` 与全局 Memory 的职责边界清晰；MVP 中全局 Memory 不覆盖收益排序。

## 推进计划

### 阶段 0：文档与 taxonomy 对齐

- 固定全局 Memory 的概念边界；
- 定义 `scene_family`、`question_form`、`reasoning_mechanism` 和 `overscore_pattern` 的首版 taxonomy normalization；
- 明确局部事实和全局策略卡的字段映射。
- 明确 `memory_snapshot_id`、`route_versions.memory_snapshot`、Router cache 和搜索状态的复用规则。

退出条件：维护者能用同一套术语解释局部 Memory、原始账本、运行时 Memory 和策略卡。

### 阶段 1：MVP shadow 全局 Memory

- 实现发布候选、策略卡、运行时索引、快照和发布报告 schema；
- 从历史实验和新实验中离线生成 `proposed`/`shadow` 策略卡；
- 生成风险卡、系统诊断卡和优化信号卡；
- 构建运行时 Memory 索引；
- 单题检索只写审计报告，不影响路由；
- 不产生 active 策略，不自动触发正式发布。

退出条件：能够在不改变实验结果的情况下，为每道题输出 Top-K 全局策略参考和解释。

### 阶段 2：Router 上下文集成

- 将 Top-K 策略卡摘要加入 Router 紧凑 payload；
- 保持非绑定，不直接删除候选；
- 记录 Memory 命中、推荐、实际选择和结果；
- 增加 payload 测试，确认长 Memory、完整样本正文和答案边界不会进入 Prompt。

退出条件：能评估 Memory 命中后是否提升有效边界率，且不会明显增加无效生成。

### 阶段 3：active Memory 治理

- 建立 active 升级规则；
- 支持降级、退役和策略拆分；
- 增加离线回放和发布报告；
- 根据证据决定是否允许 active Memory 影响排序。

退出条件：全局 Memory 能稳定提升策略选择效率，并具备可解释的错误恢复机制。

### 阶段 4：优化反馈闭环

- 将优化信号卡定期汇总为 Router Prompt、算子内容、Validator 和 taxonomy 的优化 backlog；
- 对已实施的优化做前后对比；
- 将优化后的实验结果回写为新的证据，而不是直接覆盖旧结论。

退出条件：全局 Memory 不仅能服务在线路由，还能稳定指出下一轮系统优化方向。

## 风险与缓解

## 非目标

- 不把局部 Memory 的 JSONL 文件简单合并为全局 Memory。
- 不在首版引入向量数据库或复杂学习排序。
- 不让全局 Memory 直接硬门禁在线候选集合。
- 不把题面质量、Rubric 风险或 Judge 不稳定写成成功算子经验。
- 不让优化信号卡直接驱动代码或 Prompt 自动修改。
- 不用来源实验自身结果证明 active 策略有效。
- 不要求主实验等待人工复核后才能完成。
- 不改变已发布 `operator_plan` 的恢复语义。

## 首版决策

以下决策用于收窄首版范围，避免实现时重新分叉。

1. 分类来源。 首版不新增独立画像 Prompt 字段，而是基于现有画像、虚高诊断、准入场景和 metadata 做确定性 normalization；等归一化稳定后再考虑前移到画像阶段。
2. 运行模式。 MVP 默认是 audit，只生成单样本全局 Memory 提示和发布报告；不改变 Router 执行结果。
3. Router 接入。 Router 上下文集成只进入 LLM Router 紧凑 payload；规则路由首版只记录审计，不读取策略卡调整候选。
4. 发布触发。 全局 Memory 发布首版是独立离线命令，不由主实验自动触发，也不阻塞主实验完成。
5. active 策略。 MVP 不生成 active 策略；低成功率阶段只能生成 `proposed`/`shadow`、风险卡、系统诊断卡和优化信号卡。
6. 留出集默认。 有时间顺序清晰的实验时优先时间切分；没有足够后续实验时使用样本切分；场景切分和版本切分作为后续补充。
7. 优化信号归档。 首版先进入发布报告；是否同步到独立 backlog 由后续排期决定。
8. 缺失 snapshot。 默认 fail-fast；只有显式配置 `no-global-memory` 降级时，才允许继续运行并写入审计。

### 开放问题

1. active 升级阈值应按全局统一配置，还是按算子族和场景族分别配置？
2. 人工复核是只审核 shadow 升级 active 的策略卡，还是也审核高影响失败策略？
3. `operator_performance` 与 active 全局 Memory 后续若共同影响排序，合并权重和冲突优先级如何定义？
4. taxonomy 低置信分类的人工修正是否写回原始账本，还是只写入下一次发布报告？
5. 优化信号卡进入独立 backlog 时，是否需要状态同步、负责人和关闭标准？