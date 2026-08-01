# 进化样本 JSON 字段：语义经济补充

以下字段由题目生成、候选校验与效果分析阶段新增；历史记录没有这些字段时应按“未评估”读取，不应阻断后续流程。

## `meta_info.question_evolution_metadata`

- `reference_ledgers`：生成前分离的账本。`observable_fact_ledger` 是题面生成可见的可观察事实；`answer_boundary_ledger` 与 `rubric_intent_ledger` 仅供答案、评分、校验和审计使用。
- `generator_visible_context`：实际提供给题面生成器的上下文，只包含原题与 `observable_fact_ledger`。
- `prompt_recipe_version`：使用的算子题面契约版本。
- `balanced_semantic_load`：生成模型对候选之间语义槽位、表面完整度和信息显著性平衡的审计说明；不表示字符数平衡。
- `validation_retry`：同一算子校验重试记录。语义失败的重试最多两次，且只携带结构化失败反馈。
- `retry_exhausted`、`retry_attempts`、`final_failure_reasons`：语义重试耗尽时的审计信息。

## `validation_result`

- `estimated_prompt_chars`、`prompt_char_delta`、`prompt_char_growth_ratio`：字符观察指标。它们不参与 `passed`、重试、候选评分或排序。
- `semantic_economy_mode`：`off`、`shadow` 或 `enforce`。`shadow` 记录风险但不改变 `passed`；`enforce` 才会拒绝语义失败。
- `semantic_economy_evaluated`、`semantic_economy_llm_evaluated`、`semantic_economy_llm_status`：本地和 LLM 判断是否实际完成；缺失或失败不等于低风险。
- `semantic_economy_risk`：语义经济风险等级；`not_evaluated` 表示未执行判断。
- `semantic_redundancy_dominant`：存在可删除的重复事实或无职责段落。
- `shared_context_repeated`：共享背景在版本、场景或段落中重复。
- `answer_hint_expansion`：题面扩写完整答案依据、充分证据或结论总结。
- `surface_leak_risk`、`surface_leak_type`：题面泄漏风险与类型，类型包括 `boundary_language_leak`、`safe_option_leak`、`rubric_axis_leak`、`reasoning_path_leak`。
- `semantic_economy_evidence`：触发检查的题面片段。
- `suggested_same_operator_retry_reason`：保持当前算子时的修正建议，不作为路由或评分依据。

## 实验产物

`analyze_evolution_effect.py --semantic-report-output semantic_economy_report.json` 会按算子汇总语义冗余、公共背景重复、答案提示、题面泄漏类型、人工复核状态和字符分布。字符分布仅作观察，不可用作健康阈值。
