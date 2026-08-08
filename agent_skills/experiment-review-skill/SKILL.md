# experiment-review-skill

## 1. Applicable scenarios（适用场景）

在实验结束、只读复盘或需要解释候选、分支、评分变化与失败原因时使用。不得用于启动实验、改写正式产物，或把自动评分直接认定为已确认边界。

## 2. Input materials（输入材料）

必须读取 `task_context`、实验摘要、分支结果、候选统计、效果分析与带定位信息的 `evidence_refs`。可读取局部 Memory 摘要和 Agent 事件摘要；不得读取完整实验日志、完整模型响应或全量 Memory。

## 3. Prohibited actions（禁止事项）

不得修改评分、分支结果、Prompt、Router、Rubric、Operator、状态或 active Memory；不得发布策略；不得把自动评分候选写成“已人工确认的有效边界”。

## 4. Workflow（工作步骤）

1. 先核对实验产物和 manifest 是否可用；缺失或损坏时只报告系统风险。
2. 依据效果分析区分 `score_decreased`、`score_increased`、`no_gain`、`not_applicable` 与 `validation_failed`。
3. 将每项结论绑定到最小必要的证据引用，并把证据不足的判断降级为待人工复核。
4. 区分业务失败与系统失败；只提出只读复盘建议。

## 5. Output structure（输出结构）

输出短小、结构化的对象：`summary`、`outcome_type`、`findings`、`needs_human_review`、`evidence_refs` 与 `artifact_refs`。每条 finding 必须保留证据引用；自动结论必须标识为候选或待复核。

## 6. Failure fallback（失败降级）

当必要产物、证据或上下文摘要缺失时，输出 `status=needs_human_review` 或 `status=rejected_insufficient_evidence`，写明缺失材料，并且不补造结论。Skill 本身加载失败时由基础只读规则继续生成报告并记录事件。

## 7. Acceptance criteria（验收标准）

输出能区分业务与系统失败；每一项正式建议都有 `evidence_refs` 或 `artifact_refs`；报告明确解释人工复核原因；不产生任何正式资产修改。
