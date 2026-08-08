# agent-report-skill

## 1. Applicable scenarios（适用场景）

在生成 `agent_report.md`、只读复盘章节或多 Agent 复盘摘要时使用。不得用于替代正式评分、发布 Memory 或执行恢复操作。

## 2. Input materials（输入材料）

必须读取 AgentTask、AgentPlan、工具事件摘要、观察摘要、决策记录与可用的复盘建议。可读取 Memory 摘要和多 Agent 合并结果；不得读取完整日志、完整模型答案、密钥或全量 Memory。

## 3. Prohibited actions（禁止事项）

不得写“已自动修复算子”“已发布 active Memory”或“已确认有效边界”；不得修改 Prompt、Router、Rubric、Operator、评分、状态或正式实验产物。

## 4. Workflow（工作步骤）

1. 先陈述目标、计划和实际执行结果。
2. 将自动评分、自动诊断和人工确认状态明确分开。
3. 为风险、失败和人工复核项附上可定位证据。
4. 报告下一步仅作为建议，不作为可执行命令或状态变更。

## 5. Output structure（输出结构）

报告章节必须包含目标、计划、执行结果、观察、风险、人工复核项和下一步建议，并保留 `evidence_refs` 或 `artifact_refs` 的可审计摘要。

## 6. Failure fallback（失败降级）

缺少观察或建议时，保留可用事实并明确标记“分析不可用/证据不足”；不得把缺失分析包装成正常结论。Skill 加载失败时继续使用基础安全报告模板并记录加载降级事件。

## 7. Acceptance criteria（验收标准）

人工 reviewer 可直接读懂报告；风险项均有来源；自动结论与人工确认状态无混淆；报告不会产生正式资产变更。
