# Question Evolution 离线生成评分就绪样本提示词（source-faithful v1.1）

请在当前 `Question_Evolution` 项目中，基于现有进化机制，对原始 JSONL 离线生成可供后续人工运行弱模型评测脚本的新 JSONL 样本。

```text
输入审计与任务授权
→ 来源分析与样本画像
→ 候选选择
→ Mode-aware 算子路由
→ Hidden Planner
→ Public Fact Compiler
→ Blind Surface Writer
→ 本地规则/Schema校验与Codex语义复核
→ 难度结构校验
→ 候选选择
→ 新参考答案重建与独立核验
→ 新Rubric/score_prompt重建
→ 本地契约与可消费性验证
→ 输出评分就绪JSONL
```

输入文件：

```text
data/四大场景测试样本.jsonl
```

最终输出文件：

```text
output/police_qa_4scene_evolution.jsonl
```

原始输入不得被覆盖。最终只要求交付上述新 JSONL。阶段中间结果可作为原子发布和断点恢复所需的临时/checkpoint产物保留，但不得替代最终文件。本任务到评分材料准备完成为止，不调用弱模型、不执行自动评分、不做效果分析。

---

## 0、最高优先级执行解释：防止来源门禁过度保守

本节优先级高于后续阶段中可能产生歧义的表述。若后文与本节冲突，以本节为准。

### 0.1 目标与非目标

本任务不要求达到固定进化比例，也不得为了产生进化样本而新增事实。但是必须积极、逐条识别输入中已经存在的可用事实，不能以“抽象方法题”“案例不完整”“事实不够丰富”“来源材料主要是原则”等概括性理由批量透传。

允许的难度提升不仅包括增加案例事实。本任务禁止增加案例事实，但允许在不新增事实的情况下：

- 消除原问题中的答案预设、角色预设或诱导方向；
- 将问题改写为中性的证据边界判断；
- 要求区分“可以确认”“仅能怀疑”“尚不能确认”；
- 比较题面已经存在的对象、解释、阶段、关系或候选；
- 约束已有多人物、多对象或多阶段事实的正确绑定；
- 校准已有事实能够支持的结论强度；
- 要求识别现有信息不能支持的越级结论；
- 将单点结论改为基于已有事实的组合推理；
- 对题面已给出的程序、规则或判断条件进行适用边界分析。

以上属于任务结构改写，不属于新增案件事实。

### 0.2 必须逐句、逐分句分析来源

不得对整条记录直接统一判定为“无观察事实”。必须先按句子和分句拆分题面，对每个片段分别判断。

以下内容应优先进入 `source_observation` 候选：

- 题面明确出现的人物、车辆、物品或其他对象；
- 对象已经实施的行为；
- 已明确发生的事件及其先后顺序；
- 已明确给出的地点、时间、路径或位置变化；
- 已描述的可见性、遮挡、出现、消失或状态变化；
- 已明确出现的人物—行为、人物—物品、对象—位置或事件—事件关系；
- 题面已经列出的两个或多个解释、对象、步骤、阶段或比较项；
- 使用“疑似”“可能”“未看清”“无法确认”等措辞描述的不确定观察。

以下内容不得作为观察事实：

- 问题要求模型得出的结论；
- “是否构成”“是否属于”“应认定为”等待判断内容；
- 旧参考答案反向补充的案件事实；
- 仅由常识、答案或算子设计推导出的因果关系；
- 原题没有出现的动机、身份、共谋、阈值、路径或竞争解释。

“原题中的句子不自动等于观察事实”仅表示必须逐片段分类，不表示原题不能作为事实来源。没有逐片段排除理由时，不得将整条记录统一写成 `source_observations=[]`。

### 0.3 输入题面是合法的本地来源

题面逐字包含的情境描述可以使用以下定位：

```text
input_file
record_identity
JSON Pointer
prompt逐字span
行号
内容哈希
```

外部书籍页码或文档来源暂时无法核验，不会自动使题面中逐字存在的观察事实失效。此时必须区分：

```text
题面内部事实可追溯：resolved_from_input
外部真实性或出处：unresolved_external_provenance
```

不得因为外部出处 `unresolved`，就把题面内部已有观察全部清空。若任务答案必须依赖外部规则，而该规则无法核验，则规则状态仍应标记 `unresolved`，并据此进入人工复核或透传。

### 0.4 必须拆分答案侧来源材料

不得将 `answer_from_book`、旧参考答案或参考材料整块设置为不可公开，也不得把它们整块当成事实来源。必须拆分为：

```text
source_supported_rule_primitive
source_supported_procedure
design_only_principle
derived_summary
answer_direction
case_specific_conclusion
unsupported_addition
```

处理规则：

- 旧答案不能证明题面未出现的案件事实；
- 可可靠定位、适用条件明确且有效状态可确认的规则原语或程序步骤，可以进入 rule ledger；
- 规则来源、适用对象、场景、版本或有效状态无法确认时标记 `unresolved`；
- `derived_summary`、`answer_direction` 和具体案件结论不得进入 Writer；
- 不得仅因为某内容出现在旧答案中，就认定其可以公开；
- 也不得仅因为某内容出现在旧答案中，就一律禁止使用其中可验证的规则原语。

### 0.5 必须区分事实、规则与任务结构槽位

算子槽位必须拆分为：

```text
factual_required_slots
rule_required_slots
task_structure_slots
optional_slots
```

其中：

- `factual_required_slots`只能由当前来源世界的已有事实满足；
- `rule_required_slots`只能由可验证的有效规则满足；
- `task_structure_slots`可以由中性提问方式、输出边界和推理任务要求满足；
- `optional_slots`缺失不能被误判为硬槽位缺失。

Writer可以创建中性的任务结构，但不得创建案件事实。以下表述通常属于任务结构，不属于新增事实：

```text
“现有信息能够支持到什么程度？”
“分别说明可以确认和不能确认的内容。”
“比较题面已有两种解释与现有事实的相容程度。”
“指出作出更强结论还需要哪一类信息。”
```

询问缺少的信息时，不得暗示该信息实际存在，也不得泄漏 Hidden Planner 中预设的缺失链路、正确假设或唯一性结果。

### 0.6 透传必须有记录级证据

以下理由单独出现时，不足以支持 `pass_through`：

```text
“这是抽象方法题”
“只有方法原则”
“案例事实较少”
“缺少完整案件”
“不适合构造复杂案例”
“算子槽位不足”
```

决定透传前必须记录：

```text
existing_observation_candidates
accepted_source_observations
rejected_observation_candidates
rejection_reason_per_span
available_rule_primitives
attempted_safe_transformations
operator_slot_matrix
pass_through_counterfactual
```

`pass_through_counterfactual`必须明确说明：

1. 已尝试哪些不新增事实的任务结构改写；
2. 每种改写具体缺少哪个事实或规则硬槽位；
3. 强行生成会新增什么未经授权的内容；
4. 为什么不能仅通过中性化、比较、绑定或结论校准完成进化。

### 0.7 数据集级防塌缩检查

以下情况触发强制二次审计，不得直接发布最终文件：

```text
全部记录的 source_observation 均为空
全部记录的 selected_operator_ids 均为空
全部记录均为 pass_through
某个统一的 mode_reason 覆盖全部记录
某个统一的 routing_reason 覆盖全部记录
```

二次审计必须：

- 重新逐句检查题面中的行为、顺序、关系、状态和不确定性；
- 重新拆分旧答案中的规则原语与答案结论；
- 检查是否错误地把任务结构槽位当成事实硬槽位；
- 对相关算子重新进行硬槽位和相邻算子比较；
- 对每条透传记录给出独立证据，不得复用批量模板理由。

二次审计不保证必须产生进化样本，也不得设置最低进化数量。若复核后仍全部透传，只有在每条记录都具备独立、具体且可验证的槽位不足证据时才允许发布，并必须在最终回复中将“全量透传”列为显著风险。

### 0.8 当前数据的分类示例

以下示例仅说明分类原则，不预先指定算子或答案：

- “目标进入监控盲区”“后续路口出现疑似目标”“无法看清正脸”是不同观察候选，不得整体归入答案预设；
- “一人抛出物品”“另一人接住”“随后将物品丢弃”是人物—行为与事件顺序候选，不得因题目涉及行为关系判断而全部清空；
- 题面已经明确出现的两种解释或两个比较对象可以作为现有候选使用，但不得额外创造第三种解释；
- “应当如何认定”“哪种解释正确”等问题方向不是观察事实，应从公开事实中分离。

---

## 一、任务授权与执行范围

本次任务采用以下显式授权：

```json
{
  "evolution_authorization": {
    "allowed_evolution_modes": ["source_faithful"],
    "controlled_synthesis_authorized": false,
    "hypothetical_adaptation_authorized": false,
    "authorization_source": "this_task_prompt",
    "authorization_id": "codex_offline_scoring_ready_source_faithful_v1_1"
  },
  "execution_scope": {
    "max_stage": "scoring_material_preparation",
    "allow_reference_rebuild": true,
    "allow_model_answering": false,
    "allow_judge_scoring": false,
    "allow_effect_claim": false
  }
}
```

强制解释：

- 本任务只授权 `source_faithful`；`pass_through` 作为安全退出始终允许；
- 即使样本适合构造假设案例，也不得选择 `controlled_synthesis` 或 `hypothetical_adaptation_from_source`；
- Source Analyzer只能判断 `eligible`，不能授予合成权限；
- 不得新增人物、车辆、时间、地点、路径、来源、规则、阈值、竞争解释或案件事件；
- source-faithful 所需事实或规则硬槽位不足时，只能人工复核或透传，但必须先完成逐句事实提取、答案侧材料拆分和任务结构槽位检查；
- 不得把可由中性提问满足的任务结构槽位误判成来源事实不足；
- `execution_scope`只允许完成新参考答案、Rubric和`score_prompt`准备；不得调用弱模型、Judge或效果分析阶段；
- 最终产物必须可由用户后续手工输入现有评测脚本，但本任务不得代替用户发起模型请求。

如以后需要受控合成，必须由用户另行修改授权，并同时启用第28、29号方案要求的 world、fact ledger、rule ledger和合成资格门禁。

---

## 二、环境与安全边界

- 环境没有可供代码调用的GPT/Codex API；
- 后续由用户手工使用现有StepFun 3.7配置，或通过CLI指定OpenAI-compatible弱模型进行真实回答与评分；本任务不执行该步骤；
- 你作为当前Codex/GPT，直接完成来源分析、画像、失败机制、模式判断、路由、隐藏规划、题面进化、参考答案、Rubric、`score_prompt`和必要语义复核；
- 禁止新增、伪造或调用GPT/Codex API客户端；不得通过项目代码调用GPT；
- 不新增脚本，不改动`run_loop.sh`，不修改`scoring.py`或既有评分Prompt的业务语义；
- 不读取、输出、打印、提交或修改`config.py`中的密钥、URL或私有配置；
- 不提交、stage、commit或push Git；不要把大型`experiments/`产物纳入Git；
- 开始前检查工作区已有修改，绝不覆盖无关用户改动；
- 不按prompt文本去重；相同题面的不同`sample_id/index`必须独立保留；
- 正式输出使用现有原子/可恢复发布机制，或临时文件校验成功后原子替换；
- 阶段失败写入对应`*.failed`或结构化失败记录，并以非零状态退出；不完整正式文件不得进入下一阶段；
- 日志和报告不得包含API Key、私有URL、完整私有Prompt或完整弱模型答案；
- 不运行`round0_stability_probe.py`、`multitrial_evaluate.py`、`collect_answers.py`的真实模型调用、`scoring.py`的正式评分入口、`analyze_evolution_effect.py`或`update_sample_state.py`；
- 允许导入不发起网络请求的本地契约函数，对Rubric和`score_prompt`做消费性断言。

---

## 三、开始前必须完整阅读

按以下顺序阅读并遵守：

1. `AGENTS.md`
2. `docs/优化任务/28、问题进化规则约束问题.md`
3. `docs/优化任务/27、题面信息职责隔离与全算子语义泄漏治理优化方案.md`
4. `docs/优化任务/29、问题进化规则约束完整修改实施方案.md`
5. `docs/优化任务/21、LLM动态路由与规则约束混合路由方案-已实施.md`
6. `docs/优化任务/23、LLM 动态路由 Prompt 的算子选择精度-已实施.md`
7. `docs/优化任务/24、生成题目长度语义预算优化方案-已实施.md`
8. `prompts/operators/README.md`及O10–O33全部算子定义
9. `operator_registry.py`
10. `operator_routing_cards.py`
11. `router_contract.py`
12. `prompts/router_prompt.py`
13. `semantic_budget.py`
14. `profile_samples.py`
15. `select_evolution_candidates.py`
16. `operator_router.py`
17. `question_evolution.py`
18. `validate_evolved_question.py`
19. `light_factual_check.py`（若存在）
20. `validate_difficulty_gain.py`
21. `candidate_selection.py`
22. `gen_rubric.py`
23. `scoring.py`（只读，用于确认消费契约）
24. `multitrial_evaluate.py`（若存在，只读，用于确认最终输入契约）
25. `pipeline_runtime.py`
26. `run_loop.sh`（只读，禁止修改）
27. 相关JSONL Schema、路由、算子、校验和评分契约测试。

若v2.2字段尚未全部落地到代码：

- 不擅自修改主流程或新增脚本；
- 在现有兼容嵌套结构中记录审计字段；
- 区分“Codex语义复核”和“现有代码自动校验”；
- 不得声称未接入代码的门禁已经由运行时自动执行。

---

## 四、字段与版本兼容

必须保留既有字段，包括但不限于：

```text
sample_id、index、round、prompt、reference_answer、rubric、
rubric_thought_process、score_prompt、score_rate、scoring_result、
round0_score_trials、round0_score_summary、representative_round0_answer、
rubric_item_stability、sample_profile、overscore_diagnosis、
evolution_action、operator_route、candidate_group_id、candidate_id、
candidate_operator、candidate_generation、validation_result、
difficulty_gain_validation、candidate_selection、question_evolved、
effect_analysis、evolution_state、failure_memory_candidate、meta_info
```

新增信息优先放入现有嵌套结构。为新题评分工件建立一致版本引用：

```text
question_version_id
reference_version_id
rubric_version_id
score_prompt_version_id
```

旧答案、Rubric、`score_prompt`和评分结果可以保留用于审计，但题目实质变化后必须标为stale，不能作为新题活动材料。

---

# 阶段0：工作区与输入审计

执行只读检查：

- 工作区已有修改和未跟踪文件；
- 输入文件、所需脚本和Schema是否存在；
- 输出目录是否会覆盖其他实验；
- Python环境是否可用；
- 不读取`config.py`内容。

若输出目录已有产物，只有输入指纹、样本身份、stage config和契约版本全部匹配时才允许断点续跑。若旧产物来自不同门禁解释或授权版本，不得复用旧的mode decision、路由或候选选择结果。

逐行确认输入JSONL：

- JSON可解析；
- `sample_id`或`index`可作为稳定身份；
- 同一身份没有冲突记录；
- 原题、参考材料、答案、Rubric、`score_prompt`和评分记录的可用性；
- 兼容原始样本、round0、画像和历史流水线产物的字段缺失；
- 相同prompt的不同身份不会被合并。

对来源事实尽量建立：

```text
world_id
fact_id
global_fact_key = world_id::fact_id
origin_type
source_locator
source_provenance_status
```

`source_locator`只能使用实际可验证的文件、记录ID、JSON Pointer、逐字span、行号或内容哈希。无法可靠定位时标记`unresolved`，不得伪造。必须区分题面内部逐字定位与外部来源真实性，不得因外部定位缺失而清空题面内部事实。

已有round0回答、分数或历史效果信息只作为输入证据读取；本任务不补跑基线、不调用弱模型。没有真实评分证据时，后续画像必须标记为“离线结构判断”，不得推断或伪造弱模型表现。

---

# 阶段1：来源分析、画像与候选判断

不要调用`profile_samples.py`的真实provider。由Codex逐条完成Source Analysis、Mode Decision、画像和失败机制分析。

## 1.1 Source Analysis与Mode Decision

先把题面按句子、分句和列表项拆分，再分类为：

```text
source_observation
source_claim
task_presupposition
comparative_direction
role_or_relation_claim
uncertainty_label
design_only_principle
provided_rule_primitive
answer_direction
derived_summary
```

每条记录至少写入：

```text
existing_observation_candidates
accepted_source_observations
rejected_observation_candidates
rejection_reason_per_span
source_material_decomposition
available_rule_primitives
source_fact_extraction_audit
```

要求：

- 原题必须逐句分类；描述性片段不自动等于观察事实，但也不得无证据统一排除；
- 人物、行为、顺序、位置、状态、可见性、对象关系和不确定性描述必须进入观察候选；
- 旧参考答案不能反向证明来源事实；
- 比较方向、角色预设和答案方向与观察事实分离；
- 真实规则必须具有来源、适用对象、场景、版本和有效状态；
- `answer_from_book`和旧参考答案必须按0.4拆分，不得整块公开或整块封禁；
- 无法确认的规则或外部来源输出`unresolved`，进入人工复核或透传；
- 题面内部可逐字定位的观察事实标记`resolved_from_input`，不与外部真实性状态混淆。

本任务只允许`source_faithful`和`pass_through`。每条记录写明：

```text
source_world_status
evolution_mode
mode_reason
world_id
synthesis_eligibility
authorization_checked
authorization_id
```

即使`synthesis_eligibility=eligible`，也不得新增事实。`mode_reason`必须引用该记录的事实和规则审计结果，禁止批量复用“抽象方法题、槽位不足”之类模板理由。

## 1.2 画像与候选判断

生成或更新：

- `sample_profile`
- `overscore_diagnosis`
- `evolution_action`

画像基于题面、参考材料、已有弱模型回答、评分和round0稳定性证据。无真实评分时标明“离线初始判断”。

候选分流：

- `hard_reject`：仅用于致命问题；
- `main_chain_candidate`：有明确、可执行的进化价值；
- `exploration_candidate`：收益不确定但无致命风险；
- `pass_through_candidate`：完成安全改写尝试后仍无安全进化空间，保留原题。

不得因为评分证据缺失、难度收益尚未实测或题面较短就直接透传。没有真实评分证据时，可以依据结构潜力进入探索候选，但必须标明预测性判断。

本阶段结果可保存为临时checkpoint用于恢复，但不是正式交付文件。

---

# 阶段2：Mode-aware严格算子路由

禁止调用`operator_router.py`的真实Router provider。Codex直接读取记录、来源分析和O10–O33卡片，生成与当前`router_contract.py`兼容的`operator_route`。

路由顺序：

```text
任务授权gate
→ evolution mode gate
→ operator factual/rule slot gate
→ task structure slot检查
→ 相邻算子比较
→ Memory仅用于合格候选排序
```

只选择同时满足以下条件的算子：

- 失败机制与能力轴直接匹配；
- source-faithful事实硬槽位已被当前来源世界满足；
- 所需规则槽位已由有效规则满足；
- 缺失的仅是可以由中性提问满足的任务结构槽位时，不得误判为事实不足；
- 不需要新增任何案件事实、规则、阈值或竞争解释；
- 相比邻近算子更直接、更具体、闭包更小；
- 有独立压测价值；
- runtime policy允许执行；
- O14不作为生成算子。

主题或关键词相近不构成适配；Memory不能绕过授权、mode、事实槽位、规则槽位或资格门禁。不要求为了使用算子而构造完整新案例。如果仅通过消除答案预设、比较已有对象、绑定已有关系或校准结论强度即可形成独立压测价值，应视为合法的source-faithful候选。

为每个重点算子写入`operator_slot_matrix`，至少包含：

```text
operator_id
slot_name
slot_type
required
satisfaction_status
supporting_fact_ids/global_fact_keys/rule_primitive_ids
missing_impact
would_require_fabrication
```

缺事实或规则硬槽位时写入`uncertain_operator_rationales`，明确缺失槽位以及强行构题会补造什么。缺失任务结构槽位不构成透传理由。没有安全算子时进入pass-through，不是generation failure，但必须保存0.6要求的安全改写尝试和反事实说明。

证据片段必须逐字来自该输入样本，并尽量映射到`global_fact_key`；不得把算子卡片文字伪装为证据。

路由至少写入当前契约要求的：

```text
routing_schema_version
selected_operator_ids
primary_operator
operator_candidates
operator_decision_audit
operator_slot_matrix
selected_operator_rationales
not_selected_operator_rationales
uncertain_operator_rationales
operator_improvement_notes
routing_reason
```

没有安全算子的样本保留原题、设置`question_evolved=false`，并保存完整审计和原因。`routing_reason`必须是记录级理由，禁止对全部样本复用同一句概括性结论。

完成全量路由后执行0.7的数据集级防塌缩检查。

本阶段结果可保存为临时checkpoint用于恢复，但不是正式交付文件。

---

# 阶段3：隐藏规划、公开投影与盲题面编写

每个选中算子生成独立候选行；同源候选共享稳定`candidate_group_id`，每行有唯一`candidate_id`和明确`candidate_operator`。

## 3.1 Hidden Planner

建立只供规划、校验和参考答案重建使用的隐藏结构：

```text
operator_id
primary_axis
auxiliary_axis
target_claim
competing_interpretations
required_inference_obligations
decisive_fact_ids
relevant_distractor_fact_ids
forbidden_surface_propositions
control_plan
conclusion_contract
```

`conclusion_contract`声明允许的结论、是否要求唯一答案，以及保证唯一性的公开事实和有效规则。合法结果可以是`insufficient_evidence`、`cannot_distinguish`或`candidate_set_expanded`。

Hidden Planner不得把来源世界没有的事实写入计划后再要求Writer隐藏使用。Planner中的每个案件事实都必须映射到现有`global_fact_key`；任务结构要求应明确标为`task_structure`。

## 3.2 Public Fact Compiler

构造新的Writer公开投影，只包含：

- 允许公开且可追溯的原子观察；
- 必要且有效的规则原语或题内参数；
- 中性的任务要求；
- 不包含答案方向的表面要求。

删除：

```text
answer_role
design_basis
correct_answer
decisive_fact_ids
wrong_competitor_id
target_failure_mode
expected_weak_model_failure
rubric
reference_answer
old_score
derived_summary
case_specific_conclusion
```

Compiler只做过滤和投影，不得创作新事实、总结因果链或暴露事实角色。不得将答案侧材料整块封禁；必须按照0.4仅投影可验证的规则原语和程序，不投影具体结论及其推导结果。

公开投影至少记录：

```text
public_fact_ids/global_fact_keys
public_rule_primitive_ids
neutral_task_structure
excluded_material
exclusion_reasons
writer_input_whitelist_snapshot
```

## 3.3 Blind Surface Writer

题面只能根据Public Projection编写，不得在同一内容步骤中同时生成隐藏答案和题面。

在不调用GPT API的前提下，至少：

1. 保存Writer输入白名单快照；
2. 题面只引用其中的公开事实、公开规则和中性任务结构；
3. 生成后逐句映射`used_fact_ids/global_fact_keys/rule_primitive_ids`；
4. 独立比较题面与`forbidden_surface_propositions`；
5. 若当前Codex环境不能形成真正独立上下文，记录限制，并把无法可靠判断的检查标为`unresolved`。

禁止泄漏算子名、层级/A-B/排序/双门槛脚手架、推理清单、答案边界、正确假设状态、事实角色、规则应用结果、缺失链路、唯一性结果、反事实结果、Rubric、Judge意图或内部元数据。

允许创建中性的提问结构，但不得通过提问结构暗示某个未出现事实已经存在。题面必须使用真实能力轴，只用已有可追溯事实，自包含、可回答，保留一个自然业务判断；难度来自推理结构，不来自长度、重复或格式。

每条候选至少写入：

```text
prompt
question_evolved
candidate_generation
candidate_operator
candidate_id
candidate_group_id
operator_route
```

题目实质变化后，旧评分材料进入stale/`not_evaluated`状态。

本阶段结果可保存为临时checkpoint用于恢复，但不是正式交付文件。

---

# 阶段4：题目校验、Schema校验与Codex语义复核

先运行`validate_evolved_question.py`的本地规则和Schema校验，不启用外部GPT LLM validation。再由Codex执行独立语义复核，并与代码自动结果分开记录。

每项语义检查使用`passed/failed/unresolved`，至少覆盖：

- 身份、Schema和artifact状态；
- 事实来源、单world和mode compliance；
- 题面逐句事实回映射，确认没有新增事实；
- 可答性、冲突、题外知识依赖；
- `conclusion_contract`和唯一性；
- 单句直达、竞争存活；
- 决定性/干扰事实消融；
- 名称/顺序交换和信息量平衡；
- operator专属推理负担；
- 任务结构是否被错误当成事实或答案提示。

统一检查：

```text
direct_answer_leakage
derived_summary_leak
target_chain_disclosure
rule_ceiling_disclosure
role_binding_disclosure
competitor_elimination
answer_direction_disclosure
answer_template_disclosure
salience_imbalance
hypothesis_status_disclosure
fact_role_disclosure
rule_application_disclosure
missing_link_disclosure
uniqueness_disclosure
forced_conclusion_type
counterfactual_result_disclosure
control_role_disclosure
authority_bias
position_or_naming_bias
metadata_leakage
unsupported_fact_addition
unsupported_rule_addition
```

处置：hard risk覆盖其他结果；可修复风险最多一次定向重试；无hard evidence但关键检查`unresolved`时进入manual review或预算内exploration。不得把`unresolved`当`passed`，也不得为了通过而删除必要事实。

本地规则或Schema明确失败、且定向重试后仍未修复的候选不得进入正式候选选择；必须保留`candidate_id`、失败标签、证据和重试结果。不得通过删除全部探索候选来人为提高通过率。

记录：

```text
local_rule_validation
schema_validation
codex_semantic_validation
validation_disposition
```

本阶段结果可保存为临时checkpoint用于恢复，但不是正式交付文件。

---

# 阶段5：难度结构校验与候选选择

运行`validate_difficulty_gain.py --rule-only --validate-schema`，不启用外部GPT validator，不伪造弱模型降分。

要求：

- 写入`difficulty_gain_validation`；
- `clear_gain/probable_gain`仅表示预测性结构潜力；
- 保留无致命风险的`weak_gain`、`needs_manual_review`和探索候选；
- 仅对硬风险hard reject；
- 每组最多选择一个exploration candidate，每轮总预算继续生效；
- hard label、hard risk和模板化简化信号覆盖exploration；
- 运行现有`candidate_selection.py`，每组输出一个正式候选或合规透传基线；
- 不得设置最低进化数量或为了避免零进化而降低事实、规则、泄漏或可答性门禁；
- 也不得因尚未获得真实降分证据而把所有结构潜力候选透传。

完成候选选择后再次执行0.7的数据集级防塌缩检查。若触发二次审计，审计完成前不得发布正式输出。

本阶段结果可保存为临时checkpoint用于恢复，但不是正式交付文件。

---

# 阶段6：新参考答案、Rubric与score_prompt重建

只对最终选中候选执行，顺序必须是：

```text
新参考答案重建
→ 独立核验
→ 新Rubric/score_prompt重建
→ 用户后续手工运行弱模型正式回答
```

不得调用`collect_answers.py`或`gen_rubric.py`的GPT provider。由Codex分步骤完成，Rubric Builder不得看到弱模型答案。

## 6.1 参考答案重建与核验

只使用最终新题、公开fact ledger、有效规则/题内参数和`conclusion_contract`。旧答案正文不得作为新答案来源，hidden-only fact不得进入答案。

写入：

```text
reference_answer
supporting_fact_ids/global_fact_keys
rule_primitive_ids
answerability_status
verification_notes
```

独立核验答案是否仅由公开事实推出、是否符合结论契约、是否存在其他合理答案。无法可靠核验时进入`unresolved/needs_manual_review`，不得标记为评分就绪。

## 6.2 Rubric和score_prompt

Rubric必须是非空JSON数组；每项具有唯一非空`title`、非空`description`和整数`weight`。评分依据必须自包含，不写“与参考答案一致”，能区分关键推理、结论边界、核心事实和越级推断，不奖励术语堆砌、格式服从或无关扩写。

同时生成非空`rubric_thought_process`，说明评分维度如何覆盖当前题目的主要能力轴、结论契约和常见错误。该字段用于评分材料审计，不得把旧题Rubric思路复制为新题依据，也不得进入后续弱模型回答上下文。

`score_prompt`使用现有`gen_rubric.py`构造语义并满足`scoring.py`：

- 含且仅含一个`<<<待评答案>>`；
- 要求输出合法JSON；
- `item_scores`数量与Rubric相同；
- title逐字匹配；
- 可由`scoring.build_scoring_prompt()`消费。

状态迁移：

```text
reference: pending_rebuild → rebuilt → independently_verified
rubric/score_prompt: pending_rebuild → rebuilt
scoring: not_evaluated
```

本地断言：JSONL可解析；答案非空；Rubric可被`validate_and_normalize_rubric()`消费；占位符唯一；`build_scoring_prompt(score_prompt, "候选答案")`成功；版本引用一致。

pass-through只有在题目未变化且原评分材料通过契约验证时才可复用材料；最终文件仍保持`scoring_status=not_evaluated`，由用户后续手工重新回答和评分。

唯一正式交付文件：

```text
output/police_qa_4scene_evolution.jsonl
```

最终文件中的每条记录至少包含：

```text
sample_id或index
prompt
question_evolved
reference_answer
rubric
rubric_thought_process
score_prompt
candidate_group_id
candidate_id
candidate_operator
candidate_generation
operator_route
validation_result
difficulty_gain_validation
candidate_selection
meta_info
```

活动评分字段必须保持未评测语义：`scoring_status=not_evaluated`，不得把旧`score_rate`、旧`scoring_result`或任何离线判断写成新题实测结果。

该文件必须可直接输入现有`multitrial_evaluate.py`，不依赖Codex对话上下文或外部GPT API。

---

# 阶段7：最终自检与回复

完成前逐项确认：

1. 原始JSONL未覆盖；
2. 未新增GPT客户端/脚本，未修改主流程或评分业务语义；
3. 无无关用户改动被覆盖；
4. 身份稳定，未按prompt去重；
5. 每条记录有授权、来源世界、模式和理由；
6. source-faithful没有新增事实；
7. 路由符合授权、事实/规则硬槽位、邻近算子边界和资格门禁；
8. 候选有稳定group/candidate/operator身份；
9. Planner、Compiler、Writer和Validator职责已按可行方式隔离并记录限制；
10. 每个候选有`conclusion_contract`；
11. 已执行本地规则、Schema和Codex语义三态复核；
12. 完整泄漏标签已检查，`unresolved`未被当作`passed`；
13. 已执行rule-only难度校验和候选选择；
14. 旧评分材料未误用于新题；
15. 新答案已核验，新Rubric/score_prompt版本一致；
16. ready JSONL可被现有评分模块消费；
17. 未调用弱模型、Judge、效果分析或状态更新脚本；
18. 活动`score_rate/scoring_result/effect_analysis`未被伪造，评分状态保持`not_evaluated`；
19. 未泄漏私有配置，未stage/commit/push；
20. 未把“抽象方法题”直接等同于无观察事实；
21. 每条记录已经逐句生成观察候选及接纳/排除理由；
22. 已区分事实硬槽位、规则硬槽位、任务结构槽位和可选槽位；
23. 未将`answer_from_book`或旧参考答案整块公开或整块封禁；
24. 每条透传记录都有独立的安全改写尝试和反事实说明；
25. 若全部记录无观察、无算子、统一理由或全部透传，已经执行并记录强制二次审计；
26. 最终进化数量是逐条门禁结果，不是预设配额；既未强制进化，也未因保守模板批量透传；
27. 每个`question_evolved=true`候选的题面逐句都映射到公开事实、公开规则或中性任务结构；
28. 每个`question_evolved=false`记录都有记录级而非批量模板化的透传证据；
29. “结构校验通过的进化样本”未被表述为已经证实有效的成功进化样本。

最终回复简洁列出：

- 唯一正式交付JSONL的绝对路径；
- 输入、输出、进化、透传、失败、重试和人工复核数量；
- source world、evolution mode和operator分布；
- 观察候选、接纳观察、规则原语及来源状态的汇总；
- 本地规则、Schema、Codex三态复核和难度校验结果；
- 最终JSONL活动字段与artifact状态；
- 若触发数据集级防塌缩检查，说明触发项、二次审计动作及结果；
- 明确声明本任务未执行弱模型回答、评分和效果分析；
- 给出用户后续可手工运行的现有评测脚本命令，但不要执行；命令参数必须先依据脚本`--help`核实；
- 已知风险、`unresolved`项和待人工验证内容；
- 明确声明未修改源码、未读取私有配置、未执行Git提交操作。

最终措辞必须区分：

```text
question_evolved=true：已生成并通过当前离线门禁的进化题
scoring_status=not_evaluated：尚未运行弱模型和评分
effective_evolution：只有完成后续真实回答、评分和效果分析后才能判断
```
