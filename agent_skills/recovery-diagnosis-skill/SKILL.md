# recovery-diagnosis-skill

## 1. Applicable scenarios（适用场景）

在运行失败、中断、产物缺失、manifest 异常、`score_increased` 或预算耗尽后，用于判断恢复、重规划、停止报告或人工复核路径。

## 2. Input materials（输入材料）

必须读取 Agent 事件、工具返回结果、checkpoint、manifest、实验目录摘要、终止原因与 `evidence_refs`。只能读取摘要和可定位产物引用，不得读取或更改冻结快照、完整实验目录和完整模型响应。

## 3. Prohibited actions（禁止事项）

不得在 `score_increased` 后以相同条件重复重跑；不得改变已冻结快照；不得跳过 manifest 校验；不得回写评分、状态、Prompt、Router、Operator 或 active Memory。

## 4. Workflow（工作步骤）

1. 先验证 manifest、checkpoint 和终止原因。
2. 将情况分类为 `business_failure`、`system_failure`、`governance_block` 或 `unknown`。
3. 指定 `resume`、`rollback`、`replan`、`stop_and_report` 或 `manual_review_required`，并列出不可重跑步骤。
4. 将 `score_increased` 明确作为负收益，停止同条件重跑并转入人工复核或受约束重规划。

## 5. Output structure（输出结构）

输出 `failure_type`、`recommended_action`、`resume_point`、`must_not_rerun`、`evidence_refs` 和 `artifact_refs`。恢复点必须明确到已发布且通过校验的产物。

## 6. Failure fallback（失败降级）

当 checkpoint 或 manifest 无法验证时，输出 `manual_review_required` 或 `stop_and_report`，而不是猜测恢复点。Skill 加载失败时使用基础保守恢复规则并写入降级事件。

## 7. Acceptance criteria（验收标准）

能说明为什么续跑、回滚或停止；恢复点和不可重跑步骤明确；不会同条件重跑负收益；每项建议都有证据引用。
