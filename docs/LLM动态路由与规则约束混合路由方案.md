# LLM 动态路由与规则约束混合路由方案

- 适用范围：Question Evolution 的样本画像、算子路由和多算子分支搜索入口
- 相关方案：docs/后续优化方案/基于父节点的多算子分支搜索.md

## 1. 方案摘要

本方案将当前基于固定关键词的算子路由升级为"LLM 负责结构识别和候选召回，规则负责约束、兜底和可审计调度"的混合路由。

LLM 不直接决定唯一算子，也不负责资格、校验、评分或发布。它读取原题及其评测上下文，识别题目中的推理对象，召回一个有序的候选算子集合，并为每个候选提供结构证据。程序规则随后校验算子身份、适用状态、历史状态、avoid 和路由预算，生成最终的 operator_route。Search Coordinator 是 operator_plan 的唯一构建、持久化和状态所有者，它根据路由结果和运行策略建立完整计划。

动态路由阶段固定使用 GPT 系列模型。默认配置为 `ROUTER_MODEL=GPT_MODEL`，当前基线对应 `gpt-5.4`；路由并发上限固定为 `ROUTER_CONCURRENCY=20`。Router 使用独立任务并发池，不与画像、题目进化、答案、Rubric 或评分阶段共用同一个 semaphore；当未来多个阶段发生时间重叠时，只共享 provider/API-key 维度的全局限流器与配额视图。

该方案解决两个相互独立的问题：

1. 让路由可以从题目结构识别 O19–O33，而不是依赖诊断文本刚好出现固定关键词；
2. 让 LLM 路由失效、漏召回或输出非法结果时，系统仍可通过确定性规则和完整算子计划保持可运行、可恢复和可审计。

本方案不改变候选题校验、答案生成、Rubric、Score Prompt、候选模型回答、Judge、Memory 业务准入或发布门槛。混合路由只改变"算子如何被识别、排序和加入搜索计划"。

---

## 2. 问题背景

### 2.1 当前数据流

当前流水线的相关数据流是：

```text
已评分样本
  ↓
profile_samples.py
  ↓  sample_profile + overscore_diagnosis
select_evolution_candidates.py
  ↓  evolution_action
operator_router.py
  ↓  primary_operator + backup_operators + avoid_operators
question_evolution.py
  ↓  按 num_candidates 截取候选算子
候选校验、答案、Rubric、评分
```

`profile_samples.py` 生成两组关键字段：

- `sample_profile`：核心能力、结论层级、问题形态、推理粒度、回答方式和风险画像；
- `overscore_diagnosis`：高分可疑原因、目标失败模式和是否值得进化。

当前 `operator_router.py` 主要读取 `candidate_overscore_cause` 与 `target_failure_mode`，并用确定性关键词规则匹配 `primary_operator` 和 `backup_operators`。`question_evolution.py` 随后只从这两个列表中按 `num_candidates` 截取前几个算子。

### 2.2 当前实验暴露的问题

在 `experiments/2026-07-23/exp` 中，O19–O33 的问题不是生成失败或校验失败，而是没有被路由召回：

- 5 轮路由记录中，O19–O33 的路由次数为 0；
- 28 个候选中，O19–O33 的生成次数为 0；
- 已生成的候选均通过原有形式校验；
- 诊断文本主要使用 O10–O18 的旧 taxonomy 表达。

这说明当前机制把"诊断语言是否包含指定关键词"误当成了"题目是否需要某种结构推理"。例如：

- 断点追踪和同一人确认可能被诊断成"排除替代解释混淆"，但结构上更接近 O22/O29；
- 多阶段车辆链可能被诊断成"答案写太满"或"信息闭包"，但结构上更接近 O20/O22/O28；
- 时间差和阈值问题可能被诊断成"反事实后结论层级未重排"，但结构上更接近 O25/O26。

### 2.3 现有多算子分支方案与本方案的关系

`基于父节点的多算子分支搜索.md` 已经解决了"未进入首批候选的算子不能继续尝试"和"多个候选只保留一个进入评分"的搜索层问题。该方案规定：

- 初始路由只决定算子顺序，不永久排除未命中的可生成算子；
- 每个适用算子可在同一个父节点下建立独立分支；
- 每条分支与同一个直接父节点比较分数；
- `not_applicable`、重复、预算和分支终止需要独立记录。

本方案位于该搜索方案的上游，负责为 `operator_plan` 提供更高质量的排序和候选召回。它不能替代多分支搜索，也不能让 LLM 路由成为唯一覆盖保障。

---

## 3. 目标与非目标

### 3.1 目标

- R1. 让路由器能够根据原题结构识别推理对象，而不是只依赖 `overscore_diagnosis` 中的固定词面。
- R2. 让 O19–O33 可以通过结构证据进入候选召回集合，同时保留确定性路由作为基线和兜底。
- R3. 让每个路由候选都有可审计的 `reasoning_object`、证据片段、置信度和来源标记。
- R4. 让非法 operator、未启用 operator、仅校验 operator、当前模式不允许或已进入终态的 operator 在模型调用前被规则过滤；样本级前置事实不明确时保留为 `unknown`，不能因画像或 LLM 未识别而永久排除。
- R5. 让 `avoid_operators`、历史失败、`evolution_state.recommended_next_methods` 和 Memory 继续影响路由，但不被 LLM 输出绕过。
- R6. 让 LLM 超时、限流、空响应、JSON 解析失败、未知 operator 或低置信输出时，系统可以退回确定性路由或完整算子计划。
- R7. 让单分支模式和多算子分支模式共享同一份混合路由结果，并能记录 LLM 路由、规则路由和最终合并结果之间的差异。
- R8. 让路由效果、算子效果和评分效果可以分开评估，避免把"路由错了"误判为"算子无效"。
- R9. 让路由具备稳定的缓存、版本和重放信息，重复执行同一输入时能够解释结果变化。
- R10. 让 `single_branch` 继续保持现有数据语义，并让 `multi_operator_branch` 在编排、候选保留、效果分析和 Memory 幂等层按分支 ID 工作。
- R11. 让 operator 内容语义与运行资格分离，通过版本化运行策略明确 `forced`、`hybrid_shadow`、`natural` 和 `forced_coverage` 模式下的可调用范围。
- R12. 让完整 `executable_operator_pool` 与本次调度窗口分别持久化；预算截断只能产生 `partial coverage`，不能被误报为 `operator space exhausted`。
- R13. 让路由阶段使用 GPT 系列模型和独立的 20 并发池；跨阶段重叠时通过 provider 级限流协调总配额，不能让路由抢占进化或评分任务的并发槽位。

### 3.2 非目标

- 路由阶段的 GPT 不负责判断题目是否通过复杂度、可回答性或其他已有校验；这些工作仍由原有校验阶段执行。
- 路由阶段的 GPT 不写入或覆盖题目、参考答案、Rubric、Score Prompt、候选答案或 Judge 结果。这不是取消这些阶段的 LLM，而是要求画像、路由、题目进化、答案、Rubric 和评分阶段各自只执行自己的角色。
- 不用路由置信度自动确认边界候选有效。
- 不新增语义相似度校验或"合理降分"自动判定。
- 不改变既有 Memory 的业务准入、写入时机和数据结构；只允许增加技术审计字段或分支级幂等信息。
- 不把 O19–O33 的自然路由结果和强制算子验证结果混为同一指标。
- 不在本方案中实现纵向子节点递归；纵向搜索仍由另行方案决定。
- 不用"所有注册算子"绕过资格状态、`validation_only`、`data_gated` 或其他既有启用约束。

---

## 4. 核心设计原则

### 4.1 LLM 是召回器和排序器，不是唯一门禁

LLM 可以识别题目结构、补充当前诊断未表达的 `reasoning_object`，并对候选算子排序。但 LLM 的漏召回不能直接意味着算子不适用，LLM 的高置信也不能直接意味着算子适用。

完整候选池必须由规则层根据注册、模式资格、静态生成能力和父节点终态确定。多分支模式下，LLM、诊断、Memory 和数据槽位只决定 `operator_plan` 的顺序；没有进入 LLM top-k 的可执行算子仍必须保留，除非静态策略禁止调用，或已有权威完整数据明确证明不适用。

### 4.2 资格、路由和 fallback 分层

三者含义必须分开：

- 资格：该算子是否具备进入某类实验或生产路由的条件
- 路由：在具备资格的算子中，当前题目优先尝试哪些
- fallback：LLM 和规则首选均不可用时，哪些算子仍可被尝试

资格不足的算子不能因为 LLM 高置信而进入正式自然路由。但样本适用性暂时无法判断时，不能直接记录为 `not_applicable`。它应以 `unknown` 保留在完整可执行池中并降低优先级；只有权威完整数据明确证明前置不成立，或算子实际返回结构化 `not_applicable`，才能关闭当前父节点下的该算子分支。

### 4.3 阶段角色隔离与证据边界

路由证据用于审计和候选排序，不进入题目生成、答案、Rubric、Score Prompt 或 Judge 的业务输入。算子生成 Prompt 仍只接收原题和事实过滤所需的参考材料，遵守 `prompts/operators/base.py` 的事实闭包设计。

"路由阶段不修改题目、Rubric 或 Judge"描述的是路由 GPT 的写入边界，不是禁止后续阶段使用 LLM。各阶段的 LLM 角色和写入范围必须保持隔离：

- 样本画像：profile GPT 诊断题目能力、虚高原因和目标失败模式；只写入 `sample_profile`、`overscore_diagnosis` 和 profile trace；不得写入 operator ID、题目、Rubric 或评分结果。
- 动态路由：router GPT 识别 `reasoning_object`、召回并排序 operator；只写入 `operator_route` 中的 route metadata 和 LLM route trace；不得写入题目、参考答案、Rubric、Score Prompt、候选答案或 Judge 结果。
- 题目进化：evolution GPT 按指定 operator 改写题目；只写入 `question_evolved`、候选题和 operator generation metadata；不得写入父题评分、Judge 结果或资格状态。
- 答案生成：answer/candidate 模型回答进化后的题目；只写入 candidate answer 和 answer trace；不得写入题目、Rubric、路由结果或 Judge 分数。
- Rubric 生成：rubric GPT 为当前题目生成评价标准；只写入 `rubric` 和按既有阶段契约生成的 `score_prompt`；不得写入路由排序、父子分数或 Judge 结果。
- 评分/Judge：Judge GPT/Qwen 按既有 Rubric 评分；只写入 `scoring_result`、score trace 和聚合分数；不得写入题目、`operator_plan` 或路由优先级。

路由 metadata 可以被后续协调器用于调度和审计，但必须从传给题目生成、答案、Rubric 和 Judge 的业务字段中剥离。后续阶段的 LLM 仍按各自职责正常调用，不因引入动态路由而被删除或合并。

### 4.4 发现预算与生产预算分离

专项发现实验可以提高路由候选数和算子覆盖，生产模式可以使用更小的 top-k 和更严格的请求预算。两种模式必须在 manifest 中显式记录，不能用发现模式的覆盖率直接声称生产路由质量。

### 4.5 路由并发与跨阶段并发

Router 的 `ROUTER_CONCURRENCY` 固定为 20，表示同一 Router 进程内最多 20 个并发路由请求。它不是所有 LLM 阶段的共享并发数。

当前 `run_loop.sh` 以阶段串行方式执行 profile、route、evolution、answer、Rubric 和 scoring，因此路由与后续阶段没有同时占用同一进程并发槽位。此时不应把各阶段合并到一个 semaphore：

- 路由失败和重试不会拖住进化题目生成；
- 评分高延迟不会让路由请求饥饿；
- 每个阶段的 QPM、超时、缓存命中和失败率可以独立统计；
- 路由阶段固定 20 并发，不会因为下游阶段临时调整而改变实验定义。

如果未来 Search Coordinator 让不同样本的路由、进化和评分阶段重叠，应引入 provider 级 GlobalRateLimiter，按 base_url + credential pool + model family 共享总请求/令牌配额。各阶段仍保留自己的任务池：

```text
Router task pool       = 20
Evolution task pool    = 独立配置
Rubric task pool       = 独立配置
Scoring/Judge pool     = 独立配置
Provider global limit  = 对上述池的总请求和 token 施加上限
```

全局限流器可以暂缓或排队请求，但不能把某个阶段的任务 semaphore 直接借给另一个阶段。实际有效并发为阶段池上限与 provider 剩余配额的较小值，并必须在 manifest 中记录排队、限流和降级原因。

### 4.6 单一状态所有者

`operator_route` 与 `operator_plan` 不能由两个模块独立重建：

- Router 只输出版本化的 `executable_operator_ids`、`priority_operator_ids`、`operator_applicability`、`excluded_operators`、`avoid_operators` 和排序证据；
- Search Coordinator 根据 route revision 构造一次 `operator_plan`，并成为该计划的唯一状态所有者；
- `operator_plan` 一旦持久化，恢复时不得根据新 route 或当前 registry 静默重建；
- operator 状态、分支 ID、生成次数、终态和剩余预算只由 Search Coordinator 更新；
- 需要使用新 Memory、registry 或 routing policy 时创建新的 route revision 和搜索运行，不覆盖旧计划。

---

## 5. 混合路由整体架构

```text
[已评分样本]
     |
     +-- evolution_action 是否要求进化? -- 否 --> [透传或停止]
     |
     +-- 是 --> [规则预处理与资格过滤]
                    |
                    +-- [确定性 baseline route] --+
                    |                              |
                    +-- [LLM 结构识别与候选召回] ---+--> [规则合并]
                                                           |
                                                           v
                                      [状态 / Memory / avoid / 预算约束]
                                                           |
                                                           v
                         [operator_route：完整可执行池 + 优先顺序]
                                                           |
                                                           v
                                                      [搜索模式]
                                                       /      \
                                                      v        v
                                            [single_branch]  [multi_operator_branch]
                                        top-k 候选生成与选择      冻结 operator_plan
                                                                        |
                                                                        v
                                                            [兄弟分支完整评分闭环]
```

### 5.1 阶段一：进化准入

继续沿用 `select_evolution_candidates.py` 的 `evolution_action`。以下情况不调用路由 LLM：

- `pass_through_or_scoring_noise`；
- `stop_evolution`；
- 样本缺少必要评分上下文；
- profile 解析失败且现有准入逻辑判定不应进化。

路由层不得重新判断样本是否需要进化，避免画像、路由和候选准入互相覆盖。

### 5.2 阶段二：规则预处理

规则预处理从输入记录构造一个只读路由上下文，至少包括：

- 原题、参考答案和必要的事实标识；
- `sample_profile`；
- `overscore_diagnosis`；
- `evolution_action`；
- `evolution_state`；
- 现有 `operator_route` 或历史路由结果；
- operator registry 中的启用、资格、生成和校验属性；
- operator/failure Memory 的摘要；
- 当前搜索模式和剩余预算。

预处理使用独立、版本化的 `OperatorRuntimePolicy` 判断静态运行资格。只有以下对象可以在样本调用前硬排除：

- 未注册或 ID 不规范的 operator；
- `enabled=false`；
- `validation_only=true`；
- 当前模式不允许的资格状态，例如 `natural` 模式下的 `qualification_only` 或 `suspended`；
- 已在当前父节点被标记为 `not_applicable`、`duplicate_exhausted` 或已完成分支；
- 权威且完整的事实账本明确证明前置条件不成立的 operator。

画像没有输出 required slot、LLM 没有召回结构或样本字段缺少可定位事实，都只能得到 `applicability_status=unknown`，不能进入上述硬排除集合。预处理结果只保留 operator ID、三态适用性和必要的可审计摘要，不把整个 registry 无限制塞入 LLM Prompt。

`OperatorRuntimePolicy` 不属于 operator 内容 spec，至少包含：

```json
{
  "operator_id": "O29_entity_identity_conflict_resolution",
  "policy_version": "operator-runtime-policy-v1",
  "qualification_status": "qualification_only | qualified | suspended",
  "generation_enabled": true,
  "natural_routing_enabled": false,
  "data_gate": {
    "required_slots": [],
    "policy": "all_required"
  }
}
```

模式与资格矩阵固定为：

- `forced`：`qualification_only` 和 `qualified` 均可显式调用，`suspended` 不可调用；用于算子内容资格实验。
- `hybrid_shadow`：`qualification_only` 和 `qualified` 只记录召回、不执行，`suspended` 不进入候选；用于路由离线或影子评估。
- `natural`：仅当 operator 为 `qualified` 且 `natural_routing_enabled=true` 时执行；`qualification_only` 和 `suspended` 均不执行；用于正式自然路由。
- `forced_coverage`：`qualification_only` 可按预注册集合调用，`qualified` 可调用，`suspended` 不可调用；用于专项覆盖实验。

`data_gated` 不作为与 qualification 并列的内容状态，而是 runtime policy 中的附加 gate。`required_slots` 的判定必须返回三态：

- `applicable`：权威事实明确满足
- `not_applicable`：权威且完整事实明确缺失
- `unknown`：画像、诊断或事实账本不足以判断

`not_applicable` 可以在模型调用前关闭该父节点分支；`unknown` 必须保留在 `executable_operator_pool` 中，通常降到优先队列尾部，并允许算子在生成时自行返回结构化 `not_applicable`。这样可以避免画像缺字段导致 O19–O33 永远不被调用。

### 5.3 阶段三：确定性 baseline route

保留当前 `_base_rule_route()` 和 `build_operator_route()` 的确定性语义，作为以下用途：

- LLM 不可用时的主回退；
- LLM 候选的交叉验证来源；
- 回归比较基线；
- 线上 shadow 模式的对照结果。

规则路由仍可以根据 `candidate_overscore_cause` 和 `target_failure_mode` 命中 O10–O33，但不应把未命中的 operator 视为永久不适用。

### 5.4 阶段四：LLM 结构识别和候选召回

LLM 路由器使用固定、版本化的 system prompt 约束分析方法；每次调用再通过 per-sample user prompt 提供题目事实、画像、诊断、状态摘要和当前可路由的 operator cards。固定指令不得混入样本事实，动态上下文不得自行改变路由规则或 operator 资格。

LLM 读取完整的路由上下文，重点识别：

- 题目中的实体、角色、节点、事件阶段、物品、路径、观测、假设、来源和结论层级；
- 需要回答者自行判断的关系，而不是题面已经给出的答案；
- 题目主要要求判断什么结构变化会改变结论；
- 当前失败模式属于哪个 `reasoning_object`；
- 哪些相邻 operator 看起来相关但不应优先。

LLM 只返回结构化路由结果，不生成进化题目。

### 5.5 阶段五：规则合并和最终计划

规则合并器把 baseline route、LLM route、Memory、`recommended_next_methods` 和历史失败状态合并成最终 `operator_route`。它同时输出完整的 `executable_operator_ids` 和每个 operator 的三态 `operator_applicability`，不能只输出 top-k。

合并顺序必须满足：

```text
静态不可用和权威事实明确不适用
→ evolution_state 中明确要求切换的 operator
→ LLM 与规则共同支持的 operator
→ LLM 结构证据支持的 operator
→ deterministic baseline route
→ 成功 Memory 推荐
→ `unknown` 的可执行 operator
```

`avoid_operators` 在 single-branch 模式中作为当前路由周期的排除约束；在 multi-operator-branch 模式中只降低优先级，不能把它误标记为 `not_applicable`。真正的永久排除由 `operator_plan` 中的终态负责。LLM 低置信、画像缺字段和 `required_slots=unknown` 都只能降低优先级，不能改变完整可执行池。

---

## 6. LLM 路由契约与提示词结构

### 6.1 模型与调用配置

动态路由必须使用 GPT 系列模型，不使用候选回答模型或 Judge 模型兼任路由。默认配置是：

```text
ROUTER_MODEL = GPT_MODEL
当前基线模型 = gpt-5.4
ROUTER_CONCURRENCY = 20
ROUTER_TEMPERATURE = 0 或实现支持的最低稳定值
ROUTER_MAX_REQUESTS_PER_SAMPLE = 1
```

`ROUTER_BASE_URL` 和 `ROUTER_API_KEYS` 可以按阶段单独配置；未提供时复用项目的 GPT provider 配置。复用 provider 配置不等于复用其他阶段的 semaphore、重试计数或任务队列。manifest 必须保存实际模型名、base URL 标识、Prompt 版本、并发 20、温度、timeout、重试和 provider limiter 版本。

如果配置解析出的 `ROUTER_MODEL` 不是允许的 GPT 路由模型，正式 hybrid 运行应在启动时失败；shadow 调试可以显式选择其他模型，但必须使用不同的 routing mode/version，不能混入 GPT 路由指标。

### 6.2 Prompt 分层与版本责任

每个 Router 请求由以下两层组成：

- system prompt：包含 Router 角色、分析步骤、事实闭包、适用性语义、JSON 输出约束和禁止事项；由路由实现维护 `router_prompt_version`；不随样本变化。
- user prompt：包含当前样本的路由上下文、operator cards、历史状态和预算摘要；其输入 hash 与 registry、runtime policy、Memory snapshot 共同决定缓存键；随样本变化。

system prompt 只定义"如何分析"，不能包含特定样本结论、硬编码的 operator 优先级或手工维护的 operator 语义。user prompt 只提供"分析什么"，不能覆盖 system prompt 的事实闭包、资格边界或输出 schema。

路由结果不得作为下一阶段题目生成、答案、Rubric、Score Prompt 或 Judge 的输入；它只服务于规则合并、审计和 `operator_plan` 排序。

### 6.3 Per-sample user prompt 输入字段

LLM 路由 Prompt 应复用 `prompts/router_prompt.py` 的职责边界，但扩展输入内容。建议按以下顺序组织：

1. `sample_id`、评分状态和 `evolution_action`；
2. 原题；
3. 参考答案或事实摘要；
4. 候选模型回答及评分摘要；
5. `sample_profile`；
6. `overscore_diagnosis`；
7. `evolution_state` 中与下一算子有关的字段；
8. 可用 operator card 的精简版；
9. 已尝试、失败、avoid 和 Memory 摘要。

不向 LLM 路由器传递不必要的 API key、原始内部调用日志或完整 Memory 内容。大字段应截断、摘要或通过稳定引用替代。

### 6.4 Operator card

LLM 不应只看到 operator 名称。每个可路由 operator 的 card 至少包含：

```json
{
  "operator_id": "O29_entity_identity_conflict_resolution",
  "ability_axis": "实体同一性冲突消解",
  "reasoning_object": "冲突绑定下的实体连续性与同一性",
  "target_errors": ["将局部相似误当全程同一"],
  "applicability_requirements": ["存在两个或以上竞争实体绑定"],
  "adjacent_boundaries": [
    "O19_multi_entity_role_binding",
    "O21_object_provenance_identity",
    "O22_path_topology_joint_reachability"
  ],
  "enabled": true,
  "qualification_status": "qualified",
  "routing_version": "..."
}
```

card 中的 `reasoning_object`、`target_errors` 和相邻边界来自现有 `OperatorPromptSpec` 的结构化字段；资格和启用状态由规则层提供。路由 Prompt 不应手工维护一份与 registry 脱节的 O19–O33 文本清单。

### 6.5 Router system prompt 模板

以下模板是 Router 的稳定系统提示词；实现时以独立文件维护，并将 router_prompt_version 写入 route metadata。`<...>` 占位符由每次调用的 user prompt 提供，不能在 system prompt 内补全。

```text
你是 Question Evolution 的 operator Router。你的任务是根据当前样本的已给事实和可路由 operator cards，识别需要被压测的推理结构，并返回有证据的 operator 候选排序。

工作边界：
1. 你只做结构识别、候选召回、相邻算子区分和排序；不改写题目，不生成答案、Rubric、评分建议，也不判断发布结果。
2. 只能使用 user prompt 中明确提供的原题、参考材料、回答、评分摘要、画像、诊断和状态事实。不得补造实体、角色、时间、路径、物品、数值、观测条件或竞争解释。
3. 先识别 reasoning objects，例如实体/角色绑定、事件阶段、物品来源、路径时间窗、观测可靠性、竞争假设、数量阈值、结论层级或程序不变量；再从给定 operator cards 中选择最匹配的候选。
4. 每个候选必须引用至少一个可定位的输入 evidence span，并说明该结构为何匹配该 operator、为何不优先选择相邻 operator。主题相似但没有结构证据时，不得排到高优先级。
5. `applicable` 表示输入存在明确结构证据；`unknown` 表示信息不足但不能证明不适用；不要仅因未识别到结构、置信度低或画像缺字段输出 `not_applicable`。`not_applicable` 只能在输入提供权威且完整的反证时提出，并给出证据。
6. 只使用 user prompt 给出的 operator_id；不得创造、改写或猜测 operator ID。不得绕过已给出的 avoid、历史终态、预算和运行资格摘要。
7. 返回严格合法 JSON，且只包含约定 schema 中的字段；不要输出 Markdown、解释性前后缀或题目改写内容。

输出目标：
- `reasoning_objects`：识别到的结构及 evidence spans；
- `operator_candidates`：按优先级排序的候选，每项包含 operator_id、rank、applicability、confidence、evidence_spans、why_fit 和 why_not_adjacent；
- `not_selected_reasons`：仅记录有证据的低优先或被排除原因；
- `router_comment`：简短说明本次路由的不确定性或多结构竞争。
```

system prompt 不直接决定最终资格、operator_plan 或搜索停止条件。LLM 响应必须先通过 ID、evidence span、资格、avoid、历史状态和预算的规则校验；规则层可拒绝或降级任何候选。

### 6.6 输出字段

LLM 输出必须是合法 JSON，建议结构如下：

```json
{
  "routing_schema_version": "hybrid-router-v1",
  "reasoning_objects": [
    {
      "name": "实体同一性冲突消解",
      "evidence_spans": ["断点后出现疑似目标", "存在竞争人员"],
      "confidence": 0.82
    }
  ],
  "operator_candidates": [
    {
      "operator_id": "O29_entity_identity_conflict_resolution",
      "rank": 1,
      "applicability": "applicable",
      "confidence": 0.82,
      "reasoning_object": "实体同一性冲突消解",
      "evidence_spans": ["断点后出现疑似目标", "竞争人员的时间路线也相容"],
      "why_fit": "需要判断竞争绑定是否足以保留同一性不确定性",
      "why_not_adjacent": {
        "O19_multi_entity_role_binding": "重点不是建立角色关系，而是消解冲突绑定"
      }
    }
  ],
  "not_selected_reasons": [],
  "router_comment": "..."
}
```

LLM 不得输出：

- 题目改写内容；
- Rubric 或评分建议；
- 不存在的 operator ID；
- 未出现在输入中的事实；
- 以 `not_applicable` 作为无证据的推断结论；
- 依赖题目长度、实体数量或固定选项数量的路由理由。

### 6.7 置信度使用规则

置信度只用于排序、日志和实验分析，不直接作为资格通过或样本适用条件。规则层可以设置成为 single-branch primary 所需的最低结构证据，但不能只根据 `confidence < threshold` 把 operator 标记为不适用或移出多分支完整可执行池。

推荐使用三档：

- `high`：有明确 `reasoning_object` 和至少一个可定位证据片段；
- `medium`：有结构线索，但与相邻 operator 仍有竞争；
- `low`：只有主题相似或泛化措辞，不足以提升优先级。

`low` 候选可以保留在 fallback 或搜索计划中，但不能成为 single-branch 的唯一 primary。

---

## 7. 规则约束与合并算法

### 7.1 硬约束层

规则层必须在任何 LLM 结果进入最终路由前执行：

1. operator ID 必须存在于 registry；
2. operator 必须启用并允许生成；
3. `qualification` 状态必须满足当前实验模式；
4. `validation_only` operator 不进入生成集合；
5. 当前父节点已经终止的 operator 不再次进入 plan；
6. `not_applicable` 必须来自权威完整事实的生成前证明，或算子实际返回的结构化结果；两者都必须有非空原因和证据引用；
7. `avoid_operators` 和失败记录不能被 LLM 重新放回 single-branch primary；
8. `recommended_next_methods` 必须经过 ID 校验、去重和 avoid 过滤；
9. 预算耗尽时不得创建新的路由任务；
10. 所有最终 operator 必须有来源和排序理由。

### 7.2 来源合并

每个候选保存来源集合，不只保存最终 rank：

```json
{
  "operator_id": "O22_path_topology_joint_reachability",
  "sources": ["llm", "deterministic_rule"],
  "source_rank": {
    "llm": 2,
    "deterministic_rule": 1
  },
  "support_count": 2,
  "final_rank": 1,
  "final_reason": "LLM 和规则均识别出路径、时间窗和竞争端点结构"
}
```

合并优先使用可解释的分层排序，而不是不可解释的单一浮点总分：

```text
Tier 0：状态机强制推荐，且通过硬约束
Tier 1：LLM 与 deterministic rule 共同支持
Tier 2：LLM 有结构证据支持
Tier 3：deterministic primary/backup
Tier 4：成功 Memory 推荐
Tier 5：适用性为 `unknown` 的其他可执行 operator
```

同一 Tier 内按以下顺序稳定排序：

1. `recommended_next_methods` 原始顺序；
2. 支持来源数量；
3. LLM 结构证据置信度；
4. operator card 的相邻边界匹配度；
5. `OPERATOR_FALLBACK_ORDER` 的稳定顺序。

任何排序都不能越过静态硬排除和预算限制；但排序低、LLM 未召回或适用性为 `unknown` 不能把 operator 移出完整可执行池。

### 7.3 路由结果

最终 operator_route 在兼容现有字段的同时增加审计字段：

```json
{
  "routing_mode": "hybrid",
  "primary_operator": "O29_entity_identity_conflict_resolution",
  "backup_operators": [
    "O22_path_topology_joint_reachability",
    "O19_multi_entity_role_binding"
  ],
  "avoid_operators": [],
  "executable_operator_ids": [
    "O19_multi_entity_role_binding",
    "O20_multistage_event_chain_breakpoint",
    "O22_path_topology_joint_reachability",
    "O29_entity_identity_conflict_resolution"
  ],
  "priority_operator_ids": [
    "O29_entity_identity_conflict_resolution",
    "O22_path_topology_joint_reachability",
    "O19_multi_entity_role_binding"
  ],
  "operator_applicability": {
    "O29_entity_identity_conflict_resolution": "applicable",
    "O22_path_topology_joint_reachability": "applicable",
    "O19_multi_entity_role_binding": "unknown",
    "O20_multistage_event_chain_breakpoint": "unknown"
  },
  "excluded_operators": [],
  "operator_candidates": [],
  "reasoning_objects": [],
  "llm_route_status": "succeeded",
  "llm_route_confidence": 0.82,
  "routing_reason": "...",
  "route_versions": {
    "router_schema": "hybrid-router-v1",
    "operator_registry": "...",
    "qualification_policy": "...",
    "memory_snapshot": "..."
  }
}
```

已有消费者只读取 `primary_operator`、`backup_operators`、`avoid_operators` 时应继续工作；新增字段用于审计、回放和多分支 `operator_plan` 构造。

### 7.4 Operator plan 构造

Router 不直接构造或持久化 `operator_plan`。它输出完整的 `executable_operator_ids`、排序后的 `priority_operator_ids`、三态 `operator_applicability` 和带理由的 `excluded_operators`。在 `multi_operator_branch` 模式下，Search Coordinator 使用 route revision 第一次构造并持久化计划。它应按以下规则构造：

```text
priority_operator_ids
→ executable_operator_ids 中未进入优先列表且 applicability=applicable 的 operator
→ executable_operator_ids 中 applicability=unknown 的 operator
→ 已被 avoid 但仍可执行、且尚未达到终态的 operator
```

静态不可生成、当前模式资格不足和 `validation_only` operator 需要记录排除原因，不进入待调用状态。画像缺字段、LLM 未召回、低置信和动态 required slots 未知不能进入排除集合。运行时 `not_applicable` 需要保留在 plan 中并进入终态。

完整可执行池和本次可调度窗口必须分开保存：

```json
{
  "executable_operator_ids": ["..."],
  "dispatch_window_operator_ids": ["..."],
  "omitted_due_to_budget": ["..."],
  "coverage_status": "complete | partial"
}
```

如果容量预算只允许调度窗口的一部分，未调度 operator 不能被标记为已尝试，样本终止原因应为 `operator_plan_budget_exhausted` 或 `partial_coverage`，而不是 `operator_space_exhausted`。只有完整可执行池中的全部 operator 到达终态时，才能使用 `operator_space_exhausted`。

专项验证不修改自然多分支的"边界候选达到 5 个即停止"规则。需要保证 O19–O33 全覆盖时，使用独立的 `forced_coverage` 调度模式：`boundary_target` 只记录，不作为停止条件；该模式的结果不得计入 natural routing 或自然多分支的边界产出指标。

### 7.5 适用性与候选校验边界

适用性判断和候选题校验是两个阶段，不能互相替代：

```text
静态资格/权威事实前置证明
  → 决定 operator 是否进入 executable pool
样本适用性为 unknown
  → 保留 operator，允许生成阶段自行返回 not_applicable
候选题生成
  → 执行原有复杂度、可回答性和格式校验
候选通过校验但分数上升或不变
  → 分支完成但无边界命中，继续下一个 pending operator
候选未通过校验
  → 按原有校验重试，耗尽后标记 validation_failed，继续下一个 pending operator
```

不能因为候选校验通过就反推路由一定正确，也不能因为路由置信度低或画像缺字段就跳过候选校验。只要 operator 仍在冻结的 executable pool 中，首批算子无效后就必须回溯到下一个 pending operator。

---

## 8. fallback、失败与恢复

### 8.1 LLM 路由失败

以下情况统一记录 `llm_route_status`，但不直接中止样本：

- 请求超时；
- 限流或网络错误；
- 空响应；
- JSON 解析失败；
- schema 字段缺失；
- 返回未知 operator；
- 证据片段不在输入文本中；
- 结果全部被规则过滤。

回退顺序为：

```text
evolution_state.recommended_next_methods
→ deterministic baseline route
→ operator Memory 推荐
→ 完整 executable operator plan
→ 无可用算子时 operator_space_exhausted
```

不得因为 LLM 失败而无条件回退 O10。也不得把 API 错误标记成 `not_applicable`。

路由状态必须使用有限枚举，并区分可重试与不可重试错误：

- `succeeded`：进入规则合并；不重试。
- `timeout`：使用独立路由重试预算，失败后使用 baseline fallback；最多重试一次。
- `rate_limited`：按现有 API 重试/退避机制处理，失败后使用 baseline fallback；最多重试一次。
- `network_error`：保留错误并使用 baseline fallback；最多重试一次。
- `empty_response`：记录失败并转 deterministic；不重试。
- `invalid_json`：记录原始响应摘要并转 deterministic；不重试。
- `schema_error`：记录字段错误并转 deterministic；不重试。
- `hallucinated_evidence`：拒绝对应候选，但保留其他合法候选；不重试。
- `all_candidates_rejected`：使用 deterministic route 或 executable plan；不重试。
- `skipped`：仅用于非进化样本或 shadow-only 模式；不重试。

建议为每个运行批次预注册路由 circuit breaker：至少观察 30 个实际路由请求后，在滚动 100 请求窗口内，若 transient 失败率超过 20%，或 `invalid_json` + `schema_error` + `hallucinated_evidence` 超过 5%，则本批次切换 deterministic-only。下一批次先用 10 个样本 probe；probe 通过后才恢复 hybrid。阈值、窗口和恢复条件必须进入 manifest，不能由运行中动态修改。

### 8.2 LLM 输出部分可用

如果 LLM 返回多个候选，其中部分合法，规则层保留合法候选，并把非法项放入 `rejected_operator_candidates`。只要至少有一个合法候选，不能把整次路由标记为失败。

### 8.3 断点恢复

路由结果需要带有以下稳定信息：

- 输入样本或父节点 ID；
- 路由输入 hash；
- LLM Prompt hash；
- LLM 模型和参数版本；
- operator registry 版本；
- qualification policy 版本；
- Memory 快照版本；
- 合并器版本。

恢复时：

- 已成功完成且版本一致的路由结果直接复用；
- LLM 失败但 deterministic route 已持久化的结果可以继续运行；
- 版本发生变化时建立新路由记录，不覆盖旧记录；
- 已完成的 operator 分支不因重新路由重复执行。

同一个父节点只允许有一个冻结的 `memory_snapshot_id` 和一个已持久化的 `operator_plan` revision。前序兄弟分支产生的新 Memory 不重排当前父节点的计划，只影响下一父节点或下一次实验运行。若必须使用新 Memory，必须建立新的 route revision，并明确这是新运行而不是恢复旧运行。

---

## 9. 与父节点多算子分支搜索的接口

### 9.1 职责边界

混合路由负责：

- 识别 `reasoning_object`；
- 召回和排序 executable operator；
- 记录来源、证据和版本；
- 为搜索协调器提供 `operator_plan` 初始顺序。

父节点搜索协调器负责：

- 维护父节点和子分支状态；
- 每个 operator 的生成、重复、校验和评分闭环；
- 父子分数比较；
- `boundary_candidate`、`no_score_change`、`score_increased` 等分支终态；
- 业务终止、请求预算、评分预算和断点恢复。

路由器不得读取子分支评分并在线修改 Judge 结果，也不得因为某个候选降分就自动确认有效边界。

### 9.2 单分支模式

single-branch 模式兼容现有行为：

- 使用最终 `primary_operator`；
- backup 按现有 `num_candidates` 规则参与候选生成；
- `avoid_operators` 仍作为当前路由周期的排除条件；
- 保存完整 hybrid route 作为审计和 shadow 对照。

### 9.3 多算子分支模式

multi-operator-branch 模式使用完整 `operator_plan`：

- route 只决定顺序，不决定最终覆盖上限；
- 每个 executable operator 最多建立一条横向分支；
- 同一父节点内按 plan 串行执行；
- 不同样本可以并发；
- 分支状态和 Memory 幂等规则遵循父节点搜索方案；
- 达到 5 个边界候选时记录尚未尝试 operator，不能称为"全算子覆盖"。

#### 9.3.1 无效分支后的回溯

多分支回溯不是重新调用 LLM 生成一份新的候选集合，而是从父节点初始化后冻结的 `operator_plan` 中继续取下一个 pending operator：

```text
当前 operator 生成不适用
  → 标记 not_applicable
当前题目重复耗尽
  → 标记 duplicate_exhausted
候选未通过原有校验
  → 标记 validation_failed
候选通过校验但分数上升或不变
  → 标记 score_increased / no_score_change
上述任一终态
  → 返回父节点
  → 选择下一个 pending operator
```

LLM 没有召回某个 operator、画像没有识别结构、required slots 为 `unknown` 或当前分支没有降分，都不能把该 operator 从冻结计划中删除。只有静态运行策略排除、权威事实生成前证明不适用，或该 operator 分支已经进入终态，才不再调度。这样才能保证"首批候选无效后回溯到未进入首批候选的其他算子"真正成立。

### 9.4 O19–O33 专项验证

专项验证至少分成四种 assignment mode：

- `forced`：显式指定 operator，验证算子内容和完整闭环
- `hybrid_shadow`：记录 LLM/规则路由，但仍由强制 assignment 控制执行
- `natural`：由 hybrid route 和 `operator_plan` 决定执行
- `forced_coverage`：按预注册 operator 集合完成全覆盖，边界目标只记录不提前停止

只有 `natural` 模式用于评价自然路由 precision、recall 和混淆矩阵。`forced` 与 `forced_coverage` 的降分只用于评价算子潜力和覆盖，不用于证明路由正确，也不与自然多分支的前 5 个边界指标合并。

### 9.5 下游接线边界

当前编排仍按单样本单主链运行。混合路由接入多分支模式时必须同时处理以下接口，不能只修改 `operator_router.py`：

- `run_loop.sh` 需要按 `search_mode` 分流；single-branch 继续使用旧阶段顺序，multi-operator-branch 由搜索协调层驱动每条分支的完整闭环；
- `candidate_selection.py` 在 single-branch 中继续选一条，在 multi-operator-branch 中不能把同一父节点下的多个合法算子分支压回一条；
- 生成候选进入答案、Rubric 和评分前就必须携带稳定的 `parent_node_id`、`branch_id`、`candidate_group_id` 和 `candidate_id`，不能等到外层汇总时再补；
- `analyze_evolution_effect.py` 必须使用显式父节点和分支 ID 比较，不能只按 `sample_id`/`index` 建立前序索引；
- `update_sample_state.py` 和 Memory 写入需要分支级幂等键，避免兄弟分支覆盖或恢复时重复追加；
- `resume_run_loop.sh` 需要恢复未完成的 operator plan，而不是按旧 round 重新路由已完成分支。

这些接线不改变回答、Rubric、评分或 Memory 的业务判定，只修正多分支模式下的记录身份、调度和幂等语义。

Search Coordinator 必须是 multi-operator-branch 的唯一运行入口。若 `question_evolution.py` 或其他模块存在独立的多分支循环，应改造成 Search Coordinator 的 generation adapter，或从多分支主链移除；不能保留两个分别管理预算、恢复和终态的状态机。

---

## 10. 可观测性与报告

### 10.1 样本级路由产物

每条样本至少保存：

- `routing_mode`；
- `router_model`、`router_concurrency`、`router_temperature` 和 provider limiter 版本；
- `llm_route_status`；
- `llm_route_model`；
- `llm_route_prompt_hash`；
- `llm_route_response_hash`；
- `reasoning_objects`；
- `operator_candidates`；
- `deterministic_route`；
- `memory_route`；
- `merged_route`；
- `rejected_operator_candidates`；
- `route_versions`；
- `operator_plan` 的初始顺序和版本。

路由审计信息不能覆盖原有 `overscore_diagnosis` 或 `sample_profile`。建议放在独立的 `operator_route` 子对象或 `router_metadata` 中。

### 10.2 必须报告的指标

路由报告必须分母完整，至少包括：

- LLM 调用成功率：发生调用的样本中成功返回可解析结构的比例。
- LLM fallback 率：因失败或全被过滤而使用规则兜底的比例。
- 合法 operator ID 率：LLM 返回候选中通过 registry 校验的比例。
- `avoid` 违规率：最终 primary 是否违反规则排除，目标为 0。
- Top-1 适用 precision：primary 落在人工或资格金标集合中的比例。
- Top-k 适用 recall：可接受算子是否出现在候选集合中的比例。
- operator 混淆矩阵：O19–O33 与相邻算子的误选分布。
- O19–O33 召回率：在适用样本中进入 priority 集合或 `executable operator plan` 的比例。
- operator 覆盖率：进入生成、校验、评分的算子分布。
- `route stability`：相同输入和版本重放时结果一致率。
- 路由成本：token、延迟、失败重试和 API 成本。
- 路由并发：active requests、排队时长、provider 限流次数和有效并发上限。
- 分支效果：parent-child 分数变化和 `boundary_candidate` 数量。
- 复核质量：provisional hit 经人工复核后的 precision。

平均分下降不能替代上述路由指标。

### 10.3 对当前实验的回放

第一批回放应使用 experiments/2026-07-23/exp 的既有 profiled、routed 和候选产物：

1. 固定原始输入和版本；
2. 离线重放 deterministic route；
3. 以 shadow 方式记录 LLM route，不改变既有候选；
4. 比较两者对 O19–O33 的 top-k 召回差异；
5. 再在独立样本上进行 forced/forced_coverage/hybrid_shadow/natural四种模式实验。

不能用同一批样本同时调 Prompt、定义金标并报告 natural routing 指标。

---

## 11. 规则约束清单

### 11.1 生成前约束

- operator ID 必须来自 registry；
- operator 必须通过当前模式的资格状态；
- `validation_only` 不生成；
- `data_gated` 的 `required_slots` 为权威缺失时记录生成前 `not_applicable`；仅为 `unknown` 时保留在计划并降低优先级；
- LLM 不得将主题相似当成结构适用，也不能因为未识别结构就排除 operator；
- 只有有证据的 operator 才能成为 `single-branch` `primary`；
- LLM 漏召回不能直接排除未命中的 executable operator；
- fallback 不能无条件选择 O10。

### 11.2 候选生成后约束

- 生成结果仍由原有校验处理；
- `not_applicable` 必须带非空原因；
- API 错误、空响应和解析失败不是 `not_applicable`；
- 重复、校验重试和 API 重试分别计数；
- 题目、答案、Rubric 和 Judge 不得接收路由内部解释字段；
- 任何分数下降只能标记 `boundary_candidate`，不自动确认有效。

### 11.3 运行时预算约束

建议为路由单独定义以下预算：

- 单样本 LLM 路由请求数：默认 1；
- Router 任务并发：固定上限 20，不与其他阶段共享任务 `semaphore`；
- 路由重试数：默认 0 或 1，按 API 错误类型处理；
- 单样本 `dispatch_window` 可调度的最大 executable 数；完整 `executable_pool` 仍必须持久化；
- 单样本候选生成、校验和评分预算；
- 单批次总路由 token 和耗时预算；
- 缓存命中时不重复调用 LLM。

预算触发时必须使用独立原因，例如 `router_budget_exhausted`，不能伪装成 `operator_space_exhausted`。

---

## 12. 版本与缓存

路由结果的可复现键至少包含：

```text
sample_or_parent_id
+ input_content_hash
+ profile_version
+ router_prompt_version
+ router_model
+ router_temperature
+ operator_registry_version
+ qualification_policy_version
+ memory_snapshot_version
+ merge_policy_version
```

以下变化必须使缓存失效：

- 原题、参考答案、候选答案或评分摘要变化；
- `sample_profile` 或 `overscore_diagnosis` 变化；
- operator card、资格状态或启用状态变化；
- 规则合并器变化；
- Memory 快照变化；
- LLM 模型或 Prompt 版本变化。

路由缓存只能复用路由结果，不得跨版本复用题目生成、答案、Rubric 或评分产物。

---

## 13. 实施单元

### U1. 统一 Operator Card 与路由数据契约

Goal: 从 operator registry 的结构化 spec 派生 LLM 路由所需的 reasoning object、目标错误、适用条件、相邻边界和版本信息。

Requirements: R1、R3、R4、R9、R11。

Dependencies: 无。

Files: `prompts/operators/base.py`、`prompts/operators/__init__.py`、`operator_router.py`、`schemas/operator_route.schema.json`、`schemas/operator_runtime_policy.schema.json`、`schemas/pipeline_record.schema.json`、`tests/test_new_operator_content.py`、`tests/test_stage03_operator_routing.py`。

Approach: 保留现有 `OperatorPromptSpec` 作为内容语义来源，不在 `prompts/router_prompt.py` 手工复制 O19–O33 描述。新增独立、版本化的 `OperatorRuntimePolicy` 承载 `qualification_status`、`natural_routing_enabled`、`generation_enabled`、`required_slots` 和 `routing_version`，不把运行资格继续塞入内容 spec 的两个布尔字段。明确区分内容语义、运行资格、数据 gate 和运行时 `not_applicable`。

Test scenarios:

- Happy path: O19–O33 均能生成合法 card，且 card 中的 reasoning object 与 operator spec 一致。
- Edge case: validation_only、未启用和当前模式不允许的资格状态被标记为不可生成，不进入 executable pool；样本适用性为 unknown 的 operator 仍保留。
- Error path: 未知 operator ID、缺失 card 字段或非法资格状态被规则层拒绝并记录原因；required slots 无法判断时记录 unknown 而不是 not_applicable。
- Integration: 由 registry 生成的 card 可被路由 Prompt 使用，且 card 字段不会进入题目生成 Prompt。

Verification: operator registry、运行策略、路由 schema 和测试对 O10–O33 的 ID、状态和语义字段保持一致；不存在第二份手工 operator 清单；样本适用性三态可被持久化和恢复。

### U2. 实现 LLM 结构路由器

Goal: 让 LLM 从原题和评测上下文中识别 reasoning object，并返回带证据的 top-k operator 候选。

Requirements: R1、R2、R3、R6、R9、R13。

Dependencies: U1。

Files: prompts/router_prompt.py、llm_operator_router.py、operator_router.py、local_api_config.py、run_loop.sh、schemas/operator_route.schema.json、tests/test_hybrid_operator_router.py、路由响应 trace 相关测试。

Approach: 复用现有 OpenAI-compatible async client 约定和 JSON 解析模式，使用 GPT 系列路由模型，默认 gpt-5.4，并发上限为 20。Router 使用固定、版本化的 system prompt 约束角色、分析步骤、事实闭包、三态适用性和严格 JSON 输出；每次调用的 user prompt 注入当前样本上下文和由 registry/runtime policy 派生的 operator cards。LLM 输出严格 schema；保留 reasoning object、evidence spans、candidate rank、applicability、confidence 和 adjacent rejection。使用低温度和明确的 schema version。Prompt 中强调只识别结构、不改写题目、不推荐评分规则、不编造事实。路由任务池独立于进化、答案、Rubric 和评分任务池；provider 级限流只在阶段重叠时共享。

Execution note: 先用固定 fixture 写解析和失败路径测试，再接入真实 API；真实路由运行采用 shadow 模式，不改变主链候选。

Test scenarios:

- Happy path: 断点追踪、竞争实体、时间窗和来源融合样本分别召回 O22/O29、O26/O25 和 O33 等候选，并带输入内证据片段。
- Edge case: 一个样本同时包含多个 reasoning object 时返回有序多个候选，而不是强制唯一算子。
- Error path: 空响应、非 JSON、未知 ID、缺失 rank、证据片段不在输入中时返回结构化失败状态。
- Integration: LLM 路由失败时能产生可供规则合并器使用的 deterministic fallback，不阻断非路由阶段；40 个并发 fixture 请求的同时活动路由请求不超过 20。

Verification: LLM 结果满足 schema；未知 ID 和幻觉证据不会进入最终 route；LLM 路由失败不改变样本原有 evolution_action。

### U3. 实现规则合并、过滤和兜底

Goal: 将 LLM route、deterministic route、Memory 和状态建议合并为兼容旧字段的 operator_route。

Requirements: R2、R4、R5、R6、R7。

Dependencies: U1、U2。

Files: operator_router.py、prompts/router_prompt.py、tests/test_stage03_operator_routing.py、tests/test_hybrid_operator_router.py。

Approach: 保留 _base_rule_route() 作为 baseline。新增来源合并和稳定排序，硬排除优先于任何 LLM 结果。LLM 漏召回不排除其他 executable operator；规则 fallback 不允许无条件回退 O10。single-branch 与 multi-operator-branch 对 avoid_operators 使用不同语义：前者当前周期排除，后者由 plan 终态负责永久排除。

Test scenarios:

- Happy path: LLM 与规则共同支持的 O29 排在仅由 fallback 召回的 O10 之前。
- Edge case: LLM 漏掉 O22 时，O22 仍保留在 multi-operator operator_plan；LLM 返回重复 ID 时最终列表去重且顺序稳定。
- Error path: LLM 返回被 avoid、当前模式不允许、validation-only 或权威事实明确不适用的 operator 时被过滤，并保留 rejected reason；LLM 漏召回或样本适用性 unknown 的 operator 不能被过滤出 executable pool。
- Integration: 现有 O10–O18 路由 fixture 的 primary、backup、avoid 行为不因 LLM 失败而改变。

Verification: route 输出可被现有 question_evolution.py 消费；最终 primary 不违反硬约束；同一输入和版本的合并结果稳定。

### U4. 接入 operator_plan 与多分支搜索

Goal: 让最终混合路由结果成为父节点多算子搜索的排序输入，并保证 executable 但未被 LLM 召回的算子仍可覆盖。

Requirements: R2、R4、R7、R8、R10、R11、R12。

Dependencies: U3；docs/后续优化方案/基于父节点的多算子分支搜索.md 的搜索状态实现。

Files: search_coordinator.py、run_loop.sh、resume_run_loop.sh、candidate_selection.py、question_evolution.py、analyze_evolution_effect.py、update_sample_state.py、schemas/search_state.schema.json、schemas/branch_result.schema.json、Memory schema、tests/test_search_coordinator.py、tests/test_run_loop.py 或现有 run-loop 集成测试、tests/test_resume_run_loop.py、tests/test_hybrid_operator_router.py。

Approach: Search Coordinator 是 operator_plan 的唯一所有者。它使用最终 route 的优先级，但只把当前模式允许的 executable operator 追加到计划，不能无条件追加整个 registry。完整 executable pool 与本次 dispatch window 分开保存。静态不可生成、模式不允许和权威事实明确不适用的状态记录为排除项；unknown 保留在计划中；运行时 not_applicable 进入终态。专项全覆盖使用独立 forced_coverage 模式，不修改 natural 模式的 5 边界停止语义。multi-operator-branch 绕过单候选收敛，并在分支进入答案/Rubric/评分前设置稳定 ID；效果分析、状态更新和 Memory 写入均使用该 ID 幂等处理。

Test scenarios:

- Happy path: route 只召回 O16，但 executable plan 仍按优先级追加 O19、O22 和 O29。
- Edge case: 边界目标达到 5 时，未执行 operator 被列入摘要，结果不宣称已完成全算子覆盖。
- Edge case: dispatch window 因预算截断时，完整 executable pool 仍被保存，并以 partial coverage 结束而不是 operator space exhausted。
- Error path: 路由预算耗尽后仍能从已持久化的 baseline route 恢复；搜索预算耗尽不伪装为 operator space exhausted。
- Integration: 每个分支仍与同一父节点比较分数，路由 metadata 不进入候选题、答案、Rubric 或 Judge 输入；当前分支终态后从冻结 plan 选择下一个 pending operator，不重新路由。
- Integration: single-branch 继续输出每个样本一条主链记录；multi-operator-branch 的兄弟分支不会被候选选择、效果分析或 Memory 键相互覆盖。

Verification: operator_plan 覆盖和终态可恢复；分支数量、边界数量、重复重试和 not_applicable 行为符合父节点搜索方案。

### U5. 增加持久化、缓存和可观测性

Goal: 让混合路由结果可重放、可审计、可统计成本，并与已有 JSONL manifest 和 trace 机制兼容。

Requirements: R3、R6、R8、R9。

Dependencies: U2、U3、U4。

Files: pipeline_io.py、operator_router.py、llm_operator_router.py、schemas/operator_route.schema.json、schemas/operator_runtime_policy.schema.json、schemas/pipeline_record.schema.json、tests/test_pipeline_io.py、tests/test_search_coordinator.py。

Approach: 保存 LLM、规则、Memory 和 merge 的版本与 hash；保存失败类别、延迟、token、fallback 原因和候选覆盖。缓存键必须包含输入、Prompt、model、registry、资格、Memory 和合并策略版本。每个父节点冻结 memory_snapshot_id、route revision 和 operator plan；兄弟分支的新 Memory 不重排当前计划。恢复时版本变化建立新记录，不覆盖旧路由。

Test scenarios:

- Happy path: route metadata、operator plan 和 branch state 可写入并通过 schema 校验。
- Edge case: 相同输入命中缓存不增加 LLM 请求计数；Memory 快照变化会使缓存失效。
- Error path: 中途写入失败或响应解析失败时保留失败类别和可恢复状态，不产生伪成功记录。
- Integration: manifest、trace 和断点恢复使用稳定的 sample/parent/branch ID，不重复执行已完成分支。

Verification: 能按 operator、route source、fallback reason、模式和版本生成覆盖/成本报告；历史产物不会被新版本覆盖。

### U6. 建立路由离线回放、shadow 和四模式评估

Goal: 将路由识别质量、算子内容质量和完整评分效果分离评估。

Requirements: R2、R8、R9。

Dependencies: U1–U5。

Files: tests/fixtures/ 下新增路由 fixture、tests/test_hybrid_operator_router.py、docs/后续优化方案/15_扩大实验与受控评测方案.md的实验衔接说明（仅在实施阶段更新）。

Approach: 先对现有实验做 deterministic/LLM shadow 离线回放，再进行 forced、forced_coverage、hybrid_shadow、natural四种 assignment mode。使用独立 holdout 评估适用性 precision/recall、top-k recall、相邻算子混淆和 route stability。区分缓存重放一致率与独立重复调用一致率。forced/forced_coverage 结果不计入 natural routing 指标。

Test scenarios:

- Happy path: 已知结构 fixture 中 O19–O33 进入可接受候选集合，且强制、覆盖、shadow 和自然模式的指标分开统计。
- Edge case: 一个样本有多个可接受 operator 时按集合命中，而不是错误要求唯一 gold operator。
- Error path: LLM 不可用时 shadow 报告 fallback，但不伪造 LLM 成功；路由质量不足时停止自然路由扩大。
- Integration: 同一题在 forced、forced_coverage、hybrid_shadow 和 natural 模式使用相同下游评分契约，模式差异只在 assignment、覆盖和顺序。

Verification: 报告同时给出适用样本分母、召回/精确率、operator 覆盖、分支评分、replay gap、成本和终止原因。

---

## 14. 实验与验收指标

### 14.1 硬约束指标

- 非法 operator ID 率为 0；
- 正式 hybrid 路由使用非允许 GPT 模型的比例为 0；
- Router 同时活动请求数不超过 20；
- avoid 违规 primary 率为 0；
- 未资格化或 validation_only operator 进入正式生成的比例为 0；
- 路由失败被错误标记为 not_applicable 的比例为 0；
- 路由内部字段进入题目、Rubric、Score Prompt 或 Judge 输入的比例为 0；
- 已完成分支重复执行率为 0。

### 14.2 路由质量指标

- executable operator 中被判为 priority 的 top-1 precision；
- 可接受 operator 集合的 top-k recall；
- O19–O33 按 operator 和 reasoning object 的召回率；
- O19–O33 与 O10–O18 的相邻混淆矩阵；
- LLM 与规则 route 的一致率；
- 重放稳定率；
- 单样本平均路由成本和延迟。

### 14.3 算子效果指标

- 通过校验候选数；
- 完整评分候选数；
- parent-child 分数变化；
- boundary_candidate 数量；
- 目标 failure mode 命中率；
- Qwen/GPT replay gap；
- 人工复核后的 provisional precision。

算子效果指标必须按 assignment mode、operator、样本族、route source 和版本分层。不能只报告总平均分。

### 14.4 Pilot 数据与阶段晋级门槛

P0 必须预注册 gold 定义、k、样本分母、重复次数和阈值。一个样本可以有多个可接受 operator，gold 应表示为集合，不强制唯一标签。

建议的最低 pilot 数据结构是：

- 每个待验证 operator 至少 8 条适用正例；
- 每个 operator 至少 8 条相邻 operator 或不适用近邻；
- 至少覆盖 2 个业务表面；
- development、qualification 和 natural routing holdout 相互隔离；
- 每条 holdout 由人工给出 acceptable_operator_ids、rejected_operator_ids 和理由。

建议的 P1 shadow → P2 hybrid-single 晋级门槛是：

- 路由 JSON/schema 成功率应不低于 98%。
- 非法 ID、资格违规和 avoid 违规均应为 0。
- Top-1 macro precision 应不低于 75%。
- Top-3 macro recall 应不低于 85%。
- 单 operator 的 Top-3 recall 不低于 60%。
- 幻觉 evidence span 比例不高于 2%。
- 非 provider 故障导致的 deterministic fallback 率不高于 10%。
- 缓存重放一致率必须为 100%。
- 独立重复调用的 Top-1 一致率不低于 80%。
- 三次独立调用的 Top-3 集合平均 Jaccard 不低于 0.75。

这些阈值是 pilot 工程门禁，不是统计功效结论。扩大前应根据 pilot 方差和样本成本预注册正式样本量和置信区间。

P2 hybrid-single → P3 hybrid-multi 还必须满足：

- single-branch 的现有回归行为在 deterministic fallback 下保持一致；
- route metadata 向下游 Prompt 的泄漏测试为 0；
- parent/branch ID、效果比较和 Memory 幂等集成测试通过；
- 多分支预算、partial coverage 和恢复终止原因可区分；
- Search Coordinator 是唯一多分支状态机。

P3 → P4 受控自然路由还必须满足：

- 每个启用 operator 已通过独立 forced qualification；
- natural holdout 的 operator 混淆率在预注册上限内；
- 人工复核后的 provisional boundary precision 达到预注册门槛；
- 请求、评分和人工复核成本不超过 manifest 中的预算；
- forced/forced_coverage 数据未混入 natural 指标。

---

## 15. 失败处理、回滚与降级

### 15.1 路由级失败

LLM 路由持续失败时，切换到 deterministic-only 模式，并保留失败证据。此时仍可以运行单分支或多分支搜索，但报告必须标注 routing_mode=deterministic_fallback。

### 15.2 Operator 级失败

如果某个 operator 频繁返回非法结构、not_applicable 或内容资格失败，规则层可以将其置为 qualification_only 或 suspended。该状态变更不应由 LLM 路由自动触发，必须由既有资格/人工审核机制产生并带版本。

### 15.3 全局回滚

出现以下情况时停止自然路由扩大：

- route schema 解析大量失败；
- LLM 产生系统性幻觉证据；
- avoid 或资格约束被绕过；
- route metadata 泄漏到生成/评分 Prompt；
- route cache 或 manifest 无法稳定恢复；
- O19–O33 在独立 holdout 上的误选率明显高于 deterministic baseline。

回滚只切换 routing_mode 或 router/merge policy 版本，不覆盖历史路由和评分产物。

---

## 16. 分阶段实施顺序

### 阶段 P0：契约和基线冻结

1. 冻结当前 deterministic route 输出和测试 fixture。
2. 冻结 operator registry、资格状态和 route schema 版本。
3. 确认 O19–O33 的 reasoning object、目标错误和相邻边界描述。
4. 记录当前实验中 O19–O33 的零召回作为基线，不把它解释为算子无效。

### 阶段 P1：LLM shadow route

1. 只增加 LLM route 记录，不改变主链 primary、backup 和候选生成。
2. 对现有实验产物做离线回放。
3. 统计 LLM/规则的一致率、O19–O33 top-k recall 和非法输出率。
4. 检查 route evidence 是否真实出现在输入中。

### 阶段 P2：Hybrid single-branch

1. 规则合并 LLM 和 deterministic route。
2. 只让最终 primary 进入旧 single-branch 主链。
3. 使用独立 holdout 观察错路由是否减少。
4. 保留 deterministic-only 对照。

### 阶段 P3：Hybrid multi-operator-branch

1. 将混合 route 接入 operator_plan。
2. 保证 executable 但未被 LLM 召回的 operator 仍可排队。
3. 对 O19–O33 运行 forced/forced_coverage/hybrid_shadow/natural四种模式实验。
4. 记录分支覆盖、边界数量、未尝试算子和完整评分产物。

### 阶段 P4：受控自然路由

1. 仅启用已通过资格和 holdout 路由指标的 operator。
2. 分层扩大样本和预算，不以增加 round 或候选数弥补基础路由失败。
3. 按预注册指标决定是否扩大 operator 的自然路由范围。
4. 将失败 taxonomy 和路由混淆结果反馈到下一版本 card，不直接修改评分契约。

---

## 17. 风险与依赖

- LLM 路由成本和延迟增加：会降低批处理吞吐；通过单样本最多一次路由请求、缓存、shadow 先行和独立路由预算控制。
- 路由与进化/评分共用任务池：会使某阶段长尾、重试或限流导致其他阶段饥饿；Router 使用独立 20 并发任务池，阶段重叠时只共享 provider 级限流器。
- LLM 被题面表层词带偏：会使 O19–O33 与相邻算子混淆；要求 reasoning object 和输入证据，并使用相邻边界 fixture。
- LLM 漏召回：会遗漏新算子；由 executable pool 和完整 operator_plan 兜底，漏召回不能标记为不适用。
- LLM 过度召回：会增加无关算子调用；通过资格、权威 required slots、适用性三态、模式预算和分支目标控制。
- registry 与 Prompt card 漂移：会使路由理由失真；card 必须由 registry 派生并带版本，不维护手工副本。
- avoid 与多分支语义冲突：可能使算子被错误永久屏蔽或重复执行；区分路由周期排除与 operator_plan 终态。
- 边界目标过早停止：可能造成 O19–O33 覆盖不足；专项验证使用独立 forced_coverage模式，natural 模式报告未尝试算子。
- 资格状态尚未完成：会使自然路由结论不可解释；未资格化 operator 保持 qualification_only，不进入正式 natural holdout。
- 旧 Memory 污染新路由：会使路由顺序和结果漂移；将 Memory snapshot/version 纳入缓存和报告，强制实验使用独立 namespace。
- 路由解释泄漏到题目：可能使题目被提示或答案方向被写入；使用下游 Prompt 输入白名单测试和 trace 检查。
- 把 forced 结果当 natural 结果：会高估路由效果；四种 assignment mode 分离报告，并使用独立 holdout。

---

## 18. 完成定义

本方案实施完成必须同时满足：

1. LLM 路由可以从原题结构输出可解析 reasoning object 和候选 operator 集合。
2. 规则层可以拒绝非法、未启用、未资格化、仅校验和缺少前置事实的 operator。
3. LLM 失败时 deterministic route 和 executable operator plan 仍可运行。
4. LLM 漏召回不能让 executable operator 永久消失。
5. avoid、Memory、recommended_next_methods、预算和重复状态不会被 LLM 绕过。
6. 单分支和多分支模式都能消费同一份最终 route；多分支模式能保留完整 operator plan。
7. route metadata 不进入题目生成、答案、Rubric、Score Prompt 或 Judge 输入。
8. O19–O33 至少有 forced、forced_coverage、hybrid_shadow 和 natural 四类分开的验证记录。
9. 路由质量和算子效果可以按 operator、样本族、模式和版本分别报告。
10. 路由失败、缓存命中、版本变化和断点恢复均有可审计记录。
11. 当前 deterministic router 的回归 fixture 全部保持既有行为，除非对应变更已在版本和报告中明确说明。
12. 没有把任意降分候选自动标记为正式有效能力边界。
13. 正式 hybrid route 使用配置的 GPT 路由模型，Router 任务并发上限稳定为 20。
14. 画像、路由、题目进化、答案、Rubric 和 Judge 的 LLM 角色及写入字段相互隔离；引入 Router 没有取消或合并后续阶段的 LLM 工作。
15. 当前阶段串行编排下各阶段使用独立任务池；未来阶段重叠时 provider 全局限流可约束总配额且不会改变各阶段任务并发定义。

---

## 19. 明确不在本方案内的后续工作

- 用学习排序模型替代 LLM 或规则合并器；
- 根据在线评分自动更新 operator 资格；
- 让路由器直接修改 operator Prompt 内容；
- 从评分结果反向重写样本画像；
- 让子节点递归成为下一层父节点；
- 自动判断题目是否"更容易"或是否存在答案提示；
- 使用语义相似度替代当前可解释的精确重复规则；
- 变更 Judge、Rubric 或发布决策。

这些工作需要单独的方案、指标和对照实验，不能作为混合路由的隐含扩展。
