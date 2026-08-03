# LLM 动态路由方案

- 文档版本：v3.2
- 制定日期：2026-07-27
- 更新日期：2026-07-28
- 适用范围：Question Evolution 的样本路由

## 1. 文档目的

本文面向实现和维护路由流程的工程人员。读完后，应能理解 Router、规则回退和多分支迭代的完整设计，并据此实现或维护相应行为。

本方案的最终目标不是让 LLM 只写一条建议，而是让它返回的合法候选在最小规则校验后，成为后续真实分支的依据：

```text
LLM Router 成功
  -> 最小规则校验
  -> selected_operator_ids
  -> operator_plan
  -> 生成、校验、回答、评分迭代

LLM Router 失败或无合法候选
  -> 确定性规则路由生成回退候选
  -> 相同的最小规则校验
  -> selected_operator_ids
  -> 真实迭代
```

规则路由只在 LLM 调用失败或没有合法候选时充当回退。LLM 成功时，不把规则、Memory 或历史推荐中未被 LLM 召回的算子追加到本次候选列表。

## 2. 背景与边界

### 2.1 原有问题

旧路由主要依赖诊断文本中的固定关键词。当诊断语言使用旧 taxonomy，而题目实际需要新的结构推理时，相关算子不会被召回。典型情况包括实体同一性、事件阶段、物品来源、路径时间窗、竞争解释、阈值和结论层级等结构，不能只靠某个关键词判断。

因此，Router 的职责是识别题目中的推理结构并从合法算子中召回候选；它不改写题目、不生成答案、Rubric 或评分，也不替代已有的样本画像和下游生成阶段。

### 2.2 与多算子分支搜索的关系

Router 只负责产生并冻结候选列表。Search Coordinator 是 `operator_plan` 的唯一所有者：它用同一父节点为每个候选创建独立分支，记录分支状态，并负责恢复时继续未完成的分支。

不能把"所有可执行算子"直接当作本次分支集合。只有进入本次 `selected_operator_ids` 的算子才建立分支；未被选中的合法算子只可作为审计信息，不视为已经尝试。

### 2.3 本方案不改变的内容

- 样本画像、题目生成、答案生成、Rubric、Judge 和 Memory 仍按各自职责运行。
- Router 的证据与排序说明不写入下游业务提示词，不影响题目事实、参考答案或评分。
- 本方案不引入根据人工审核、置信度、历史分数或实验统计自动淘汰本次候选的机制。

### 2.4 与方案 19 的继承和新增边界

方案 19 已经定义了父节点横向分支搜索的基础能力：候选列表冻结、稳定 `branch_id`、直接父子评分比较、分支状态、断点恢复、正式产物发布和 `branch_window` 调度。方案 20 不重写这些能力，而是在其上替换"候选列表从哪里来"和"何时停止领取候选"。

暂时无法在飞书文档外展示此内容

因此，方案 20 的实施前提是先保留方案 19 的"只为冻结候选建分支"和"父节点唯一状态所有者"不变量。任何把全部注册算子补回 `operator_plan` 的实现都不符合两份方案的组合语义。

## 3. Router 设计

### 3.1 Provider 配置复用

Router 直接复用 GPT provider 的配置，不要求单独配置 Router provider。

API key 按以下顺序读取：

1. `ROUTER_API_KEYS`
2. `GPT_API_KEYS`
3. `HIAPI_KEYS_BIG`
4. `OPENAI_API_KEYS`
5. `OPENAI_API_KEY`

base URL 按以下顺序读取：

1. `ROUTER_BASE_URL`
2. `GPT_BASE_URL`
3. `OPENAI_BASE_URL`
4. 配置文件中的 `ROUTER_BASE_URL`、`GPT_BASE_URL`、`BASE_URL`、`OPENAI_BASE_URL`

这样 Router 与后续 GPT 调用使用同一套地址和凭据。Router 使用 `ROUTER_MODEL`；未显式设置时使用 GPT 模型配置。

### 3.2 明确且共享的返回格式

Router 的输出格式版本固定为 `hybrid-router-v1`。提示词和响应解析器共用同一份字段定义、示例和长度限制，避免提示词改变后解析器仍按旧规则读取。

顶层 JSON 必须且只能包含以下字段：

```json
{
  "routing_schema_version": "hybrid-router-v1",
  "reasoning_objects": [],
  "operator_candidates": [],
  "not_selected_reasons": [],
  "router_comment": ""
}
```

每个候选必须包含 `operator_id`、`rank`、`applicability`、`confidence`、`reasoning_object`、`evidence_spans`、`why_fit` 和 `why_not_adjacent`。其中 `why_not_adjacent` 必须是只含一个相邻算子 ID 的对象，不能是一段普通文字：

```json
{
  "operator_id": "O29_entity_identity_conflict_resolution",
  "rank": 1,
  "applicability": "applicable",
  "confidence": 0.8,
  "reasoning_object": "冲突绑定下的实体连续性与同一性",
  "evidence_spans": ["从输入逐字复制的短证据"],
  "why_fit": "说明该算子为何匹配当前结构",
  "why_not_adjacent": {
    "O19_multi_entity_role_binding": "说明最接近算子为何不是优先选择"
  }
}
```

`router_contract` 是 Prompt、解析器、测试和 cache 序列化的唯一格式来源。它必须定义以下不可放宽的规则：

- 顶层对象、`reasoning_objects` 项和候选项都只能包含契约规定的字段，额外字段和缺失字段都属于 schema error；
- `reasoning_object` 必须且只能包含 `name`、`evidence_spans`、`confidence`；
- `rank` 为正整数；`applicability` 只能是 `applicable`、`unknown` 或 `not_applicable`；`confidence` 为 0 至 1 的数值；
- 候选和 `reasoning_object` 的 `evidence_spans` 为 1 至 2 条，且每条须逐字出自本次 Router 输入，不能从算子卡片中取证；
- `why_not_adjacent` 必须且只能有一个键。该键必须是注册表中的真实算子，并来自当前算子的 `adjacent_boundaries`；相邻关系由算子 spec 中的 `Oxx` 标记解析，不能由模型自由声明；
- 相同 `operator_id` 的重复候选只保留第一个，后续项记录为拒绝原因；候选按 `(rank, operator_id)` 稳定排序；
- 所有字符长度上限、`not_selected_reasons` 的数量上限和空字符串规则均由同一契约常量提供，Prompt 不得自行复制另一套数字。

解析器按以下层次处理错误：

暂时无法在飞书文档外展示此内容

这样"一条候选错误、其余候选合法"不会丢弃合法路由。只有整体不可解析或合法候选数为零时才转入确定性回退。

### 3.3 输入压缩，但不缩小候选空间

Router 输入使用紧凑 JSON。目标是删除重复上下文，不删除模型判断结构所需的原文，也不减少可选择算子的数量。

保留的样本信息：

- 样本 ID、`score_rate`、`evolution_action`；
- 原题完整原文；
- 候选回答完整原文；
- 必要参考材料；
- 标准化 `sample_profile`；
- `overscore_diagnosis` 的核心字段；
- 当前模式、`avoid` 列表、推荐算子 ID 和 Memory 命中的算子 ID；
- 当前所有可路由算子的紧凑卡片。

不传递完整评分明细、完整运行状态、原始调用日志或完整 Memory 条目。参考材料只提取文本字段，进行空白规范化和精确去重；不以"截取前 N 个字符"的方式截断原题、候选回答或诊断原文，避免删除关键证据。

每张算子卡片只包含以下四项：

```json
{
  "operator_id": "Oxx",
  "reasoning_object": "需要识别的推理结构",
  "when_to_use": "简短适用条件",
  "adjacent_difference": "与最接近算子的区别"
}
```

由程序判定的启用状态、运行资格、版本信息等不会重复发送给模型。压缩只减少重复说明，绝不通过卡片压缩减少候选空间。

Router 不能直接使用"全部注册算子"或"24 个算子"这种描述性集合，必须先生成唯一且有序的 `eligible_operator_ids`。其生成顺序为注册表顺序；同一列表同时用于 cards、响应 ID 校验、缓存输入、`executable_operator_ids` 和 `omitted` 审计。`live` 中只有以下算子可进入该列表：

1. 已注册；
2. `generation_enabled=true`；
3. `validation_only=false`；
4. `qualification_status` 不是 `suspended`；
5. 不在当前 `avoid_operators`；
6. 不在当前父节点的 `completed`、`duplicate_exhausted`、`not_applicable` 等终态；
7. 没有被权威且完整的事实账本证明前置条件不成立。

`qualification_only` 在 `live` 中允许进入列表。画像缺字段、Memory 未命中或事实不完整只产生 `unknown`，不能从 `eligible_operator_ids` 中排除算子。若列表为空，不调用 Router，直接写入空 route 和明确的 `excluded` 原因。

### 3.4 输出保持简短，不限制候选数量

Router 不存在业务上的候选数量上限。它返回几个合法候选，解析和合并流程就接收几个；唯一自然上限是可路由的注册算子数。

为降低响应长度和超时风险，单个候选的说明遵守以下限制，而不是限制候选数量：

- 每个候选的 `evidence_spans` 为 1 至 2 条，每条最多 240 个字符；
- `reasoning_object` 最长 120 个字符；
- `why_fit`、相邻算子原因和 `router_comment` 最长 180 个字符；
- `not_selected_reasons` 默认是空数组，确有必要时最多一条；
- 多个候选可以复用同一个 `reasoning_object`。

程序不得因为固定 `top-k`、`num_candidates`、置信度、评分阈值、历史表现或实验指标截断 LLM 已返回的合法候选。

### 3.5 超时、重试与并发

Router 默认超时为 60 秒。一次"路由任务"与一次"HTTP 请求尝试"分别记录，避免将内部重试误认为一条任务。

每个样本固定为一条逻辑任务、一次 HTTP 尝试：超时、限流或网络错误后立即记录失败并交给确定性路由，不等待第二个 60 秒。

Router 使用独立的并发池，默认 `ROUTER_CONCURRENCY=20`。这不是超时的原因，也不等于后续题目生成或评分的并发。路由请求比下游单算子请求更重：它需要比较全部卡片并返回严格 JSON；输入压缩和短输出用于减少这一负担。若 provider 在 60 秒内没有返回，应快速回退，而不是反复等待。

### 3.6 缓存、版本和恢复

Router 缓存键必须包含：

- 紧凑输入内容
- Prompt 版本
- schema 版本
- 模型与温度
- provider 地址标识
- timeout 与重试设置
- 传输策略版本
- operator registry 与运行策略版本
- Memory 快照

只有 `succeeded` 结果可以写入 Router 缓存。`timeout`、限流、网络错误、空响应、JSON 错误、格式错误和伪造证据错误都保留审计记录，但不缓存，避免修复生效后继续读取旧失败结果。

同一 cache key 的并发请求必须合并为一次真实 HTTP 调用。成功结果写入前移除 raw response；cache 采用追加 JSONL、文件锁、flush 和 fsync，避免并发运行或进程中断留下半条记录。raw response 另写 trace，用于审计，不作为业务输入。

恢复和兼容规则如下：

1. 版本一致的成功路由可以复用；已发布的 `operator_plan` 是唯一恢复来源，不能因新 Memory、新排序或当前 registry 静默重建。
2. 已终态分支不重复执行；已领取但尚未记录首次模型请求的分支恢复为 `pending`；已记录请求的 `running` 分支按请求 checkpoint 恢复，避免重复真实调用。
3. `hybrid_shadow`、`natural` 或缺少 `assignment_mode/route` revision 的历史产物保持原有语义，不能自动升级成 `live`。需要 `live` 时必须创建新的 route revision 和新的实验目录。
4. Prompt、schema、provider、timeout、重试、运行策略或 Memory 快照变化后，旧 Router cache 不匹配；不得跨版本复用。
5. `routed`、`search_state`、`branch_results` 和 manifest 的版本必须一致。发现 schema、route revision、输入哈希或正式产物完整性不一致时，拒绝在原目录续跑。

## 4. 实际执行语义

### 4.1 唯一运行配置

正常配置为：

```text
ROUTING_MODE=hybrid
ASSIGNMENT_MODE=live
ROUTER_MODEL=GPT_MODEL
ROUTER_CONCURRENCY=20
ROUTER_TIMEOUT=60
ROUTER_RETRIES=0
SEARCH_MODE=multi_operator_branch
SEARCH_BRANCH_WINDOW=1
```

这是本方案唯一的正常运行方式。它不读取离线准入报告，不以人工审核、命中率、错误率、耗时、token、置信度或历史分数作为本次路由是否执行的门槛。

### 4.2 最小规则校验

规则校验只保证候选可执行和响应可解析，不重新判断哪个算子"更好"。除以下事项外，`live` 不得增加候选拒绝规则：

1. Router 输出符合共享 JSON 契约。
2. `operator_id` 已注册。
3. 算子允许生成，且不是 `validation_only` 或 `suspended`。
4. 算子不在当前 `avoid_operators` 中。
5. 该算子没有在当前父节点达到 `completed`、`duplicate_exhausted`、`not_applicable` 等终态。
6. 权威且完整的事实账本明确证明前置条件不成立。

样本画像缺字段、Memory 未命中、置信度较低或历史效果一般，都不能作为拒绝 LLM 候选的理由。只有有完整反证时，才可将候选标记为当前父节点的 `not_applicable`。

### 4.3 成功与回退路径

```text
LLM 返回 N 个候选
  -> 对每个候选做最小规则校验
  -> 保留全部合法候选，按 Router rank 排序
  -> 冻结 selected_operator_ids
  -> 仅据此创建 operator_plan

LLM timeout / rate_limited / network_error / empty_response
invalid_json / schema_error / hallucinated_evidence
或全部候选被拒绝
  -> 确定性路由产生回退候选
  -> 相同最小规则校验
  -> 冻结并真实执行回退候选
```

成功路径不追加规则路由、Memory 或历史推荐中未被 LLM 召回的候选。回退路径不是跳过样本，也不是只留下审计记录。

### 4.4 分支开辟与完成条件

`selected_operator_ids=[O20,O27,O29]` 时，必须生成三个分支计划项。`branch_window=1` 只表示一次执行一个分支：O20 到达终态后继续领取 O27，随后领取 O29；它绝不表示只执行列表第一个候选。

`live` 运行必须持续领取 `pending` 分支，直到全部冻结候选到达明确终态：

- 某个分支生成、校验、回答或评分失败，只结束该分支，继续下一个候选；
- `boundary_target` 只记录结果，不能提前停止 `live` 的候选遍历；
- 全部候选完成后，以 `candidate_list_exhausted` 结束；
- `operator_space_exhausted` 不用于本流程；本流程以 `candidate_list_exhausted` 作为候选遍历完成的标识。

## 5. 实验产物与指标

每个路由产物、manifest 或报告必须记录：

- Router 状态、错误分类、逻辑任务数和 HTTP 尝试数；
- 模型、provider 地址标识、Prompt/schema 版本、timeout、重试数；
- 输入/输出 token、耗时和缓存命中；
- LLM 原始候选数、最小校验通过数、拒绝原因和是否发生回退；
- `selected_operator_ids`、`operator_plan`、每条分支是否被领取、终态、耗时和父子分数变化；
- 未执行候选及其明确原因。

命中率、错误率、耗时、token、稳定性、人工标注和算子效果都由实际样本运行产出，用于分析 Router 是否值得优化；它们不参与本次样本的候选选择，也不阻止分支开辟。

## 6. 从方案 19 到方案 20 的实现映射

本节是从初始项目重建同等功能的模块边界。模块名表示职责，不允许把其中一项逻辑复制到多个阶段。

暂时无法在飞书文档外展示此内容

### 6.1 新增和变更的数据契约

必须新增或扩展以下契约，并在读取正式 JSONL 时校验：

暂时无法在飞书文档外展示此内容

`operator_route.selected_operator_ids` 是 Router 和搜索协调器之间唯一的业务接口。`primary_operator` 和 `backup_operators` 仅为兼容与审计投影，不能在 `live` 下覆盖或截断该列表。

### 6.2 实现顺序

R1. `live` 运行策略

- 在运行策略、命令行和运行脚本中加入 `ASSIGNMENT_MODE=live`。
- `live` 允许全部可生成、非仅校验、非暂停的算子进入 Router 候选空间。
- `hybrid` + `live` 不读取离线准入报告，也不要求人工批准。

R2. 耗尽冻结候选

- Search Coordinator 对 `live` 使用"直到候选列表耗尽"的调度语义。
- 禁止 `boundary_target` 提前停止 `pending` 分支。
- 保持 `branch_window` 只控制并发窗口。
- 所有候选终态后写入 `candidate_list_exhausted`。

R3. 最小校验与明确回退

- LLM 成功候选只经过第 4.2 节的校验。
- 移除离线指标、人工准入、候选数量和历史表现对 `live` 执行的影响。
- LLM 成功时不合并额外规则候选；只有失败或全部非法时才使用确定性回退。

R4. 指标保持旁路

- 保留 Router 评估与实验报告。
- 离线报告只用于分析，不能阻断 `live`。
- 保证指标变化不会改写已冻结的候选列表或停止分支。

R5. 真实样本运行

- 使用独立实验目录运行实际样本，不与此前产物混用。
- 每个样本必须保留第 5 节所列路由、候选、分支和耗时记录。
- 根据实际产物检查：合法候选是否进入计划、每个计划分支是否被领取、回退是否真实执行，以及分支的最终状态。

## 7. 真实运行、验收标准与非目标

### 7.1 真实运行前置条件和命令

真实运行前必须满足以下条件：

1. 输入为已准入种子 JSONL，并通过项目的运行环境检查；检查结果必须显示输入、Python 依赖、Bash 和所有模型 provider 均 ready。
2. 输入记录具备题面、必要参考材料、Rubric/评分依据及稳定 ID；不得用临时夹具或不完整历史产物替代正式输入。
3. provider 凭据只通过环境变量或本地未跟踪配置提供；不得写入运行脚本、产物摘要或版本库。
4. 启动新实验必须指定新的 `EXP_ROOT` 或由入口自动创建新目录；不得复用 shadow、旧 schema 或不同 route revision 的目录。

唯一正常运行配置如下，其中 `INPUT_FILE` 指向通过前置检查的正式输入：

```text
python check_runtime_environment.py --input-file "$INPUT_FILE"

INPUT_FILE="$INPUT_FILE" \
EXP_ROOT="experiments/live" \
ROUTING_MODE=hybrid \
ASSIGNMENT_MODE=live \
ROUTER_MODEL="${GPT_MODEL}" \
ROUTER_CONCURRENCY=20 \
ROUTER_TIMEOUT=60 \
ROUTER_RETRIES=0 \
SEARCH_MODE=multi_operator_branch \
SEARCH_BRANCH_WINDOW=1 \
bash run_loop.sh
```

中断恢复只允许使用项目提供的恢复入口和原实验目录。恢复前必须验证正式产物与 manifest；不得删除 partial 文件、手工拼接 JSONL，或把旧 route/state 复制到 `live` 目录中继续运行。

### 7.2 真实产物检查

实际运行后，对每个进入路由的样本依次检查：

1. `operator_route` 是否包含 route/schema/policy/provider/timeout/retry 元数据；
2. Router 成功时，`selected_operator_ids` 是否等于通过最小校验的 LLM 候选，顺序是否等于 Router rank；
3. Router 失败时，是否存在 `deterministic_fallback` 和真实回退候选；
4. `operator_plan` 是否逐项对应 `selected_operator_ids`，没有额外注册算子；
5. 每个 plan entry 是否被领取并有明确终态；只有所有冻结候选终态后才出现 `candidate_list_exhausted`；
6. cache、trace、`branch_results`、state 和 manifest 的版本是否一致；
7. 指标报告是否只读取产物，而未改变 route、plan 或 branch 状态。

以下行为成立才可进行独立真实样本实验：

- `hybrid` + `live` 的 Router 成功结果不再只是审计字段；
- Router 返回的全部合法候选都进入分支计划，且不存在隐式 `top-k` 截断；
- 每个冻结候选都会被实际尝试，除非其自身进入明确终态；
- Router 失败时，规则回退候选仍真实执行；
- 指标只记录，不作为运行门槛；
- 60 秒 timeout 和零重试保持不变；
- 实验写入独立目录，不与此前产物混用。

### 7.3 功能重建验收

若项目回到方案 19 完成后的初始状态，按本方案重建后必须同时满足以下功能等价条件：

- 方案 19 的冻结候选、直接父子比较、幂等恢复和正式产物发布仍可运行；
- 新增 Router 能从同一 `eligible_operator_ids` 构造卡片、校验响应并写出 route；
- `hybrid` + `live` 的成功和回退路径都能进入方案 19 的多分支流水线；
- `live` 不增加候选、不重排 LLM rank、不以指标阻止分支，也不因 `boundary_target` 留下未尝试的冻结候选；
- 旧产物不会被静默提升为 `live`，新的 `live` 运行使用独立 route revision 和实验目录。

不在本次范围内：根据人工审核或指标自动调整算子资格；用 Router 置信度决定是否执行；用 Router 替代题目生成、答案、Rubric、评分或 Memory 的职责；为了缩短运行时间而截断已返回的合法候选。
