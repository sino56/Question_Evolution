# memory-compile-skill

## 1. Applicable scenarios（适用场景）

用于离线整理实验事实、失败记忆、无效生成记录与效果分析，生成策略卡草案或记忆候选。不得用于当前实验中的 Memory 热更新或 active Memory 发布。

## 2. Input materials（输入材料）

必须读取局部 Memory、失败记忆、无效生成记录、效果分析、分支结果、人工复核结果及其 `artifact_refs`。不得以单次实验的无证据结论、全量 Memory 或 active Memory 写权限作为输入。

## 3. Prohibited actions（禁止事项）

不得把单次实验事实直接写入 active；不得删除历史记忆；不得用没有来源的总结生成策略；不得修改 Router、Prompt、Rubric、Operator、评分或状态。

## 4. Workflow（工作步骤）

1. 提取可归因事实，并保留实验、样本、轮次、分支、算子与评分来源。
2. 将事实与稳定分类键分开，标注证据强度和冲突。
3. 只生成 `proposed`、`shadow`、`needs_human_review` 或 `rejected_insufficient_evidence` 草案。
4. 证据不足、单次证据或冲突未解时，降级至人工复核或拒绝。

## 5. Output structure（输出结构）

输出 `candidate_facts`、`classification_keys`、`strategy_card_drafts`、`conflicts`、`evidence_strength`、`evidence_refs` 和 `artifact_refs`。每条候选事实必须可追溯到正式产物。

## 6. Failure fallback（失败降级）

缺少来源、人工复核或冲突信息时不生成可发布建议，输出 `needs_human_review` 或 `rejected_insufficient_evidence` 并列出缺失材料。加载失败时沿用既有离线草案安全门禁并写事件。

## 7. Acceptance criteria（验收标准）

不会将单次事实或无证据总结写入 active；每条事实均包含来源；冲突与低证据建议被明确降级；不会改写正式资产。
