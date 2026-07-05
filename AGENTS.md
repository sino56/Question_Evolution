# AGENTS.md

## 1. 项目定位

本项目是一个 question evolution 流水线。目标不是把题目机械地写长、写复杂，而是通过多轮“候选探索 -> 真实评分 -> 后验反馈 -> 继续进化”，把原始题目逐步改造成更难、更能暴露弱模型能力边界的题目。

开发时始终避免把项目做成 candidate rejection system。前置 validator 只负责排除致命风险和提供分流信号，候选是否真正有效应优先交给后验真实评分判断。

## 2. 当前已落地主流程

当前推荐入口是已完成准入的 `admitted_seed_samples.jsonl`；`run_loop.sh` 找不到该文件时会回退到 `data/data.jsonl`。

主流程由 `run_loop.sh` 编排：

```text
admitted_seed_samples.jsonl
-> round0_stability_probe.py
-> profile_samples.py
-> select_evolution_candidates.py
-> operator_router.py
-> question_evolution.py
-> validate_evolved_question.py
-> validate_difficulty_gain.py
-> candidate_selection.py
-> collect_answers.py
-> gen_rubric.py
-> scoring.py
-> analyze_evolution_effect.py
-> update_sample_state.py
```

实验输出位于：

```text
experiments/YYYY-MM-DD/exp*/round_N/
```

这些输出是实验产物，不是源码。不要把大型 `experiments/` 结果纳入常规提交。

## 3. 开发原则

优先做增量修改，不重写主流程。

基本原则：

1. 保留现有 pipeline 顺序，除非任务明确要求改调度。
2. 保留已有 JSONL 字段，不随意改名或删除。
3. 新字段尽量作为可选字段添加，并放入已有结构中。
4. 不强制迁移历史数据，旧数据应尽量走 fallback。
5. 不随意修改 `gen_rubric.py`、`scoring.py` 和评分 prompt。
6. 不把 validator 做得过严，避免所有候选都被拒绝。
7. 不让工程复杂度超过 question evolution 本身。
8. 优先用真实评分和 `analyze_evolution_effect.py` 的后验结果判断候选价值。

如果任务基于方案文档或阶段规划开发，必须先读对应方案和已有实现；开发过程中每完成一个功能点都要回到方案自检，发现遗漏或语义不一致时立即修正。

## 4. 候选分流原则

`candidate_selection.py` 当前支持四类分流：

```text
hard_reject
main_chain_candidate
exploration_candidate
pass_through_candidate
```

含义：

* `hard_reject`：存在致命问题，不进入评分。
* `main_chain_candidate`：明确有难度收益，正常进入主链评分。
* `exploration_candidate`：收益不确定但无致命问题，可在预算内进入真实评分。
* `pass_through_candidate`：无探索价值，透传或保留原题。

不要把所有非 `clear_gain / probable_gain` 的候选都直接拒绝。当前实现中：

* `clear_gain`、`probable_gain`、`not_applicable` 可进入主链。
* `weak_gain`、`needs_manual_review` 在无 hard risk 时可进入 exploration。
* `borderline_gain`、`uncertain_gain` 只作为 selection 层兼容 alias，不应在未同步 schema/prompt 前扩展 validator enum。
* `no_gain` 默认透传；只有存在弱探测命中、接近边界分、人工复核建议或表层变化信号时，才可作为 exploration。
* hard label、hard risk tag、模板化简化信号必须覆盖 exploration 逻辑。

每个 candidate group 最多选择 1 个 exploration candidate；每轮 exploration 总预算默认由 `MAX_EXPLORATION_CANDIDATES_PER_ROUND` 或 `--max-exploration-candidates-per-round` 控制。

## 5. score_increased 语义

进化后得分升高不是成功，而是负收益。

如果出现：

```text
score_increased
score_increased_after_evolution = true
```

应理解为：

```text
当前改写让题目更容易了
-> 写入 failure memory
-> 不作为成功终止
-> 不写 operator success memory
-> 下一轮切换 operator 或策略
```

不得将其转成：

```text
validated_high_score_sample
stable_high_score_stop
terminal success
```

## 6. Operator 修改原则

修改 operator prompt 时优先做最小文本替换。

不要默认扩展：

```text
OperatorPromptSpec
implicit_generation_rule
forbidden_surface_patterns
anti_scaffold_check
```

当前重点是减少 O1/O2/O4/O8 的显式脚手架，但不是删除 operator 能力轴。应避免在面向模型生成的问题形态中显式要求：

```text
A/B 二选一
哪一层已成立
形式化排序 / 分层
双门槛 / 动作层 / 性质层标签化作答
```

但要保留题型多样性，例如：

```text
证据关系比较
事实-结论承接
近似层级区分
单变量反事实
事实绑定约束
结论措辞边界
```

## 7. 轻量回溯与当前实现边界

当前代码已经有“链式主流程 + 单轮局部多候选探索 + 候选选优 + memory 收敛”，但尚未接入 `boundary_bank.jsonl`、`fork_tasks.jsonl` 或完整回溯任务队列。

因此常规开发中不要假设以下能力已经存在：

```text
ENABLE_BACKTRACK_FORK
MAX_FORK_TASKS_PER_SAMPLE
MAX_FORK_TASKS_PER_ROUND
boundary_bank.jsonl
fork_tasks.jsonl
merge fork_tasks into round input
```

如果后续任务明确要求实现“轻量回溯式多边界探索”，应按 `docs/5. 轻量回溯式多边界探索优化方案.md` 增量实现：只新增轻量 boundary bank 和 fork task，不引入完整树搜索、深层 parent stack、BFS/DFS/UCB、embedding 去重或 LLM 边界去重。

当前 `effective_boundary_probe` 的已落地语义是：更新 `evolution_state`，并写入 operator memory；不要把尚未实现的 fork/boundary bank 写入完成标准或测试要求。

## 8. 数据字段约束

不要随意删除或重命名以下字段：

```text
sample_id
index
round
prompt
reference_answer
rubric
rubric_thought_process
score_prompt
score_rate
scoring_result
round0_score_trials
round0_score_summary
representative_round0_answer
rubric_item_stability
sample_profile
overscore_diagnosis
evolution_action
operator_route
candidate_group_id
candidate_id
candidate_operator
candidate_generation
validation_result
difficulty_gain_validation
candidate_selection
question_evolved
effect_analysis
evolution_state
failure_memory_candidate
meta_info
```

新增字段应尽量放在已有结构内，例如：

```json
{
  "candidate_selection": {
    "candidate_flow": "exploration_candidate",
    "selected_for_exploration": true
  }
}
```

需要跨阶段稳定消费的进化信息优先放入 `meta_info.question_evolution_metadata`、`candidate_selection`、`effect_analysis` 或 `evolution_state`。注意部分标准闭环脚本会只保留关键顶层字段，所以不要依赖临时顶层字段跨越所有阶段。

## 9. 测试要求

新增行为必须补充小型 pytest 测试。当前优先覆盖：

1. `weak_gain` 无 hard risk 时进入 exploration。
2. `needs_manual_review` 无 hard risk 时进入 exploration。
3. hard risk 仍然 hard reject。
4. 每组最多选择 1 个 exploration candidate。
5. 每轮 exploration 总数受预算限制。
6. `score_increased` 不进入成功终止态，也不写 operator success memory。
7. round0 稳定评分会重写顶层 `score_rate` 和代表性 `scoring_result`。
8. O1/O2/O4/O8 不再显式要求 A/B、层级、排序、双门槛式表层脚手架。

`boundary_bank` 和 `fork_tasks` 相关测试只在真正实现轻量回溯机制时加入，不属于当前已落地功能的完成标准。

运行测试：

```bash
pytest -q
```

## 10. 常用命令

创建环境：

```bash
python -m venv .venv
pip install -r requirements.txt
```

运行预检：

```bash
python check_runtime_environment.py
```

运行完整流程：

```bash
bash run_loop.sh
```

调试单阶段示例：

```bash
python candidate_selection.py --input difficulty_validated_candidates.jsonl --output evolved.jsonl

python analyze_evolution_effect.py --before previous_scored.jsonl --input scored.jsonl --output effect_analysis.jsonl

python update_sample_state.py --input effect_analysis.jsonl --output state_updated.jsonl --memory-dir memory
```

## 11. 安全与配置

不要提交 API key、base URL 或其他密钥。
本地 `config.py` 已被 `.gitignore` 忽略，只能作为本机明文配置文件使用；不要 stage、提交或在日志/回复中引用其内容。

使用环境变量或本地忽略配置文件保存真实配置。分享日志前应隐藏私有 prompt、模型答案、API 报错和密钥信息。

## 12. 提交要求

提交信息使用清晰格式，例如：

```text
feat: add exploration candidate flow
fix: treat score increase as negative gain
test: cover candidate flow split
docs: update AGENTS
```

PR 或变更说明应包含：

1. 修改了哪个阶段；
2. 新增或变更了哪些 JSONL 字段；
3. 是否兼容历史数据；
4. 运行了哪些测试；
5. 是否修改了主流程；
6. 哪些内容被刻意暂缓。

## 13. 最重要的规则

开发时始终记住：

```text
少加规则，多做实验；
少做前置否决，多做后验反馈；
少惩罚整个 operator，多记录具体失败形态；
少追求静态完美，多追求真实 score drop。
```

本项目的核心是 question evolution，不是 candidate rejection。
