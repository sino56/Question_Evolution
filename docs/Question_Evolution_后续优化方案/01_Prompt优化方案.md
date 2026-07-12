# 01. Prompt 优化方案


> 文档版本：v1.0  
> 制定日期：2026-07-11  
> 项目：Question_Evolution / PoliceQA 题目难度进化  
> 目标弱模型：Qwen3.6-27B  
> 强模型：GPT-5.4  
> 设计基线：仓库 `main` 当前主流程、`后续思路整理.md`、`样本分类画像.md` 以及本轮已确认决策。



## 1. 方案定位

第一轮 Prompt 优化不修改整个流水线中的所有 Prompt，而只优化当前推荐路径实际调用的 active operator Prompt stack。目标是建立可版本化、可归因、可跨场景复用的生成体系，并避免把 Profile、Router、Validator、Rubric 和 Scoring 的变化混入同一次实验。

## 2. 当前实际 Prompt 调用路径

推荐路径中，样本经过 `operator_router.py` 后，`question_evolution.py` 会根据 `operator_id` 调用 `build_operator_prompt()`。实际输入包括：

```text
公共 operator 基础规则
+ O10–O18 对应 OperatorPromptSpec
+ sample_profile
+ overscore_diagnosis
+ evolution_state
+ operator_route
+ 原题
+ 参考答案
+ Qwen 当前答案
+ 原 rubric
+ validation retry 信息
```

因此，一期应优先调整：

```text
prompts/operators/base.py
prompts/operators/O10_*.py ... O18_*.py
```

而不是把主要精力投入 legacy `QUESTION_EVOLUTION_PROMPT_TEMPLATE_V1`。

## 3. 目标与非目标

### 3.1 目标

- 减少固定问法和答案脚手架；
- 每个 operator 只压一个可归因错误；
- 强化“接近但不等价”的业务判断竞争；
- 在涉黄、盗窃、违禁物品、轨迹、证据固定等场景中保持同一 operator 的语义一致；
- 保存完整 Prompt recipe 和版本信息；
- 支持 forced 与 router 两种实验模式公平比较。

### 3.2 非目标

第一轮不同时修改：

- `profile_samples.py` 的诊断 Prompt；
- `operator_router.py` 路由规则；
- `validate_evolved_question.py`、`validate_difficulty_gain.py` 的判断标准；
- `gen_rubric.py` 和 `scoring.py` 的 Prompt；
- Qwen 与 GPT 模型配置。

原因是同时修改会导致实验无法归因。

## 4. 四层 Prompt 架构

### 4.1 公共基础 Prompt

职责是规定所有候选必须满足的安全边界：

```text
单主轴
不引入题外事实
不靠题长、格式、多任务制造难度
不泄漏 operator 名称和答案标签
给足可回答事实
主失分点可以稳定评分
保留原题核心领域和事实底座
```

基础层不得包含具体场景案例，否则会把所有 operator 拉向同一业务表面。

### 4.2 Operator Spec

职责是稳定描述能力轴：

| Operator | 稳定语义 |
| --- | --- |
| O10 | 相近业务判断之间的证据充分性梯度 |
| O11 | 由可见端点事实擅自补设不可见区间状态 |
| O12 | 强线索替代尚未闭合的共同必要条件 |
| O13 | 新增一个事实后原评价是否仍可保留 |
| O14 | 题面信息闭包与隐藏前提越界 |
| O15 | 单变量改变后结论保留范围如何迁移 |
| O16 | 相近正常解释使异常强度下降，但不必然使风险消失 |
| O17 | 触发处置的门槛与证明事实的门槛混淆 |
| O18 | 同域基线、统计样本或范围口径错配 |

### 4.3 Reasoning Adapter

决定 operator 在当前推理任务中采用什么认知形态，例如：

```text
近似表述竞争
单变量后评价调整
证据链断点判断
异常分支处置
步骤依赖校验
遗漏信息检查
多角色协同判断
```

它不改变 operator 的核心 failure mode。

### 4.4 Scenario Adapter

决定具体业务对象、证据形态和合理干扰项，例如：

```text
视频身份识别
轨迹追踪与盲区
行为异常与正常解释
证据固定与证据效力
交通车辆与人车关联
涉黄人员进出与伴随关系
盗窃踩点、望风、接应
违禁品交接、藏匿、转运
```

Scenario Adapter 只能选择和重组样本已有事实，不得自动补充新证据。

### 4.5 Modifiers

只做轻量修饰：

```text
视频阶段
案件特征
难度标签
历史失败表面形态
允许的新事实数量
```

Modifiers 不能决定基础 operator，也不能形成完整笛卡尔积。

## 5. Operator 语义稳定与版本管理

建议每个候选保存：

```json
{
  "base_prompt_version": "qe_base_v2",
  "operator_id": "O17_action_vs_fact_threshold",
  "operator_semantic_version": "1.0",
  "operator_prompt_version": "o17_prompt_v3",
  "reasoning_adapter_id": "decision_node_v1",
  "scenario_adapter_id": "prohibited_item_transfer_v1",
  "prompt_recipe_hash": "sha256:..."
}
```

版本规则：

- 只改措辞、示例、禁止模式：升级 `operator_prompt_version`；
- failure mode 或能力轴实质变化：升级 `operator_semantic_version`，必要时新增 operator ID；
- 公共规则变化：升级 `base_prompt_version`；
- Adapter 单独升级自己的版本。

Memory、报告和效果矩阵必须按 semantic version 与 prompt version 分组，禁止把不同版本混成一条算子历史。

## 6. Prompt 输出合约

建议统一生成结果：

```json
{
  "evolved_prompt": "完整的新题",
  "operator_used": "O17_action_vs_fact_threshold",
  "ability_axis": "action_vs_fact_threshold",
  "target_subclaim": "本次只压测的最小判断",
  "boundary_hypothesis": "预期边界",
  "expected_qwen_failure": "Qwen 最可能的具体错误",
  "expected_evaluation_focus": ["检查点1", "检查点2"],
  "surface_form_family": "report_statement_competition",
  "source_fact_map": [
    {"fact_id": "f1", "source": "original_prompt", "usage": "保留"}
  ],
  "new_fact_count": 0,
  "notes_for_reference": "基本适用"
}
```

新增 `source_fact_map` 的目的，是让后续事实检查能够确认候选题中的关键事实来源于哪里；第一版可由生成模型输出，再由轻量规则核验。

## 7. Forced 与 Natural 两套 Prompt 实验

### 7.1 Forced Operator Qualification

- 只在 `strong_match` 或经复核的 `possible_match` 样本上指定 operator；
- 每个 operator 尽量覆盖 3 个不同场景样本；
- 保留 Router 的 shadow result，但不让它控制本次生成；
- 使用隔离 Memory；
- 用于判断 operator Prompt 是否有潜力。

### 7.2 Natural Router Validation

- 使用未参与 Prompt 调整的 holdout 样本；
- Router 决定 primary、backup 和 avoid；
- 使用同样的 K、M、R；
- 用于判断完整系统是否正确选择算子。

### 7.3 对照结论

| Forced | Natural | 解释 |
| --- | --- | --- |
| 有效 | 有效 | 算子和路由均正常 |
| 有效 | 无效 | 路由、画像或 Memory 可能错配 |
| 无效 | 无效 | Prompt 或能力假设需要修订 |
| 无效 | 有效 | 强制样本选择可能有偏，或自然结果偶然 |
| 证据不足 | 很少选中 | 暂不判定 |

## 8. Prompt 迭代流程

```text
建立 v_current 基线
→ 为每个 operator 收集生成质量、硬失败和平均降分数据
→ 只选择一个主要问题修改 Prompt
→ 生成 v_next
→ 在同一 forced manifest 上对照
→ 再用新的 natural holdout 验证
→ 保留版本，不覆盖旧 Prompt
```

每次修改必须写清：

```text
修改假设
改动段落
预期改善
可能副作用
回滚版本
```

## 9. 首轮 Prompt 优化重点

优先检查：

1. 是否把真正难点直接写进题面；
2. 是否反复生成“两项报告表述二选一”的固定模板；
3. 干扰项是否只是明显陪跑；
4. 是否靠新增大量事实或任务压分；
5. 是否把 operator 术语、缺口名称或完整解题路径泄漏给 Qwen；
6. 是否在不同场景中仍然考同一个能力轴；
7. 是否存在“题写得更清楚，反而更容易”的 clarity trap。

## 10. 文件与接口改造

### 新增

```text
prompt_recipe.py
prompts/base/qe_base_v2.py
prompts/adapters/reasoning/registry.py
prompts/adapters/scenario/registry.py
schemas/prompt_recipe.schema.json
```

### 修改

```text
prompts/operators/base.py
prompts/operators/O10_*.py ... O18_*.py
question_evolution.py
analyze_evolution_effect.py
update_sample_state.py
```

### 兼容策略

未提供 adapter 或版本字段时：

```text
reasoning_adapter = null
scenario_adapter = null
base_prompt_version = legacy_active_stack
```

历史数据仍可读。

## 11. 验收标准

一期 Prompt 优化验收不要求所有 operator 均有效，而要求：

- 每个候选都保存完整 recipe 与 hash；
- 同一个 `operator_id` 的核心语义不因版本变化而漂移；
- Forced 与 Natural 结果可分开统计；
- 生成失败可区分为 assignment mismatch、Prompt 执行失败和效果失败；
- 新 Prompt 不增加硬风险比例；
- 至少可以指出每个 operator 当前属于 `promising`、`scene_limited`、`prompt_needs_revision`、`currently_unsupported` 或 `insufficient_evidence`；
- 旧 Prompt 可一键回滚。

## 12. 单元与回归测试

- Recipe 装配顺序固定；
- Prompt hash 对相同配置稳定；
- Semantic version 不允许空；
- Adapter 不得输出未在 source fact map 中出现的关键事实；
- Forced mode 必须覆盖 Router 选择，但保留 shadow result；
- Natural mode 不得读取 forced operator；
- Prompt version 必须进入 candidate、node、Memory 和报告。
