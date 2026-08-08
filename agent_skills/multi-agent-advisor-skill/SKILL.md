# multi-agent-advisor-skill

## 1. Applicable scenarios（适用场景）

在多智能体协作中，专项 advisor 需要读取裁剪证据包并产生统一 advice 时使用。不得用于直接执行实验或创建新的智能体。

## 2. Input materials（输入材料）

必须读取 `advisor_spec_context`、`evidence_pack_slice`、允许工具、输出 schema 与必要的 `advisor_dynamic_instruction`。不得读取完整父上下文、完整实验目录、完整模型响应或正式写入工具。

## 3. Prohibited actions（禁止事项）

不得启动其它智能体；不得请求真实执行工具；不得写正式实验目录；不得请求完整父上下文或完整实验目录；不得修改 Prompt、Router、Rubric、Operator、评分、状态或 active Memory。

## 4. Workflow（工作步骤）

1. 验证 advisor 来自注册表，输入切片与快照 hash 一致。
2. 仅在声明的工具白名单内阅读证据和生成临时 advice。
3. 为每个 finding 保留 `evidence_refs`；无证据的建议降级为人工复核。
4. 将越权请求显式写入 `forbidden_actions_requested`，不执行也不隐瞒。

## 5. Output structure（输出结构）

输出统一 `advisor_id`、`status`、`summary`、`findings`、`forbidden_actions_requested`、`evidence_refs` 和 `artifact_refs`；所有输出均为 advisory-only。

## 6. Failure fallback（失败降级）

输入切片或 schema 无效时拒绝该 advisor 并记录事件；单个 advisor 失败或超时不得影响主报告和其它 advisor。加载失败时沿用既有注册表、白名单和合并器的基础安全规则。

## 7. Acceptance criteria（验收标准）

越权请求被显式拒绝；无证据建议被降级；输入不包含完整父上下文；输出可由合并器和报告消费但不能执行。
