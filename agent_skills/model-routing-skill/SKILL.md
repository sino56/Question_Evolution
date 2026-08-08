# model-routing-skill

## 1. Applicable scenarios（适用场景）

在派生 advisor 时，根据 AdvisorSpec、任务风险、预算、上下文长度和证据切片选择能力层级与 fallback 时使用。不得在 advisor 内硬编码具体模型。

## 2. Input materials（输入材料）

必须读取 AdvisorSpec、任务风险、预算、`evidence_pack_slice` 大小及 hash、是否需要 JSON 和证据引用。不得读取完整父上下文、完整实验目录、完整模型响应或全量 Memory。

## 3. Prohibited actions（禁止事项）

不得在子智能体中硬编码具体模型；不得将策略归纳、冲突审查、评分稳定性或最终合成降级为 `extract_low_cost`；不得以模型选择绕过 policy 或让 advisor 执行正式工具。

## 4. Workflow（工作步骤）

1. 基于 AdvisorSpec 的 model tier 与任务风险确认最低可接受能力。
2. 优先选择同层级配置模型；只在注册的 fallback 层级降级。
3. 对高风险归因、策略和合成任务拒绝低成本摘录层级。
4. 记录所选层级、fallback、理由和路由版本，供后续审计。

## 5. Output structure（输出结构）

输出 `model_tier`、`fallback_tier`、`selection_reason`、`forbidden_downgrade_conditions`、`evidence_pack_slice_hash`、`evidence_refs` 和 `artifact_refs`。输出不应暴露密钥或供应商配置。

## 6. Failure fallback（失败降级）

若没有合规配置模型，使用已有只读本地确定性适配器并记录不可进行模型归纳；高风险任务不得改为低成本摘录模型。加载失败时仍执行代码层的层级拒绝规则。

## 7. Acceptance criteria（验收标准）

搜索和摘录可使用低成本层级；高风险归因、策略和合成不会降到低成本层级；所有选择可在 AdvisorRunRecord 中复盘。
