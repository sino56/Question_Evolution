# planning-strategy-skill

## 1. Applicable scenarios（适用场景）

当 Planner 或 Replanner 需要在单算子、横向多算子、纵向叠加、只读复盘和恢复计划之间选择时使用。它只给出受限建议，不能替代 Plan Validator。

## 2. Input materials（输入材料）

必须读取 `task_context`、预算、允许工具、Top-K Memory 摘要和当前 observation 摘要。不得读取完整 Memory、未经校验的策略卡，或将完整父上下文注入规划。

## 3. Prohibited actions（禁止事项）

不得绕过 Plan Validator；不得跳过真实评分；不得让 Memory 覆盖 Router 结果；不得直接执行计划、修改正式资产或发布 active Memory。

## 4. Workflow（工作步骤）

1. 先确认任务范围、预算、允许工具和已冻结快照。
2. 根据观察摘要选择保守、横向、纵向、只读或恢复计划类型。
3. 明确停止条件和人工复核阈值。
4. 将任何候选计划交给现有 policy 与 Plan Validator，验证失败时降级为基础确定性计划。

## 5. Output structure（输出结构）

输出 `recommended_plan_type`、`budget_recommendation`、`operator_strategy`、`stop_conditions`、`human_review_thresholds`、`evidence_refs` 和 `artifact_refs`，其中计划仅为候选建议。

## 6. Failure fallback（失败降级）

预算、观察或 Memory 摘要不足时，选择保守的基础确定性计划并记录缺失信息；不臆造算子或绕过验证。加载失败时既有确定性 Planner 保持可用。

## 7. Acceptance criteria（验收标准）

建议可被既有 policy 校验；重规划原因可解释；没有建议能改变执行骨架、真实评分或冻结快照。
