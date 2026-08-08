# human-review-precheck-skill

## 1. Applicable scenarios（适用场景）

在人类 reviewer 审查候选边界前，整理优先级、可回答性、泄漏和机制命中风险。它是预审，不得替代人工最终确认。

## 2. Input materials（输入材料）

必须读取候选题、父题、参考材料摘要、评分结果、校验结果、机制命中分析和 `artifact_refs`。不得读取完整父上下文、完整实验目录、完整模型回答或全量 Memory。

## 3. Prohibited actions（禁止事项）

不得替代人工确认；不得把预审结果写成已确认有效边界；不得修改候选、评分、校验、状态或 active Memory。

## 4. Workflow（工作步骤）

1. 按证据质量与风险排序候选。
2. 分别标注可回答性、泄漏与目标机制证据。
3. 明确为什么需要人工检查，而不是给出最终通过结论。
4. 证据不足时排在低优先级并标为待复核。

## 5. Output structure（输出结构）

输出 `prioritized_candidates`、`review_reasons`、`answerability_risks`、`leakage_risks`、`mechanism_hits`、`evidence_refs` 和 `artifact_refs`，并标识所有结论为预审。

## 6. Failure fallback（失败降级）

若候选、参考材料或评分证据缺失，输出“材料不足，需人工补充”，不推测最终结论。加载失败时保留人审入口并记录预审不可用。

## 7. Acceptance criteria（验收标准）

reviewer 无需翻阅完整日志即可按清单复核；预审不冒充人工确认；每个排序或风险项有来源；不修改正式资产。
