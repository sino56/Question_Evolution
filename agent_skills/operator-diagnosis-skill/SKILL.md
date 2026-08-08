# operator-diagnosis-skill

## 1. Applicable scenarios（适用场景）

用于复盘算子表现，区分适用条件、生成质量、样本不匹配和校验过严等原因。不得把单次候选失败解释为整个算子无效。

## 2. Input materials（输入材料）

必须读取算子 ID、候选问题、父题、验证结果、评分变化、`not_applicable` 记录、无效生成记录与 `evidence_refs`。只读取审计摘要和产物引用，不读取完整父上下文或完整模型响应。

## 3. Prohibited actions（禁止事项）

不得直接改算子 Prompt；不得因单次 `not_applicable` 禁用整个算子；不得修改 Router、评分、状态或 active Memory。

## 4. Workflow（工作步骤）

1. 将生成失败、样本不适合、验证风险和评分负收益分别归因。
2. 比较父题与候选的可回答性、泄漏风险和目标机制。
3. 仅提出适用条件、排除条件或人工复核建议，并绑定证据。
4. 证据不足时不推断算子全局表现。

## 5. Output structure（输出结构）

输出 `operator_risks`、`applicability_conditions`、`exclusion_condition_suggestions`、`needs_human_review`、`evidence_refs` 和 `artifact_refs`；所有建议均为非执行性建议。

## 6. Failure fallback（失败降级）

缺少父题、验证或评分证据时输出待复核，不产生算子停用或 Prompt 修改建议。加载失败时保留现有只读诊断与路由策略。

## 7. Acceptance criteria（验收标准）

能清楚区分算子失败与样本不适合；不因单次失败惩罚整个算子；每项结论有证据；不修改任何正式资产。
