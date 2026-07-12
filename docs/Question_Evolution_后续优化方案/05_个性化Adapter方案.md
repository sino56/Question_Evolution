# 05. 个性化 Adapter 方案


> 文档版本：v1.0  
> 制定日期：2026-07-11  
> 项目：Question_Evolution / PoliceQA 题目难度进化  
> 目标弱模型：Qwen3.6-27B  
> 强模型：GPT-5.4  
> 设计基线：仓库 `main` 当前主流程、`后续思路整理.md`、`样本分类画像.md` 以及本轮已确认决策。



## 1. 方案定位

个性化不是为每个业务场景创建一整套独立 operator，而是把稳定的能力算子与题目认知形态、业务内容分离：

```text
失败模式 / 能力边界
→ 基础 operator
→ reasoning adapter
→ scenario adapter
→ modifiers
```

当前没有可用外部知识库，所有适配必须聚焦样本给定数据。

## 2. 分类数据现状

当前《样本分类画像》为 LLM 初步分类，不是人工金标准。主要问题：

- “流程/规范型”243/300，占约 81%，粒度过粗；
- 场景高度重叠，身份识别、轨迹追踪、行为异常、证据固定经常同时出现；
- Markdown 不能被运行时稳定消费；
- 缺少标签来源、置信度和复核状态。

## 3. 分类校准计划

已确认首轮人工复核 50 条：

```text
随机抽样 30 条
+ 低置信、冲突或边界样本 20 条
```

复核目标不是立即重标全部数据，而是校准：

- 标签定义是否互斥或可多选；
- 7 个流程子类型是否覆盖充分；
- 主场景和次场景是否稳定；
- sample-operator strong/possible/mismatch 的判断规则；
- 哪些标签应合并或新增。

## 4. 机器可读 taxonomy

```json
{
  "sample_id": "6406",
  "taxonomy_version": "taxonomy_v1",
  "reasoning_task_type": "流程/规范型",
  "reasoning_task_subtype": "决策节点与升级条件",
  "primary_scene": "视频轨迹追踪与时空关联",
  "secondary_scenes": [
    "视频行为模式与异常识别",
    "视频证据固定与取证规范"
  ],
  "modifiers": {
    "video_stage": ["监控盲区", "跨摄像头接力"],
    "crime_features": ["涉毒", "车辆转运"],
    "difficulty": "专业"
  },
  "label_source": "llm_initial",
  "review_status": "unreviewed",
  "label_confidence": {
    "reasoning_task_subtype": 0.74,
    "primary_scene": 0.88
  }
}
```

## 5. 主场景与次场景

### 5.1 Primary Scene

恰好 1 个，决定主要业务对象和 scenario adapter。

### 5.2 Secondary Scenes

最多 2 个，只提供辅助证据槽位和干扰项，不直接决定基础 operator。

例如：

```text
primary：视频轨迹追踪与时空关联
secondary：身份识别、证据固定
```

这样既不丢失重叠信息，也避免多个场景 adapter 同时主导题目。

## 6. 流程/规范型七个子类型

| 子类型 | 核心认知问题 | 常见题目变化 |
| --- | --- | --- |
| 操作步骤与先后依赖 | 哪一步必须先完成 | 删除前置步骤、交换顺序 |
| 信息完整性与遗漏检查 | 当前信息是否足够 | 隐去必要信息、加入近似无效信息 |
| 风险点与禁止事项 | 哪种做法会失效或越界 | 合规与不合规操作竞争 |
| 证据链构建与固定规范 | 如何形成连续可用证据 | 设置链条断点和关联竞争 |
| 异常分支与补救措施 | 正常流程失效后怎么办 | 设备故障、盲区、时间偏差 |
| 决策节点与升级条件 | 何时观察、核查、处置、定性 | 门槛与评价保留范围 |
| 多人协同与职责分工 | 角色如何交接、验证 | 望风、接应、分头调取 |

这些是 reasoning subtype，不是新的 O10–O18。

## 7. Reasoning Adapter 设计

建议首批 adapter：

```text
step_dependency_v1
information_completeness_v1
risk_and_prohibition_v1
evidence_chain_v1
exception_recovery_v1
decision_escalation_v1
multi_actor_coordination_v1
comparison_alignment_v1
mechanism_explanation_v1
case_chain_reasoning_v1
fault_diagnosis_v1
```

每个 adapter 只定义：

- 推荐题目结构；
- 可允许的事实操作；
- 禁止的表面脚手架；
- 输出任务上限；
- 如何保持单主轴。

它不定义 Qwen failure mode，不能替代 operator。

## 8. Scenario Adapter 设计

首批优先覆盖样本量和业务价值较高的场景：

```text
identity_and_feature_matching_v1
trajectory_and_spatiotemporal_link_v1
behavior_anomaly_v1
evidence_preservation_v1
vehicle_and_person_relation_v1
sex_industry_scene_v1
theft_and_scouting_v1
prohibited_item_transfer_v1
```

每个场景 adapter 包含：

```text
允许使用的题面对象
常见证据关系
合理但接近的替代解释
常见错误表述
绝对禁止自动补充的事实
```

## 9. 无外部知识约束

Adapter 只能引用：

```text
原题 prompt
现有参考答案
候选答案
meta_info 标签和 answer_from_book
已存在的同一条样本事实
```

禁止自动新增：

- 法律条文、司法解释；
- 未给出的鉴定结果；
- 未给出的交易、支付、通信或搜查记录；
- 物品最终性质；
- 数据库命中；
- 特定部门权限或程序结论。

### Source Fact Map

生成结果必须标明关键事实来源。若一个事实无法对应到现有数据，则 Validator 硬拒绝。

## 10. Adapter 装配规则

```python
operator = route(failure_mode)
reasoning = select_reasoning_adapter(taxonomy.reasoning_task_subtype)
scenario = select_scenario_adapter(taxonomy.primary_scene)
modifiers = select_allowed_modifiers(taxonomy, sample_facts)
prompt = assemble(base, operator, reasoning, scenario, modifiers)
```

优先级：

```text
operator 的硬边界
> reasoning adapter
> scenario adapter
> modifiers
```

若 adapter 与 operator 冲突，以 operator 为准；若无法兼容，应返回 `adapter_incompatible`，而不是强行生成。

## 11. Sample-Operator 适配性

每个组合分为：

```text
strong_match
possible_match
mismatch
```

判断依据：

- 样本是否具备该 operator 的核心语义结构；
- 是否无需新增题外事实；
- 是否能保持原题核心能力；
- 是否能构造至少两个接近判断；
- 是否能稳定评分。

Forced 实验只使用 strong match，少量复核后的 possible match 可进入探索。

## 12. 避免笛卡尔积

不创建：

```text
9 operator × 11 reasoning adapter × 8 scenario adapter × 全部 modifiers
```

而是运行时组合。只有反复验证有效的组合才写入：

```text
adapter_compatibility_registry.json
```

记录：

```text
operator + reasoning + scenario
promising / scene_limited / incompatible / insufficient_evidence
```

## 13. 实施阶段

### 13.1 Taxonomy V1

- 转换 Markdown 为 JSONL；
- 复核 50 条；
- 固化定义和示例；
- 标注置信度和来源。

### 13.2 Reasoning Adapter Pilot

先在不改变 scenario 的情况下测试 7 个流程子类型。

### 13.3 Scenario Adapter Pilot

选择涉黄、盗窃、违禁品三类重点场景，验证同一 operator 是否能跨场景保持能力语义。

### 13.4 组合注册

对真实有效的 operator-adapter 配方形成白名单建议，但不立即做硬路由。

## 14. 文件与接口

```text
sample_taxonomy.py
taxonomy_review_manifest.jsonl
sample_taxonomy.jsonl
prompts/adapters/reasoning/registry.py
prompts/adapters/scenario/registry.py
adapter_compatibility_registry.jsonl
schemas/sample_taxonomy.schema.json
schemas/adapter_spec.schema.json
```

## 15. 验收标准

- 50 条复核有明确差异记录和 taxonomy 修订；
- 每条运行样本有 primary scene，secondary 不超过 2；
- 流程/规范型可落到 7 个子类型之一或 `other_review`；
- Adapter 不引入题外事实；
- 同一 operator 在不同场景中语义稳定；
- 相同场景不再只能生成固定表面模板；
- Adapter 冲突可显式失败，不静默降级；
- 历史无 taxonomy 数据可使用 null adapter 兼容运行。
