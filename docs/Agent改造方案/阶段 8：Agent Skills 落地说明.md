# 阶段 8：Agent Skills 落地说明

本说明对应《阶段 8：Agent Skills 制定实施方案》，记录已落地的 Agent 工作规程；它不改变 Question Evolution 主流水线、评分链路、算子 Prompt、Router 或 active Memory 发布门禁。

## 已完成内容

1. 新增 `agent_skills/`，按 P0、P1、P2 共提供 10 个 `SKILL.md`。每个文件均包含适用场景、输入材料、禁止事项、工作步骤、输出结构、失败降级和验收标准。
2. 新增 `agent_runtime/skills/skill_registry.py` 与 `skill_loader.py`。注册表声明阶段、必需输入、允许上下文层、禁止动作和输出 schema；未注册 Skill、全量上下文请求和越权动作会被拒绝。
3. 加载器只允许摘要/引用层：复盘和报告读取 task、Memory 摘要、观察/事件摘要；记忆和策略读取正式 artifact 引用；多 Agent 只读取 advisor 证据切片。请求完整父上下文、完整实验目录、完整模型响应或全量 Memory 会失败。
4. 加载成功与失败分别写入 `skill_loaded`、`skill_load_failed` 事件；失败时降级到既有基础安全规则，不中断正式流水线。
5. 已接入现有运行点：报告、实验后复盘、异常/负收益恢复判断、离线记忆编译、人审预审、Global Judge proposal、Planner、advisor 执行和 advisor 模型路由。
6. 每个 Skill 都有独立输出 schema 与 `agent_skills/examples/` 中的最小输入/期望输出样例，测试会校验其可解析、可审计和符合 schema。

## 新增字段与兼容性

正式 JSONL 流水线字段未修改。Agent 运行目录新增的仅是 `agent_events.jsonl` 中的 Skill 加载事件；Global Judge 报告增加可选 `skill_load` 元数据。缺少这些可选字段的历史 Agent run、实验产物与 Global Judge 报告仍可按原逻辑读取。

## 仍然刻意不做

- 不为 O10–O33 单独创建 Skill；它们仍是业务 Operator。
- 不让 Skill 执行工具、修改 Prompt/Router/Rubric/Operator/评分/状态或发布 active Memory。
- 不用 Skill 代替 schema 校验、Plan Validator、真实评分、发布门禁或人工确认。
- 不引入动态生成、远程安装或跨项目 Skill 市场。

## 验证入口

```powershell
python -m pytest --basetemp .pytest_tmp -q tests\test_agent_skills_p0.py tests\test_agent_skill_registry.py tests\test_agent_skills_p1.py tests\test_agent_skills_p2.py tests\test_agent_skill_examples.py
python -m pytest --basetemp .pytest_tmp -q
```

完整回归需使用项目本地 Python（其中已安装 pytest）。受限环境下，pytest 默认临时目录可能没有读取权限，因此使用工作区内 `--basetemp .pytest_tmp`。
