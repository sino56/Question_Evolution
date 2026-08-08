# strategy-proposal-skill

## 1. Applicable scenarios（适用场景）

用于 Global Judge 或离线复盘把策略卡草案、冲突审查、Replay/Holdout 和人工复核记录整理为治理建议。不得代替发布门禁或直接改变线上策略。

## 2. Input materials（输入材料）

必须读取策略卡草案、冲突审查、Replay/Holdout 摘要、人工复核记录以及 `evidence_refs`/`artifact_refs`。不得把单次实验的无证据结论或 active Memory 写权限作为输入。

## 3. Prohibited actions（禁止事项）

不得直接发布 active；不得自动修改 Router、Prompt、Rubric、Operator、评分或状态；冲突未解决时不得输出 shadow。

## 4. Workflow（工作步骤）

1. 检查建议状态和证据强度是否一致。
2. 检查所有事实均有来源，且与冲突审查和 Replay/Holdout 一致。
3. 将建议限制为 `proposed`、`shadow`、`needs_human_review` 或 `rejected_insufficient_evidence`。
4. 缺证据或有冲突时降级，不产生执行指令。

## 5. Output structure（输出结构）

输出 `status`、`proposal`、`evidence_strength`、`conflicts`、`verification_plan`、`evidence_refs` 和 `artifact_refs`。输出是 proposal-only，不能包含 active 发布动作。

## 6. Failure fallback（失败降级）

没有证据引用时输出 `rejected_insufficient_evidence` 或 `needs_human_review`；Replay、Holdout 或人工复核缺失时不进入 shadow。加载失败时仍由既有 Global Judge 发布门禁拒绝越权建议。

## 7. Acceptance criteria（验收标准）

建议状态与证据强度匹配；冲突建议进入人工复核；所有建议可追溯；Skill 不具备发布或修改正式资产权限。
