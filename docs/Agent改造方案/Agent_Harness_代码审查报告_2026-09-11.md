# Question Evolution Agent Harness 代码审查报告

| 项 | 内容 |
| --- | --- |
| 审查日期 | 2026-09-11 |
| 审查对象 | `question_evolution_agent.py`、`agent_runtime/**`（含 `budgeting/`、`multi_agent/`、`skills/`）、`schemas/agent_*.json`、`agent_skills/**` |
| 审查性质 | 只读代码审查。**未修改任何项目代码或配置文件。** |
| 对照基线 | `docs/优化任务/项目Agent改造方案.md`（Harness 设计方案，2026-08-04）、`docs/项目技术报告.md` §6.9/§7、`schemas/README.md`、`docs/Agent改造方案/后续潜在的优化点.md` |
| 验证方式 | 全量静态阅读 + `pytest` 实测 + 5 组只读探针（临时目录内构造内存库与假 Registry，不触碰项目文件） |
| 实测结果 | harness 相关 14 个测试文件 **59 passed**（`test_agent_*`、`test_context_*`、`test_global_memory/judge`、`test_budget_*`、`test_multi_agent_*`） |

---

## 0. 结论摘要

Harness 的**代码完成度很高**：Session 状态机、分层 Context Pack、Plan/Policy 双校验、幂等账本、Budget Ledger、只读 Advisor、Skill Registry、Global Judge 治理链路均已成型，且单元测试全绿。

但审查发现 **Harness 目前处于"结构完备、语义未闭合、且尚未上线"的状态**，存在 4 类系统性问题：

1. **安全语义被实现细节反向削弱**——设计中最关键的"快照冻结"和"系统失败/业务失败分离"两条原则，在代码里只被部分实现，且可被静默绕过（见 M-1、R-1）。
2. **契约层退化为文档层**——20+ 个 schema 中仅有 2 处在运行时被真正校验，且 `agent_decision.schema.json` 与实现已发生枚举漂移（见 V-1、V-2）。
3. **控制面与领域面未合流**——Harness 未被 `run_loop.sh` / `run_loop.ps1` 引用，`agent_runs/`、`memory_global/` 在本机不存在，缺少任何真实 Session 现场（见 X-1、X-2）。
4. **若干部件是"仪式性"的**——Skills 内容永不进入上下文、Advisor 默认走确定性模板、Global Judge 无挂载点、预算在 Harness 层近乎名义约束（见 V-5、V-8、V-6、R-2）。

因此本报告的核心判断是：**当前瓶颈不是功能缺失，而是"设计承诺—实现—运行证据"三者之间的闭合度**。以下按用户指定的六个子系统逐一拆解。

---

## 1. 总体架构与组件地图

```
question_evolution_agent.py  (CLI: run / resume / dry-run / review)
  ├─ _memory_runtime()         冻结 Memory Snapshot          → agent_runtime/global_memory.py
  ├─ build_context_pack()      分层上下文 v2 + 缓存身份        → context.py / context_layers.py / context_cache.py / context_prompt.py
  ├─ load_stage_skills()       规程加载（planning_strategy）   → skills/skill_loader.py
  ├─ build_plan()              确定性/模型辅助规划             → planner.py
  ├─ validate_plan()           Policy + 执行骨架校验           → policy.py
  ├─ write_plan_revision()     不可变计划版本 + 事件            → state.py
  ├─ Executor.execute()        步骤执行 / 幂等 / 预算 / 产物校验 → executor.py  ←→  tools.py (ToolRegistry, 5 个复合工具)
  │     └─ observe_experiment() 只读产物聚合与观测归一化         → observer.py
  ├─ assess_budget_reallocation()  提案 → 校验 → 决策（不自动应用）→ budgeting/*
  ├─ run_post_experiment_review()  19 个只读 Advisor + 合并      → multi_agent/*
  ├─ decide_next_action()      确定性停止/阻塞/重规划决策         → decisions.py
  └─ write_agent_report()      审计报告                        → reporter.py

离线治理（当前未挂载）：global_judge.py（Evidence Pack → 诊断 → 提案 → Shadow → 发布门禁）
```

**关键结构事实**：Harness 通过 subprocess 调用 `run_loop.sh`，**不重写领域逻辑**（符合设计 §3.2）。控制语义集中在 `task.py / policy.py / decisions.py / executor.py` 四个文件；`global_memory.py`（38KB）与 `operator_router.py`、`question_evolution.py` 同级的体量说明记忆与路由都是重资产模块。

---

## 2. 上下文管理

### 2.1 当前实现机制

- **五层上下文契约**（`context_layers.py:90-189`）：
  - `snapshot_prefix`（版本与快照身份）、`stable_prefix`（角色/硬约束/工具注册表/schema 索引）、`task_context`（目标/模式/预算/白名单）、`memory_context`（Top-K 卡片）、`dynamic_tail`（运行路径、观测、上次决策、stdout/stderr、计划）。
- **缓存身份**（`context_cache.py:29-80`）：`context_cache_key` 只哈希「版本号 + 快照 id + 模式」，刻意排除 run 目录、时间戳、观测；`memory_context_key` 哈希「快照 id + 归一化 query + 检索版本 + top_k」。
- **提示装配**（`context_prompt.py:10-35`）：按 `stable_prefix → snapshot_prefix → task_context → memory_context → dynamic_tail` 固定顺序拼接，动态内容置于缓存前缀之后。
- **脱敏与限界**：`_truncate`（`context.py:12-16`）、`_bounded`（`context_layers.py:210-215`）分别以 500~18000 字符上限裁剪；`redact_context` 保留 `sha256:` 形式的缓存键。
- **压缩策略**（`context.py:63-74`）：超 `max_chars=60000` 时**只压缩 legacy 别名**（`selected_plan`/`observation_summary`/`memory_summary`/`previous_decision` → 500 字符），不动 v2 层。

### 2.2 局限与风险

| ID | 级别 | 问题 | 证据 |
| --- | --- | --- | --- |
| C-1 | P1 | **截断产物不是合法 JSON**。`_bounded` 超限时返回 `{"truncated": true, "preview": "<原始 JSON 前 N 字符>"}`，`preview` 是被硬切的字符串前缀，下游无法解析，只能当文本看。 | `context_layers.py:210-215`、`context.py:12-16` |
| C-2 | P1 | **缓存层是"审计元数据"而非真实缓存**。代码中不存在任何 prompt cache 读写；且 `context_cache_key` 在首次调用（`plan=None`，`selected_search_mode` 回退到 `task.search_mode`）与持久化调用（用 `plan` 的实际模式）之间**必然不同**，同一 Session 内两次构建的 key 不一致。 | `question_evolution_agent.py:125-126,150-152`、`context.py:36-44` |
| C-3 | P2 | **层间与 legacy 双份维护**。`dynamic_tail.selected_plan` 与顶层 `selected_plan`、`dynamic_tail.observation_summary` 与顶层 `observation_summary` 重复；压缩分支只压 legacy，导致同一字段在两处内容不同。 | `context.py:55-61`、`context_layers.py:160,172` |
| C-4 | P2 | **脱敏规则过宽**。`redact` 的键正则以子串匹配 `key`（`events.py:12`），任何含 `key` 的字段名都会被替换为 `[REDACTED]`（如 `key_findings`、`monkey_*`）；`_SENSITIVE_VALUE` 又会把任意 `https?://` 抹掉，artifact URL 形式的引用被清除。 | `events.py:12-14,21-31` |
| C-5 | P1 | **缺结构化 world_state**。`dynamic_tail` 只有路径与摘要，没有"当前父题、候选树、各分支状态、算子收益、剩余预算、已冻结快照、可回滚点"（设计 §8.1）。这与《后续潜在的优化点》第 4 条一致，说明是已知但未闭环项。 | `context_layers.py:153-173` |
| C-6 | P2 | **无 token 预算**，仅按字符数裁剪；对中文上下文，字符数→token 数偏差显著，60k 字符上限与实际模型窗口无对应关系。 | `context.py:28` |
| C-7 | P2 | `dynamic_tail` 携带 `stdout_summary/stderr_summary/parse_errors` 原始运行态（`context_layers.py:163-165`），与 `PROJECT_HARD_CONSTRAINTS` 中"不得向控制层注入日志"的自述约束存在张力。 | `context_layers.py:32-37,163-165` |

### 2.3 优化方向

1. **结构化截断**：改为「保留字段 + 溢出指针」模式（`{"__overflow__": {"path": ..., "sha256": ...}}`），确保任何被裁剪节点仍是合法 JSON 并可追溯。
2. **缓存身份自洽**：把 `context_cache_key` 的输入固化为「显式选定的模式」，避免 `plan=None` 与持久化调用的 key 分歧；若确有复用诉求，再补一层真正的内容寻址缓存（当前无消费方，建议先删掉误解性命名或明确标注为审计元数据）。
3. **消除双份字段**：legacy 别名改为 `@property` 派生视图或仅在导出时注入，避免压缩策略不一致造成的语义分叉。
4. **收窄脱敏**：键匹配改为精确/前缀白名单（`api_key`、`authorization`、`token`、`secret`、`base_url`），URL 仅在命中凭据形态时脱敏。
5. **补 world_state 层**（与 O-2、R-3 联合设计）：显式 `world_state` 由 `state.py` 单一来源派生，供 Planner/Reflector 消费。
6. **上下文落 schema 校验**：`context_pack_v2.schema.json` 已定义，但从未在运行时调用（见 V-1），建议在 `write_context` 前强制校验。

---

## 3. 记忆系统

### 3.1 当前实现机制

- **权威存储**：SQLite（`memory_global/global_memory_state.sqlite`），JSON/Markdown 为只读投影（`global_memory.py:1-7`）。表结构含 `candidate_facts / admission_log / watermarks / cards / card_events / jobs`（`global_memory.py:151-186`）。
- **两阶段编译**：
  - Phase 1 `extract()`（`318-372`）：逐源增量抽取 + 准入（`_admit`，`294-316`），watermark 仅在成功后推进，含"行数回退"与"前缀改写"两类防篡改检查。
  - Phase 2 `integrate()`（`391-472`）：按 `(fact_type, scene_family, question_form, reasoning_mechanism, operator)` 五元组归并，生成/升级/降级/退役卡片，写 `card_events`。
- **准入红线**：`_admit` 拒绝含 `prompt/reference_answer/scoring_result/rubric/score_prompt` 的候选（`300-302`），并做内容哈希去重。
- **快照与检索**：`create_snapshot()`（`516-529`）冻结 `card_versions + local_memory_hashes + global_index_hash`；`retrieve()`（`539-555`）按 snapshot 允许的 card_id 过滤后做 Top-K。
- **并发保护**：`jobs` 表实现租约（`acquire_lease/finish_lease`，`194-220`），Phase 1 按实验、Phase 2 全局单例。
- **失败隔离**：`SnapshotUnavailable` 在非 resume 时上抛为 `blocked`；resume 时降级为 `no_global_memory`。

### 3.2 局限与风险

| ID | 级别 | 问题 | 证据 |
| --- | --- | --- | --- |
| **M-1** | **P0** | **快照冻结只锁 card_id，不锁版本与内容**。`retrieve()` 取 `permitted = set(snapshot["card_versions"].keys())`，随后从**数据库当前状态**读取卡片正文，并未比对 `card["version"]` 是否等于快照记录值（`543-552`）。<br>**实测**：先创建快照（记录 `GMEM-000001: v1`），再让 `integrate()` 因新增证据把该卡升级为 `v2/needs_human_review`；用**旧 snapshot_id** 检索，返回的仍是 **v2 内容与 v2 版本号**。设计 §3.5/§7.3"运行中 Snapshot 不得变化"在内容维度上未成立。 | `global_memory.py:543-552` |
| M-2 | P1 | **检索是朴素子串计数**。`score = sum(token in searchable for token in tokens)`，只覆盖 `scene_family/question_form/reasoning_mechanism/overscore_pattern/card_type` 五字段的包含关系。设计 §13.6 规定的排序优先级（推理机制 > 题型结构 > 排除条件 > 证据强度 > 虚高模式 > 场景 > 版本新鲜度 > 风险/冲突惩罚）与 `claim_level`、`risk_labels`、冲突惩罚**均未实现**；`exclusions` 只作为输出字段透传，不参与过滤。 | `global_memory.py:544-553` |
| M-3 | P1 | **卡片统计指标是常量占位**。`evidence_summary.effective_rate` 与 `invalid_generation_rate` 恒为 `0.0`（`436`），`score_increased_rate` 依赖对 conclusion/payload 的字符串包含判断（`412`）。卡片对外自称的"有效率/负收益率"不可信，而设计 §13.3 明确要求这些是策略卡必备字段。 | `global_memory.py:412,426,436` |
| M-4 | P1 | **`source_rewritten` 无解封路径，审计日志无界增长**。一旦来源被判定"前缀改写"，`continue` 跳过 watermark 更新（`340-343`），因此**每次** extract 都会重新判定并追加一条 `needs_human_review`。<br>**实测**：同一来源连续 5 次 extract 产生 5 条同因记录。`admission_log` 无去重、无归档、无人工确认入口。<br>附带影响：`agent_observation.json` 被映射为 `system_diagnosis` 来源（`36`），而该文件在每次 Agent 运行时都被覆写——即它天然是"rewritten"来源，每次运行都会污染审计日志。 | `global_memory.py:336-343,31-38` |
| M-5 | P1 | **全量重写的 O(N) 放大**。`publish_projections()` 每次把全部卡片/准入日志/水位**整体重写**为 JSONL（`479-496`）；`retrieve()` 内部调用 `_cards()`（全表载入 + 全量 json.loads，`474-477`）**每次检索一次**。随卡片与 admission_log 增长，检索与投影成本线性退化。 | `global_memory.py:474-496,539-552` |
| M-6 | P2 | `_next_card_id` 用 `ORDER BY card_id DESC` 字符串排序 + `int(card_id.split("-")[-1])` 取号（`374-377`）。一旦存在非 `GMEM-####` 形态 id 即抛 `ValueError`，且无独立序列/自增主键兜底。 | `global_memory.py:374-377` |
| M-7 | P1 | **L3 Procedural Memory 缺失**。设计 §13.4 要求"版本化的稳定执行规则库"（工具顺序、重试/Fail-fast 规则、预算规则、回滚条件、发布门禁、审批条件）。实现中这些全部是硬编码常量：`policy.ENV_ALLOWLIST`、`policy.DECISIONS`、`tools.TOOL_SPECS`、`context_layers.PROJECT_HARD_CONSTRAINTS`——**无版本号、无变更审计、无"单次实验不得覆盖"的机制**（因为它们根本不可变更，也就无法治理其演进）。 | `policy.py:14-33`、`tools.py:64-70`、`context_layers.py:13-37` |
| M-8 | P2 | **构造即副作用**。`GlobalMemoryStore.__init__` 直接 `mkdir` 并建库建表（`130-135`），import/探测即产生文件系统变更，与"只读探测器"场景不兼容。 | `global_memory.py:130-135` |
| M-9 | P2 | **快照无限累积**。`create_snapshot()` 在每次非 resume 运行都被调用（`question_evolution_agent.py:47`），快照文件按内容哈希落盘且无复用/清理策略。 | `question_evolution_agent.py:47`、`global_memory.py:528` |
| M-10 | P2 | **来源语义与设计不符**。设计 §13.2 要求 L1 = 实验事实（样本/轮次/节点/分支/算子/验证/评分/失败/成本/回滚/终止）。实现把 `agent_observation.json` 也纳入 L1 来源，而它是控制面观测而非实验事实，容易让系统诊断噪声进入"事实记忆"。 | `global_memory.py:31-38` |

### 3.3 优化方向

1. **内容寻址快照（优先）**：快照记录 `card_id → {version, body_sha256}`；`retrieve()` 命中 id 后**必须**校验 version+body hash，不匹配则视为 `SnapshotUnavailable` 或返回快照内嵌的卡片正文副本。这是把 M-1 从"设计承诺"变为"可验证保证"的最小改动。
2. **检索器重写**：字段加权重排 + 显式硬过滤（exclusion 命中即剔除）+ 冲突/风险惩罚 + 新鲜度衰减；把 `tokens` 从空格切分改为分词器，中文语境下当前切分基本失效。
3. **指标真实化**：`effective_rate/invalid_generation_rate` 从 `candidate_facts.payload` 统计得出，或明确改为 `null` 并在文档中声明"未度量"，避免假数据进入策略卡。
4. **水位治理**：新增 `bless-source` / `reset-watermark` 运维命令；`needs_human_review` 记录做去重（同 source+reason 只留最新）并加保留期。
5. **存储与投影解耦**：`retrieve()` 改为 SQL 层过滤 + 索引（`cards.status`、fingerprint、字段 LIKE/FTS），投影改为增量或按需重建；为 `admission_log`/`card_events` 增加归档表。
6. **补 L3**：把 `ENV_ALLOWLIST`、工具契约、重试策略、回滚/发布门禁抽为版本化配置文件（`memory/procedural/*.json` + `version` + `approved_by`），运行期只读并记入 `snapshot_prefix`——这样设计 §19.3 的"发布权限"才有承载对象。

---

## 4. 编排与决策

### 4.1 当前实现机制

- **Session 状态机**（`state.py:14-26`）：`created → context_ready → planned → executing → observing → replanning → suspended/completed/stopped/blocked/failed`，非法状态在 `update_state` 处被拒。
- **不可变计划版本**（`state.py:162-196`）：`plans/plan_rNNN.json` 逐版落盘，`replan_context.replaces_plan_path` 保留替代链，事件流记录 `plan_revision_created`。
- **确定性规划**（`planner.py:76-172`）：按 `command` 分流出 `task_plan / recovery_plan / review_plan` 三种骨架；步骤模板固定为 `check_environment → run_full_loop → observe_experiment → write_agent_report`（resume 用 `resume_full_loop`，review 只用 observe+report）。
- **搜索模式选择**（`planner.py:20-28`）：`auto` 时对 goal 做中文关键词匹配（"组合/叠加/二次进化/两算子/vertical/stack" → 纵向；"逐轮/主链/single branch" → 单链；否则 → 横向）。
- **模型辅助规划**（`planner.py:175-246`）：`AGENT_MODEL/AGENT_BASE_URL/AGENT_API_KEY` 存在时请求一次 LLM，经 schema + `validate_plan` + protected 字段 + 工具序列比对；任一失败**静默回退**确定性计划并记录 `model_fallback_reason`。
- **Policy 校验**（`policy.py:65-166`）：env 白名单、执行范围/复核模式一致性、step 必需字段、工具注册与授权、唯一 step_id、protected 参数键（prompt/router/rubric/memory/operator/schema/state）、业务失败不得写成 retry、`_validate_execution_skeleton` 强制"环境检查先于 full_loop""不得绕过 manifest 校验与真实评分"。
- **决策**（`decisions.py:19-77`）：11 条首命中即返回的规则链。

### 4.2 局限与风险

| ID | 级别 | 问题 | 证据 |
| --- | --- | --- | --- |
| **O-1** | **P1** | **重规划分支丢失 `MEMORY_SNAPSHOT_ID`**。初始计划显式写入 `env_overrides["MEMORY_SNAPSHOT_ID"]`（`144`），且 `policy.ENV_ALLOWLIST` 明确说明该变量进入 Router 缓存身份（`policy.py:29-31`）。但在 `decision == "replan"` 的**非预算**分支中，代码用 `build_plan(...)` **重建** env_overrides 并只补 `memory_snapshot_id/memory_context_key/router_cache_key` 三个顶层字段，**没有回写 env_overrides**（`255-261`）。结果：重规划后的计划与初始计划的路由缓存身份不一致，且 `_validate_execution_skeleton` 中"Snapshot 是否一致"的检查（设计 §9.4）无从落实。 | `question_evolution_agent.py:144,255-261`；`policy.py:29-31` |
| **O-2** | **P1** | **不存在真实的重规划执行循环，`rollback` 完全未实现**。`replan` 分支只做「应用预算变更 → 写新 plan revision → 写上下文 → 状态置为 `suspended/replan_pending_execution`」（`229-274`），同一进程内**不执行**新计划（`Executor.execute` 只跑一个已校验计划，`236-244`）。设计 §6.2 状态机中的 `Observing → RollingBack → Replanning` 与 §16.4 恢复规则在代码中无对应实现。<br>量化：`decide_next_action` 11 个分支中，**没有任何分支产生 `run_pipeline` / `resume_pipeline` / `run_review`**，而 `policy.DECISIONS` 收录了这三个值——它们目前是死枚举。 | `question_evolution_agent.py:229-274`、`executor.py:236-244`、`decisions.py:19-77`、`policy.py:33` |
| O-3 | P1 | **"模型不得改动执行骨架"约束不完整**。`_validate_model_plan` 只比对 5 个 protected 字段与 `[step.tool for step in steps]` 的**序列相等**（`211-218`）。它**不校验** `step_id / arguments / preconditions / budget_limit / stop_if_failed / expected_outputs / success_condition`。模型可在工具顺序不变的前提下：把 `budget_limit` 写成任意 Mapping（`validate_plan` 只检查是对象）、把 `stop_if_failed` 置 `False`、替换 arguments（只要键名不含 `_PROTECTED_ARGUMENT_MARKERS` 的 7 个词）。这与"cannot alter the registered v1 tool sequence, execution scope, or environment contract"的自述（`228-232`）不匹配。 | `planner.py:207-219,228-232`；`policy.py:117-125` |
| O-4 | P1 | **决策记录缺少关联键**。`write_decision` 写入的 JSONL 只有决策体本身，不含 `session_id / plan_revision / observation_id / tool_call_id`（`85-91`）。审计时无法从决策反查"基于哪一版计划、哪个观测、哪次调用做出的"，直接削弱设计 §18 的可观测性要求。 | `decisions.py:85-91` |
| O-5 | P2 | **搜索模式靠中文关键词子串命中**（`20-28`）。"组合/叠加/逐轮/两算子"是高频通用词，例如目标文本含"逐轮评估成本"即被判定为 `single_branch`。无结构化目标字段、无优先级、无置信度。 | `planner.py:20-28` |
| O-6 | P2 | **预算耗尽判定过宽**。`_is_budget_exhausted` 用 `"budget" in termination_reason`（`80-82`），任何含 budget 字样的原因串（含"budget_observation_ready"类噪声）都会被判为预算耗尽，进而走 `stop_and_report` 且 `requires_human_review=False`——可能掩盖真实的人工复核需求。 | `decisions.py:80-82` |
| O-7 | P2 | **Session 级断点续跑未实现**。`create_run_dir(..., exist_ok=False)`（`state.py:40`）使每次 `run`/`resume` 都开新 Session 目录；`resume_checkpoint.last_completed_step_id` 被写入（`137-140`）但**无任何消费点**，幂等账本 `tool_idempotency.json` 位于 run 目录内，新目录必为空。因此设计 §21.2"中断后能从最后确认 Checkpoint 恢复"只在**实验目录**层面成立（`resume_full_loop` + `--resume-exp-dir`），在**Session**层面不成立。 | `state.py:36-41,137-140`、`executor.py:60-61` |
| O-8 | P2 | **`plan_revision` 语义重叠**。`_deterministic_plan` 内写死 `plan_revision: 0`（`159`），随后一律被 `write_plan_revision` 覆盖；而模型计划若自带该字段也会被覆盖。字段在计划体内出现但永不代表真实版本，易误读。 | `planner.py:159`、`state.py:171-180` |
| O-9 | P2 | **`execution_scope` 与 `review_mode` 的能力不对等**。`REGISTERED_TOOLS` 支持 `reference_rebuild_only` / `debug_generation_only`，但 `policy.py:128-129` 直接拒绝任何非 `full_iteration` 的计划；同时 `_deterministic_plan` 又为此追加 blocked reason（`154-155`）。即这两个 scope 仅能产出一个"注定被拒"的计划，属半成品路径。 | `policy.py:128-129`、`planner.py:154-155` |

### 4.3 优化方向

1. **补齐执行循环**：实现 `continue / replan_execute / rollback_and_retry / start_next_round` 四类决策与对应的执行器；Session 续跑复用同一 `run_dir` 并消费 `resume_checkpoint`（与 O-7 联动）。这也正是《后续潜在的优化点》第 1 条，本报告为其补充了"rollback 完全缺失"与"三个决策枚举为死值"两个量化证据。
2. **统一 env_overrides 单一来源**：把 `MEMORY_SNAPSHOT_ID` 等快照类覆盖项从"逐处手动补充"改为由 `state` 派生的 `plan_env_overrides(state)` 单函数生成，从结构上消除 O-1 这类漏写。
3. **模型计划收敛为白名单填充**：只允许模型改写 `goal_summary / assumptions / step.intent / step.purpose`；其余字段一律以确定性计划为权威并做逐字段等值校验，而非只比工具序列。
4. **决策自描述**：决策体增加 `session_id / plan_id / plan_revision / observation_id / evidence_refs`。
5. **目标结构化**：`AgentTask` 增加 `target_failure_mode / search_mode_hint / exploration_budget` 等显式字段，关键词匹配降级为兜底并记录 `assumption`。
6. **`_is_budget_exhausted` 精确化**：改为读取结构化 `budget_exhausted` 布尔或 `termination_reason ∈ 白名单集合`。

---

## 5. 工具调用

### 5.1 当前实现机制

- **注册表**（`tools.py:64-70`）：5 个复合工具，每个声明 `version / kind / input_schema / output_schema / side_effects / idempotency_key_fields / timeout_seconds / retry_policy / expected_artifacts / observation_types`。
- **执行与重试**（`tools.py:113-180`）：逐 attempt 记 `tool_started`，分类 `retryable_system_error / fatal_system_error / tool_execution_error`（`88-96`），失败可重试时按 `backoff_seconds` 睡眠后重试。
- **幂等**（`executor.py:94-99`）：`sha256(tool + version + plan_id + 白名单输入)`，命中且 `ok` 则复用并记 `tool_reused`。
- **产物校验**（`executor.py:138-157`）：`check_environment` 校验 `ready`；`run/resume_full_loop` 校验 `experiment_dir` 存在且 `final/final_scored.jsonl` 通过 `validate_published_artifact`（sha256 + 记录数 + 配置摘要）；`observe_experiment` 校验 `agent_observation.json` 落盘。
- **Checkpoint**（`executor.py:227-233`）：成功即追加 `completed_step_ids` 并写 `checkpoint_confirmed` 事件。
- **报告延后执行**（`executor.py:246-270`）：`write_agent_report` 从常规执行流排除，待 Decision 持久化后再执行，保证"报告不早于决策"。

### 5.2 局限与风险

| ID | 级别 | 问题 | 证据 |
| --- | --- | --- | --- |
| T-1 | P1 | **Step `arguments` 对复合工具不生效**。`_run_step` 对 `run_full_loop` / `resume_full_loop` 直接传 `self.plan.get("env_overrides", {})`，完全忽略 `step["arguments"]`（`170-173`）。因此设计 §9.2 的 `arguments` 契约在真实执行中形同装饰，Planner 无法做步粒度参数化（例如为某一步单独设置 `SEARCH_MAX_DEPTH`）。 | `executor.py:166-181` |
| T-2 | P1 | **幂等键绑定 `plan_id`，跨 plan 失效**。`plan_id` 每次 `build_plan` 重新生成（`planner.py:158`），重规划后同一逻辑调用获得**不同**幂等键 → 设计 §11.2"同一幂等标识不得重复消耗模型预算"仅在单个 plan 内成立；跨 revision 的重复执行无法被识别。 | `executor.py:94-99`、`planner.py:158` |
| T-3 | P1 | **超时只杀父进程，子进程会残留**。`subprocess.run(..., timeout=spec.timeout_seconds)`（`140`）在超时后终止的是 `bash run_loop.sh` 本身，脚本派生的 python 子进程不在同一进程组内被清理（无 `start_new_session` + `killpg`，Windows 下无 `taskkill /T`）。`run_full_loop` 超时上限为 **7200 秒**，一旦触发极易留下多个仍在调用模型 API 的孤儿进程——既是成本风险也是产物污染风险。 | `tools.py:66-67,140-148` |
| T-4 | P1 | **实验目录用"最近 mtime"猜测**。`_locate_experiment_dir` 优先解析 stdout 中 `本次实验目录:`，解析失败则回退为 `exp_root/*/*` 中 `summary.txt` 的**最新修改目录**（`203-215`）。在并发运行、上次实验残留、或脚本未打印约定行时，会把上一次实验目录当作本次产物交给 observer，进而污染观测、决策与记忆。 | `tools.py:203-215,219` |
| T-5 | P2 | **无指数退避**。`RetryPolicy(2, 0.25)` 表示 2 次尝试 + 固定 0.25s 退避（`66-69`），重试等待不随失败次数增长，与设计 §16.2"有限次数重试、指数退避"不符。 | `tools.py:28-31,66-69,176-178` |
| T-6 | P2 | **失败分类基于文本正则**。`_FATAL_OUTPUT` 含 `schema`、`input.*(missing|not found)` 等宽模式（`24-25`），业务提示或模型输出中偶然出现的 "schema" 会被判定为**不可重试致命错误**；反之，真实的 schema 不兼容若措辞不同则被归为 `tool_execution_error`。 | `tools.py:24-25,88-96` |
| T-7 | P2 | **成本维度未实现**。每个结果的 `cost` 恒为 `{"known_cost": None, "unit": "not_reported"}`（`161`、`executor.py:212`），而设计 §10.2 要求工具声明成本估计、§18 要求统计"每个有效边界的调用成本"。 | `tools.py:161`、`executor.py:212` |
| T-8 | P2 | **死代码与脆弱反射**。`_execute` 末尾的 `return last_result`（`180`）在 `attempts ≥ 1` 时不可达；`_invoke_registry` 依赖 `inspect.signature` 判断是否传 `record_events=False`（`159-164`），当注册表方法签名变化时静默降级为"重复记事件"而非报错。 | `tools.py:180`、`executor.py:159-164` |

### 5.3 优化方向

1. **打通 arguments**：`_run_step` 改为「step.arguments（经 `validate_env_overrides` 白名单过滤）→ 覆盖 plan.env_overrides」的合并语义，使 Step 契约真正可执行。
2. **幂等键去 plan 化**：键改为 `(tool, version, 业务输入白名单)`；账本提升到**项目级**或 Session 级（与 O-7 的续跑共用），并保留 `plan_id` 仅作为来源记录。
3. **进程组级超时治理**：`Popen(start_new_session=True)` + 超时 `killpg`（Windows 走 `taskkill /T /F`／Job Object），并增加"超时后扫描残留 `run_loop` 进程"的收尾步骤。
4. **取消 mtime 猜测**：把"实验目录"升级为工具的**显式返回契约**（例如工具写入 `run_dir/experiment_dir.txt` 或直接返回 exit-code 语义），stdout 解析只作辅助，禁止 mtime 兜底。
5. **退避改指数**：`RetryPolicy(max_attempts, base_backoff, multiplier, jitter)`。
6. **分类去正则依赖**：让 `run_loop`/子脚本输出**结构化错误码**（如 `ERROR_CATEGORY=retryable_system_error`），正则仅作兼容兜底。
7. **接入成本**：从流水线的 `experiment_statistics.json`（已含 `model_calls/request_count/cost`）回填 `cost.known_cost`。

---

## 6. 评估与校验

### 6.1 当前实现机制

- **观测归一化**（`observer.py:14-103`）：15 种 `OBSERVATION_TYPES`，`_observation()` 生成稳定 `observation_id`（内容哈希），`normalize_tool_result` 把工具结果/实验聚合映射为统一观测（含 `severity / evidence_refs / metrics / recommended_actions / requires_replan / requires_human_review`）。
- **实验观测**（`observer.py:202-326`）：只读扫描 `round_*/`、`final/final_scored.jsonl`、`memory/`，产出 `status_counts / score_increased_count / pending_count / boundary_candidate_count / target_reached / operator_status_counts / operator_attempt_count / evidence_refs`。
- **产物完整性**（`observer.py:192-199`）：遍历 `*.manifest.json` 并调用 `validate_published_artifact`。
- **产物发布校验**（`pipeline_runtime.py:443-498`）：`format_version / stage / config_sha256 / bytes / sha256 / record_count / input sha256 / sidecar` 八项校验，是仓库内质量最高的校验器。
- **多智能体评审**（`multi_agent/*`）：19 个 Advisor 分 4 组，`ThreadPoolExecutor` 并发（Memory 组 2，其余 4），每 Advisor 独立 context slot（`advisor_context.py`），`merge_advice` 做 input_hash/snapshot 一致性校验、禁止动作正则拦截、冲突检测。
- **Skill 契约**（`skills/skill_registry.py`、`skill_loader.py`）：10 个 Skill 注册、七段式文档校验（`REQUIRED_SECTIONS`）、上下文层白名单、禁止动作集合、输出 schema 校验（`validate_skill_output`：拒绝 active 发布、拒绝替代人工边界确认、要求 `evidence_refs/artifact_refs`、体积上限 12000 字符）。
- **Global Judge**（`global_judge.py`）：Evidence Pack（只允许安全字段 + 摘要）→ 8 级诊断 × 5 类归因 → Optimization Proposal → Replay/Holdout 5 项门禁 → 独立 `publisher` 角色审批后的 append-only 发布账本（`policy_guard` 拒绝 Judge 直接改正式资产）。

### 6.2 局限与风险

| ID | 级别 | 问题 | 证据 |
| --- | --- | --- | --- |
| **V-1** | **P0** | **运行时 schema 校验几乎不存在**。全仓 `validate_instance` 只有 **2 处**调用点：模型计划的 schema 检查（`planner.py:209`）与 Skill 输出检查（`skill_loader.py:151`）。而 Observation、Context Pack、Decision、Session Manifest、Tool Result、Advisor 记录、Budget 账本这些**都是先写盘后无校验**。`schemas/` 下 20+ 个 Agent 契约实际是"文档"，不是门禁——错误结构可以静默落盘并进入下游。 | `grep validate_instance` → 仅 2 处 |
| **V-2** | **P1** | **决策 schema 与实现已漂移**。`agent_decision.schema.json` 的 `action` 枚举为 `["run_pipeline","resume_pipeline","run_review","stop_and_report","blocked"]`，**缺 `replan` 与 `suspend`**；而 `policy.DECISIONS`（`policy.py:33`）与 `decide_next_action`（`decisions.py:36,53`）都会产出这两个值。由于 V-1（无运行时校验），该不一致长期静默。 | `schemas/agent_decision.schema.json` vs `policy.py:33`、`decisions.py:36,53` |
| V-3 | P1 | **观测类型集远小于设计，Reflector 动作表无法实现**。实现仅 15 种类型（`observer.py:14-19`），设计 §12.1 要求的 `effective_boundary_found`、`judge_instability_detected`、`rollback_completed`、`memory_written`、`sample_profile_ready`、`route_selected`、`candidate_generated`、`candidate_selected`、`difficulty_gain_uncertain` **全部缺失**。后果是设计 §12.2 的动作表（如"`effective_boundary_found` → 保存边界并完成 Session"、"`judge_instability_detected` → 暂停归因并触发复评"）无法表达，Session 永远停在 `stop_and_report + manual_review_required`。 | `observer.py:14-19` vs 设计 §12.1/§12.2 |
| V-4 | P1 | **Observer 的 Manifest 校验形同虚设**。`_artifact_integrity` 只在**存在** `*.manifest.json` 时校验；无 manifest（最常见情况：只有裸 JSONL）时返回 `"not_checked"`，且 `status` 仍为 `observed`（`295`）。schema 也只允许 `not_checked \| damaged`，**不存在 "ok"**。即"校验通过"这一状态在系统里无法被表达。 | `observer.py:192-199,295`；`schemas/agent_observation.schema.json` |
| V-5 | P1 | **Skill 体系在运行链路中不产生作用**。<br>① `validate_skill_output` 仅被 `global_judge.py:465` 调用；run 流程中无任何 Skill 输出被校验。<br>② `LoadedSkill.content`（已读取的 SKILL.md 正文）在全仓**没有任何消费点**——18 处 `load_stage_skills` 调用只用于记录 `skill_loaded/skill_load_failed` 事件，以及报告里打印 skill_id。<br>即 Skills 当前是"事件发生器"：既不注入 Prompt，也不产出被校验的结构化结果，"遵循规程"无法被验证或证伪。 | `grep '\.content'` → 无消费点；`grep validate_skill_output` → 仅 1 处 |
| V-6 | P2 | **Global Judge 未挂载**。`run_agent` 不调用 `global_judge`；`global_judge.main()` 只有手动 CLI 入口，`run_loop.sh` / `run_loop.ps1` 亦无引用。设计 §17 的离线归因与受控发布在当前流程中不可达。 | `grep global_judge run_loop.sh run_loop.ps1` → 空 |
| V-7 | P2 | **统计口径脆弱**。`_metrics` 用字符串包含推断 judge 不稳定（`"judge" in label and ("unstable" in label or "disagreement" in label)`，`296-300`），叠加 `_label` 的多级字符串兜底（`232-243`），使"有效边界率 / Judge 分歧率"等核心指标取决于标签文本措辞而非结构化字段。 | `global_judge.py:232-243,291-315` |
| V-8 | P2 | **Advisor 独立性只有结构意义**。默认部署下（未配置 `ADVISOR_BASE_URL/API_KEY`），`select_model` 返回 `local-deterministic-advisor`，`request_model_advice` 直接返回 `None`（`advisor_model_client.py:21-26`），于是全部 19 个 Advisor 走本地模板（`review_advisors.py` 等），其输出只是把 `status_counts` 按固定规则镜像成一句话。多 Advisor 的"多视角交叉验证"在默认配置下恒为同一视角的复述。 | `advisor_model_client.py:21-26`、`review_advisors.py:12-31`、`memory_advisors.py:20-26` |

### 6.3 优化方向

1. **把校验从"文档"变"门禁"（最高优先）**：至少在写入前强制校验 `agent_observation / agent_decision / agent_run_state / agent_tool_result / agent_plan / budget_state`；在 `write_decision`、`_record_observations`、`save_state`、`_verify_outputs` 四处插入 `validate_instance`，失败即 `fatal_system_error`。
2. **契约漂移检查进 CI**：新增测试对 `policy.DECISIONS ∩ schema.enum`、`OBSERVATION_TYPES ∩ schema.enum`、`REGISTERED_TOOLS ∩ TOOL_SPECS`、`SkillSpec.output_schema` 存在性做集合断言，用测试锁住文档-实现一致性。
3. **补齐观测类型并实现 Reflector 动作表**：先把 `effective_boundary_found`、`judge_instability_detected`、`rollback_completed`、`memory_written` 四类接入（它们分别对应"成功终止""暂停复评""回滚""记忆写入"四条关键路径），再逐步补 `sample_profile_ready` 等阶段型观测。
4. **Manifest 缺失 = blocked**：`_artifact_integrity` 返回三态 `ok / damaged / not_checked`，并在"要求 manifest 校验"的前置条件（`published_manifest_validation_required`）下把 `not_checked` 视为未满足；schema 补 `ok`。
5. **让 Skill 真正参与运行**：在 `assemble_context_prompt` 中按 stage 注入已加载 SKILL.md 正文（并纳入 `context_cache_key` 的版本身份）；各 stage 的产物（复盘建议、诊断、报告要点）走 `validate_skill_output` 后再入报告。
6. **挂载 Global Judge**：在 `review` 命令路径中调用 `build_evidence_pack + run_global_judge`，把结果写入 `memory_global/global_judge/`，并在报告中附 proposal/shadow 摘要（保持"提案-only"语义）。
7. **Advisor 默认语义澄清**：若默认部署即确定性模式，应把 Advisor 定位明确为"结构化检查清单"而非"独立判断"，并把 `model_tier` 与"是否使用模型"的差异写入报告，避免把模板输出误读为多视角证据。
8. **指标结构化**：Evidence Pack 的 `score_summary`/`effect`/`validation` 已做字段白名单，进一步把这些字段提升为 `_metrics` 的唯一输入，禁止标签文本参与统计。

---

## 7. 失败与重试

### 7.1 当前实现机制

- **分类**：`classify_system_failure` 输出 `retryable_system_error`（超时/限流/临时/连接重置/文件锁）与 `fatal_system_error`（schema/manifest/哈希/checkpoint 身份/输入缺失），否则 `tool_execution_error`（`tools.py:88-96`）。
- **工具级重试**：`RetryPolicy(max_attempts, backoff_seconds)`，逐 attempt 记事件（`will_retry`、`retry_backoff_seconds`）。
- **业务失败隔离**：`validate_plan` 显式禁止把业务失败写进 `retry`（`policy.py:119-120`）；`score_increased` 被映射为负收益并强制人工复核（`decisions.py:45-47`）。
- **异常兜底**：`Executor.execute_step` 捕获 `ExecutorError/ToolExecutionError/OSError/ValueError` 统一转为 `fatal_system_error`（`217-218`）；CLI 捕获 `ExecutorError` 生成失败结果（`question_evolution_agent.py:188-189`）。
- **降级开关**：Memory 快照缺失 → `blocked`（非 resume）或 `no_global_memory`（resume）；Advisor 失败 → fail-open（`question_evolution_agent.py:205-210`）；Skill 加载失败 → 退回基础规则。

### 7.2 局限与风险

| ID | 级别 | 问题 | 证据 |
| --- | --- | --- | --- |
| **R-1** | **P0** | **可重试系统故障被强制降级为致命故障，`suspend` 路径实际死掉**。<br>① `_verify_outputs` 首行对 `ok=False` 直接提前返回（`139-140`）；<br>② 调用侧**无条件**覆写 `recoverable=False` 且 `failure_category="fatal_system_error"`（`214-216`）。<br>**实测**：让注册工具返回 `{ok: False, recoverable: True, failure_category: "retryable_system_error"}`，经 `Executor.execute_step` 后变为 `{ok: False, recoverable: False, failure_category: "fatal_system_error"}`，`normalize_tool_result` 归一化为 `tool_fatal_failure`。<br>**后果**：`decide_next_action` 中 `retryable → suspend（requires_human_review=False）` 的分支（`decisions.py:35-37`）对执行器路径**永不可达**，所有网络超时/限流/锁冲突都走 `blocked + requires_human_review=True`。直接违反设计 §3.4「业务失败与系统失败分离」与 §16.2「可重试系统错误采用有限次数重试」，也让 `tool_retryable_failure` 观测类型成为死枚举。 | `executor.py:139-140,214-216`、`decisions.py:35-37`、`observer.py:56-66` |
| R-2 | P1 | **预算在 Harness 层近乎名义约束**。<br>① 唯一的真实扣减是"若配置了 `model_calls`，则每次工具调用扣 1"（`executor.py:196-201`）——它计量的是**工具调用次数**，与"模型调用数"语义不等价；<br>② `search_steps / candidate / generation / scoring / branch / vertical_depth` 由流水线内部消费，Harness 的 `BudgetLedger` 对它们既不扣减也不感知；<br>③ `_validate_step` 检查的 `budget_limit.max_tool_calls`（`114-115`）在 `planner._step` 生成的任何计划中都不存在——真实计划只写 `max_search_steps / boundary_target`。因此 Step 级预算校验恒为空转。 | `executor.py:110-115,196-201`；`planner.py:111,136` |
| R-3 | P1 | **无整计划级重试与补偿动作**。`Executor.execute` 在首个 `stop_if_failed=True` 的失败处 `break`（`242-243`），随后直接进入 Decision；不存在"重试当前工具 → 回滚父节点 → 换算子 → 缩小范围"的恢复配方（设计 §11.1、§16.4，以及《后续潜在的优化点》第 10 条"失败处理配方库"）。与 O-2（无 rollback）同源，但此处是执行器层面的缺口。 | `executor.py:236-244` |
| R-4 | P2 | **Resume 的降级语义偏隐晦**。resume 且快照不可用时，`_memory_runtime` **新建**一个快照并把 mode 置为 `no_global_memory`（`41-50`），随后该新快照 id 被写入 state 与 plan。设计 §16.4 要求"原环境无法恢复时明确失败…降级行为必须写入 Trace"。当前实现既未在 `terminal_reason` 体现降级，也未把"原快照 id 不可用"作为独立审计字段。 | `question_evolution_agent.py:36-56,124` |
| R-5 | P2 | **异常捕获面过宽**。`executor.py:217` 把 `ValueError` 与 `OSError` 一并归入 `fatal_system_error`，参数/配置类缺陷（本应快速暴露为配置错误）被伪装成系统故障，触发人工复核而非修复。 | `executor.py:217-218` |
| R-6 | P2 | **重试不区分幂等风险**。对 `side_effects=True` 的工具（`run_full_loop`/`resume_full_loop`，`tools.py:66-67`）同样套用 `RetryPolicy(2)`，而这两个工具的重试会**重新启动整条流水线**；虽有幂等键保护，但键绑定 `plan_id`（见 T-2），跨 plan 重试可能造成重复计费。 | `tools.py:66-67`、`executor.py:94-99` |

### 7.3 优化方向

1. **保留原始可重试性（最小且最高收益的修复）**：`_verify_outputs` 的覆写改为条件式——仅当失败原因是**产物校验未通过**（`artifact_missing / artifact_validation`）时才升级为 fatal；工具自身返回的 `recoverable=True` 必须保留。这样 `suspend` 分支与 `tool_retryable_failure` 观测立即复活。
2. **建立失败配方库**：以表格形式定义 `failure_category × observation_type → 恢复动作`（如 `score_increased → rollback_parent + 换算子`；`validation_failed × N → 降该算子预算`；`retryable_system_error → suspend + 备用端点`；`artifact_missing → 重新观察后 fail-fast`），并作为 `decisions.py` 的显式数据源而非散落 if。
3. **预算下沉**：把 Step/Tool 级预算真实接入（`budget_limit.max_tool_calls`、`max_model_calls`），并让流水线的 `experiment_statistics.json` 回填 `generation/scoring` 消耗，形成"控制面预算 ↔ 领域面消耗"的核对。
4. **Resume 显式降级**：新增 `memory_mode="degraded_missing_snapshot"` + `original_memory_snapshot_id` 审计字段，并在报告中显著标注；或按设计 §16.4 直接 `failed`。
5. **收窄异常捕获**：`ValueError`/`TypeError` 归入 `configuration_error`（不触发自动重试、直达人工），`OSError` 保留为系统故障。
6. **副作用工具重试保护**：`side_effects=True` 的工具在重试前必须先确认幂等账本中不存在成功记录（当前仅在同一 plan 内生效，需配合 T-2 修复）。

---

## 8. 横切问题

| ID | 级别 | 问题 | 证据 |
| --- | --- | --- | --- |
| **X-1** | **P0** | **Harness 未接入主流程**。`run_loop.sh` / `run_loop.ps1` 中**无任何** `question_evolution_agent` / `agent_runtime` / `global_judge` 引用；全仓除 `question_evolution_agent.py` 自身外，唯一的外部消费方是 `mechanism_governance.py`（仅使用 `GlobalMemoryStore`）。Harness 目前是与既有编排**并行存在的第二控制面**，设计 §21.4"Harness 达到可用状态"的运行验收条件（每个根样本都有独立可恢复 Session）尚未在任何真实链路中成立。 | `grep question_evolution_agent run_loop.sh run_loop.ps1` → 空 |
| **X-2** | **P0** | **无运行时现场证据**。`agent_runs/`（Session 产物根目录）与 `memory_global/`（L2 记忆库）在本工作区**均不存在**；`memory/*.jsonl` 为 1 字节空文件。也就是说当前所有关于 Harness 正确性的证据**仅来自单元测试**（本次实测 59 项通过），缺乏端到端 E2E、真实实验闭环、恢复演练与并发场景验证。这是"设计承诺—实现—运行证据"三角中最薄弱的一边。 | `ls -d agent_runs memory_global` → No such file or directory；`memory/*.jsonl` = 1 byte |
| X-3 | P1 | **双重编排与配置源**。`SEARCH_MODE / MAX_SEARCH_STEPS / SEARCH_BOUNDARY_TARGET / EXECUTION_SCOPE` 既由 Agent 的 `plan.env_overrides` 注入，又由 `run_loop.sh` 自身解析并各自持默认值，未在单一处声明优先级。设计 §21.1 要求"历史字段保持兼容，不依赖临时顶层字段跨阶段传递"，但环境变量这条路正是**跨层隐式传参**，且不可在计划里被 schema 校验。 | `planner.py:80-89`、`policy.py:14-32`、`run_loop.sh` |
| X-4 | P2 | **文档—实现漂移已成规模**。已确认的不一致至少 5 处：Session 字段表（设计 §6.1 的 `policy_snapshot_id/prompt_snapshot_id/operator_snapshot_id` 从未落入 manifest，`state.py:44-79` 中缺失）、观测类型（§12.1）、L3 记忆（§13.4）、检索排序（§13.6）、发布权限链路（§19.3 的"发布权限"对象不存在）。建议建立"设计条目 ↔ 实现位置 ↔ 测试用例"三列映射表并纳入 CI 检查。 | 设计 §6.1/§12.1/§13.4/§13.6/§19.3 vs 实现 |
| X-5 | P2 | **`reporter` 中的报告体裁偏"状态罗列"**。`write_agent_report` 输出为定长 Markdown 字段清单（`reporter.py:44-105`），未包含设计 §20 要求的 `best_question / score_before / score_after / score_delta / operator_path / judge_stability / cost_summary` 等最终输出契约字段；`write_global_review_artifacts` 也只产出单一 proposal（`proposal_id` 硬编码 `proposal_001`，`reporter.py:114`）。 | `reporter.py:19-132`、设计 §20 |

---

## 9. 缺陷清单与优先级

### P0 —— 阻塞"Harness 可用"验收（建议立即处理）

| ID | 子系统 | 一句话问题 | 影响 |
| --- | --- | --- | --- |
| **R-1** | 失败与重试 | 可重试故障被无条件降级为 `fatal_system_error`，`suspend` 决策分支不可达 | 违反"业务/系统失败分离"核心原则；所有瞬时故障都升级为人工复核 |
| **M-1** | 记忆系统 | 快照只锁 `card_id`，不锁内容与版本，旧快照可读到新卡片正文 | 违反"运行中 Snapshot 不得变化"；可复现性保证失效 |
| **V-1 / V-2** | 评估与校验 | 运行时几乎无 schema 校验，且 `agent_decision` 枚举已与实现漂移（缺 `replan/suspend`） | 契约层退化为文档，错误结构静默落盘 |
| **X-1 / X-2** | 横切 | 未接入 `run_loop`，无任何真实 Session 现场 | "可用"缺少运行证据，风险全部外推 |

### P1 —— 影响可信度与可扩展性

| ID | 子系统 | 问题 |
| --- | --- | --- |
| O-1 | 编排 | 重规划分支丢弃 `MEMORY_SNAPSHOT_ID`，路由缓存身份漂移 |
| O-2 | 编排 | 无重规划执行循环，`rollback` 完全未实现，3 个决策枚举为死值 |
| O-3 | 编排 | 模型计划仅比对工具序列，可改写预算/`stop_if_failed`/arguments |
| O-4 | 编排 | 决策记录无 `session_id/plan_revision/observation_id` 关联键 |
| T-1 | 工具 | Step `arguments` 对复合工具失效，Step 契约形同装饰 |
| T-2 | 工具 | 幂等键绑定 `plan_id`，跨 plan 幂等失效 |
| T-3 | 工具 | 超时只杀父进程，`run_full_loop` 7200s 超时会残留孤儿进程 |
| T-4 | 工具 | 实验目录用"最近 mtime"猜测，可能读错实验 |
| M-2 | 记忆 | 检索为朴素子串计数，未实现设计 §13.6 的排序与硬过滤 |
| M-3 | 记忆 | 卡片 `effective_rate/invalid_generation_rate` 恒为 0.0（假数据） |
| M-4 | 记忆 | `source_rewritten` 无解封路径，审计日志无界增长（实测 5 次调用 5 条同因记录） |
| M-5 | 记忆 | 投影全量重写 + 检索全表载入，O(N) 退化 |
| M-7 | 记忆 | L3 Procedural Memory 缺失，规则硬编码无版本无审计 |
| V-3 | 评估 | 观测类型集远小于设计，Reflector 动作表无法实现 |
| V-4 | 评估 | Observer 的 Manifest 校验形同虚设，不存在 "ok" 状态 |
| V-5 | 评估 | Skills 内容永不进入上下文，Skill 输出校验在 run 链路从未触发 |
| R-2 | 失败 | 预算在 Harness 层近乎名义；Step 级预算校验恒为空转 |
| R-3 | 失败 | 无整计划级重试与补偿动作（无失败配方库） |
| C-1 | 上下文 | 截断产物为非法 JSON 片段 |
| C-2 | 上下文 | 缓存身份自相矛盾且无真实缓存 |
| C-5 | 上下文 | 缺结构化 `world_state`（父题/候选树/分支/收益/回滚点） |
| X-3 | 横切 | 双重编排与配置源，环境变量跨层隐式传参 |

### P2 —— 健壮性与工程债

`C-3 C-4 C-6 C-7`、`M-6 M-8 M-9 M-10`、`O-5 O-6 O-7 O-8 O-9`、`T-5 T-6 T-7 T-8`、`V-6 V-7 V-8`、`R-4 R-5 R-6`、`X-4 X-5`。

---

## 10. 优化路线图（建议顺序）

**里程碑 1：语义闭合（让设计承诺可验证）**
1. 修复 R-1（保留原始 `recoverable`）——单点改动，立即恢复 `suspend` 语义。
2. 修复 M-1（快照内容寻址：`card_id → {version, body_sha256}` 强校验）。
3. 落地 V-1/V-2（写入前 schema 门禁 + 契约漂移 CI 断言）。
4. 修复 O-1（`plan_env_overrides(state)` 单一来源）。

**里程碑 2：控制环打通（让 Agent 能连续行动）**
5. 实现 O-2 的 `continue / replan_execute / rollback_and_retry` 与 O-7 的 Session 续跑（复用 `run_dir` + 消费 `resume_checkpoint`）。
6. 实现 R-3 的失败配方库 + V-3 的四类关键观测（`effective_boundary_found` / `judge_instability_detected` / `rollback_completed` / `memory_written`）。
7. 打通 T-1（Step arguments）与 T-2（幂等键去 plan 化）。

**里程碑 3：领域面合流与证据积累（让 Harness 真正上线）**
8. X-1：把 Harness 接入 `run_loop` 的可选外层（`--agent` 开关），先以 `review` + `dry-run` 低风险路径积累 `agent_runs/` 现场。
9. 单样本 E2E 演练 + 中断恢复演练 + 并发演练，产出 X-2 缺失的运行证据。
10. M-2/M-3/M-5 检索与指标真实化；M-7 L3 程序性记忆版本化。

**里程碑 4：治理闭环**
11. V-5 Skills 真正注入并校验输出；V-6 Global Judge 挂载到 `review` 路径；V-8 Advisor 默认语义澄清。
12. X-4 建立"设计条目 ↔ 实现 ↔ 测试"映射表并纳入 CI。

---

## 附录 A：本次审查的验证证据

| 验证项 | 方法 | 结果 |
| --- | --- | --- |
| Harness 单元测试 | `pytest` 14 个相关测试文件 | **59 passed** |
| R-1 可重试降级 | 临时目录 + 假 Registry 返回 `retryable_system_error/recoverable=True`，经 `Executor.execute_step` | 输出 `recoverable=False, failure_category=fatal_system_error`；归一化观测 `tool_fatal_failure`（**确认**） |
| M-1 快照冻结 | 构造卡片 v1 → 建快照 → 新增证据使 `integrate()` 升级为 v2/`needs_human_review` → 用旧 `snapshot_id` 检索 | 返回 **v2 内容 + v2 版本号**（**确认快照未冻结内容**） |
| M-1（直接篡改） | 绕过 `integrate()` 直接改库中 `cards.body`，保持 id/version 不变 → 旧快照检索 | 返回被篡改正文（**确认只按 id 过滤**） |
| M-4 审计日志增长 | 同一来源连续 6 次 `extract()` | 5 条同因 `needs_human_review: source_rewritten`，无去重、无解封（**确认无界增长**） |
| X-2 运行现场 | `ls -d agent_runs memory_global` | 两目录均不存在；`memory/*.jsonl` 各 1 字节（**确认未产生真实 Session**） |
| X-1 接线 | `grep` `run_loop.sh` / `run_loop.ps1` | 无 `question_evolution_agent` / `agent_runtime` / `global_judge` 引用（**确认未接入**） |
| V-1 校验覆盖 | `grep validate_instance agent_runtime/` | 仅 `planner.py:209`、`skill_loader.py:151` 两处 |
| V-5 Skill 消费 | `grep '\.content'` + `grep validate_skill_output` | `LoadedSkill.content` 无消费点；`validate_skill_output` 仅 1 处调用 |

> 所有探针均在系统临时目录内构造内存库与假 Registry，**未读取、未修改项目内任何源码或数据文件**。

## 附录 B：与既有《后续潜在的优化点》的关系

`docs/Agent改造方案/后续潜在的优化点.md` 提出的 11 项已覆盖"重规划循环、原子工具拆分、计划模拟、world_state、实验谱系、自评置信度、策略回放、人工审批队列、失败配方库、长期调度器"等方向，判断准确。本报告在其基础上补充了 **10 项该文档未提及但属于实现级缺陷**的发现：

1. R-1 可重试故障被无条件降级为致命（导致 `suspend` 死路）
2. M-1 快照只锁 id 不锁内容/版本
3. M-4 `source_rewritten` 无解封路径导致审计日志无界增长
4. V-1/V-2 运行时无 schema 校验 + `agent_decision` 枚举漂移
5. V-3 观测类型缺失使 Reflector 动作表无法表达
6. V-4 Manifest 校验不存在 "ok" 状态
7. V-5 `LoadedSkill.content` 无消费点（Skills 仪式化）
8. O-1 重规划分支丢失 `MEMORY_SNAPSHOT_ID`
9. O-3/O-4 模型计划校验不完整、决策无关联键
10. T-1/T-2/T-3/T-4 Step arguments 失效、幂等键跨 plan 失效、超时遗留孤儿进程、实验目录 mtime 猜测

**报告结束。本次审查未修改任何代码或配置。**
