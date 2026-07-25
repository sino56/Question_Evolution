import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from prompts.operators import build_operator_prompt, get_operator_spec
from select_evolution_candidates import (
    EVOLVE_HIGH_SCORE_OVERSCORE,
    PASS_THROUGH_OR_SCORING_NOISE,
    PROBE_MIDDLE_SCORE_BOUNDARY,
    RECONSTRUCT_LOW_SCORE_BOUNDARY,
    STOP_EVOLUTION,
)
from local_api_config import get_config_list, get_config_value
from pipeline_runtime import (
    AtomicJsonlStageWriter,
    StageMetrics,
    TraceStore,
    append_performance_event,
    bounded_async_map,
    iter_json_records,
    load_json_records,
    stable_record_key,
    validate_published_artifact,
)
import validate_evolved_question as validation_stage


# 默认使用与 rubric_evolution 相同的 strong model，可通过 CLI 或环境变量覆盖。
EVOLVE_MODEL = (
    os.getenv("EVOLVE_MODEL")
    or get_config_value("EVOLVE_MODEL", "QA_MODEL", "GPT_MODEL", default="gpt-5.4")
)
EVOLVE_BASE_URL = (
    os.getenv("EVOLVE_BASE_URL")
    or os.getenv("OPENAI_BASE_URL")
    or get_config_value("EVOLVE_BASE_URL", "BASE_URL", "OPENAI_BASE_URL", default="https://hanbbq.labpilot.top/v1")
)

REQUEST_TIMEOUT_SECONDS = 180.0
MAX_OUTPUT_TOKENS = 32768
DEFAULT_MAX_VALIDATION_RETRIES = 1
EVOLUTION_REQUIRED_ACTIONS = {
    EVOLVE_HIGH_SCORE_OVERSCORE,
    RECONSTRUCT_LOW_SCORE_BOUNDARY,
    PROBE_MIDDLE_SCORE_BOUNDARY,
}
NON_EVOLUTION_ACTIONS = {
    PASS_THROUGH_OR_SCORING_NOISE,
    STOP_EVOLUTION,
}
NO_AVAILABLE_OPERATOR_STATUS = "no_available_operator"
GENERATION_FAILED_STATUS = "generation_failed_pass_through"


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def parse_api_keys(cli_keys: Optional[List[str]] = None) -> List[str]:
    if cli_keys:
        keys = [key.strip() for key in cli_keys if key and key.strip()]
        if keys:
            return keys
    raw = os.getenv("EVOLVE_API_KEYS") or os.getenv("OPENAI_API_KEYS") or os.getenv("OPENAI_API_KEY") or ""
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    if keys:
        return keys
    return get_config_list(
        "EVOLVE_API_KEYS",
        "GPT_API_KEYS",
        "HIAPI_KEYS_BIG",
        "OPENAI_API_KEYS",
        "OPENAI_API_KEY",
        "API_KEYS",
    )


def append_validation_retry_instruction(user_prompt: str, reject_reason: Optional[str]) -> str:
    reason = str(reject_reason or "").strip()
    if not reason:
        return user_prompt
    return (
        user_prompt.rstrip()
        + "\n\n# 上一轮候选题未通过独立复杂度/可回答性校验\n"
        + f"reject_reason: {reason}\n"
        + "请继续使用同一个 operator，不要更换题型主轴；只修正上述问题后重新生成。"
        + "新题必须可回答、单主轴、不过度依赖格式复杂度，也不得引入题干外知识。\n"
    )

#########################################################
'''
V1版本的原始prompt：
"""
# 角色
你是一位专门设计大模型评测题目的专家。当前题目对模型的区分度不足：较强模型（参考答案）和较弱模型（候选答案）的得分都过高。你的任务是把原题升级为"更难版本"，使其能更有效地区分模型的真实能力。

# 目标
分析原题、参考答案和候选答案。候选答案虽然得分高，但可能存在：泛泛而谈、缺少深度推理、遗漏边界条件、未能紧扣核心逻辑等问题。你需要生成一个新题目，使得：
1. 参考答案中的核心知识和推理链条仍然适用，或只需合理扩展即可回答新题；
2. 候选答案中的 superficial / 模板化 / 泛化 / 堆叠术语式的回答不再能轻易得高分；
3. 新题能迫使回答展现以下至少一种能力：因果链推理、边界意识、反事实分析、多条件综合判断、精确操作化、或对干扰信息的甄别。

# 可选进化策略（根据题目特点选择最合适的 1-3 种，不要全部堆砌）
1. **增加约束条件**：要求回答必须基于特定视角、法律条款、技术约束、场景限制或证据条件。
2. **要求显式推理**：不要只问"是什么"，而是问"为什么""如何排除其他可能""在什么条件下结论不成立"。
3. **引入边界或反事实**：改变原题中的某个条件，问结论会如何变化，或要求指出适用边界与例外。
4. **提升综合复杂度**：把两个相关子问题合并，要求比较、权衡、排序或推导优先级。
5. **抑制泛化回答**：要求回答必须紧扣本题具体情境，避免通用套话；或限制回答长度，要求"用最精炼的语言给出最关键的两点"。

# 原则
1. **可回答性**：新题必须仍能被强模型基于参考答案合理回答，不能引入需要外部未提供知识的隐藏条件。
2. **区分度**：新题应让"覆盖关键词但缺乏深度"的回答明显失分，让紧扣 reference 主线的回答得高分。
3. **不过拟合**：不要为了刁难某个候选答案而设置极窄陷阱；进化应提升题目本身质量，而不是针对特定措辞找茬。
4. **语言一致**：新题语言必须与原题一致。
5. **保持核心主题**：不要改变题目的领域和核心事实，只在深度、约束、推理要求上升级。

# 输入
## 原题
{|prompt|}

## 参考答案
{|response1|}

## 候选答案（当前得分过高，需要题目升级以区分强弱模型）
{|response2|}

## 现有评分标准
{|rubrics|}

# 输出
返回合法 JSON 对象，不要输出 Markdown 标记或额外解释：
```json
{
  "evolved_prompt": "升级后的新题目。必须是一个完整、可独立作答的问题。",
  "evolution_strategy": "说明采用了哪些策略（如'增加约束/要求反事实推理/要求比较权衡'），以及为什么这些策略能提升区分度",
  "notes_for_reference": "如果参考答案需要补充或调整才能完美回答新题，请简要说明；如果基本适用则写'基本适用'"
}
```
"""
'''
########################################################

QUESTION_EVOLUTION_PROMPT_TEMPLATE_V1 = """
# 角色
你是一位负责“当前弱模型判断弱点发现”的评测题目设计专家。
你的任务不是把题目机械改难，也不是把题目改成长、多步骤、强格式的结构题，而是要把原题升级成一道：
1. 单主轴；
2. 可回答；
3. 可稳定评分；
4. 能让当前弱模型在一个可归因的关键判断点上暴露错误；
5. 能让强模型凭借更精确地区分相近业务表述、处置结论或评价保留范围而答对
的新题。
当前阶段的重点不是复用旧题型模板，而是发现当前弱模型的新 failure modes。
# 背景判断
已有实验显示，当前弱模型已经明显改善了以下旧弱点：
1. 普通两层拆分；
2. 显式“哪一层成立 / 哪一层不成立”判断；
3. 显式“最小缺口是什么”追问；
4. 常规“线索不能直接推出结论”的判断；
5. 明确提示“不要展开题外流程 / 不要补充题外事实”后的事实绑定。
因此，除非原题确实只能这样进化，否则不要优先生成这些旧式题型。
# 核心目标
请分析原题、参考答案、候选答案和现有评分标准，生成一道升级后的新题，使其满足：
1. 仍然考查原题的核心领域、核心事实和核心能力，不改变题目主题；
2. 新题的主要失分点集中在 1 个关键判断上，而不是因为任务太多、格式太复杂、篇幅太长而失分；
3. 新题能压测当前弱模型仍可能不稳的具体判断点；
4. 后续 judge 能根据 rubric 稳定判断对错，而不是靠术语密度、结构完整性或格式服从度打分；
5. 新题不能因为把难点讲得过清楚而让弱模型顺着题面复述拿高分。
# 第零步：先判断旧题型是否已经不够
在内部先做判断，但不要输出完整中间推理。
请先判断候选答案是否已经基本能处理以下旧式问题：
1. 能否承认“当前事实还不足”；
2. 能否说出大致还缺关键事实；
3. 能否区分线索、异常、嫌疑与最终结论；
4. 能否在题面明确提示时遵守“只根据题干事实”；
5. 能否复述“第一层成立、第二层未成立”的结构。
如果候选答案已经能处理上述旧式问题，本轮不得继续生成普通两层拆分题、显式最小缺口题或显式线索/结论区分题，必须切换到新的题面竞争点。
# 第一步：选择当前最值得压测的题面竞争点
只能选择 1 个主竞争点作为本题主轴。算子类型只用于内部选题和 metadata，不得把算子定义、解题标签或评分关注点搬进 evolved_prompt。
优先把题面写成“业务判断竞争”，而不是“材料分类题”。推荐题面形态按优先级为：
1. 在两个或三个报告表述之间选择更稳妥的一项；
2. 判断新增一个事实后原评价是否仍可保留；
3. 判断一个处置结论是否仍可执行、哪种表述需要降级；
4. 只在原题确实需要时才比较候选事实，但不得要求作答者给证据分层。
各算子的成题规则如下：
- O10：只能生成同主体、同场景、同目标结论的近似判断竞争；不得生成材料分层题或让作答者比较材料强弱。
- O11：只给不可见区间的端点事实、时间事实、进入前动作和未出现事实；任务只能比较处置或报告表述，不得直接问能否证明不可见区间内发生了什么。
- O12：只给一个强线索和两个业务判断竞争；不得明说还缺哪类统计、记录、阈值、条件清单或待补条件。
- O13：先给原评价，再问新增事实后原评价是否仍可保留；不得问“哪个最会迫使下调 / 哪个直接推翻”。
- O14：只比较两个看似都来自题干的业务表述；不得要求识别抽象推理标签。
- O15：只改变一个核心事实，并明说其他关键事实保持不变；任务是判断原风险判断哪部分保留、哪部分降级或撤回。
- O16：只新增一个相近正常解释；正确竞争点必须是“异常强度下降但风险未必消失”，不得把题面写成正常解释是否排除风险。
- O17：只比较两个报告或处置表述；不得使用“应急核查 / 事实定性 / 处置门槛 / 事实门槛”等标签。
- O18：只给两个同域统计或基线来源；不得明说“样本口径错配 / 基准范围不一致”，让模型通过业务表述竞争自行识别。
- O9+：只在原题确有反常事实时使用；不得要求模型列主线切换步骤，只能让它比较哪种研判方向或报告表述更稳妥。
# 第二步：旧题型降权规则
以下题型只能在确有必要时使用，不能作为默认选择：
1. 普通两层拆分：
“哪一层已经成立，哪一层还不成立？”
2. 显式最小缺口：
“还缺的最小关键事实是什么？”
3. 常规线索/结论区分：
“该事实最多只能说明异常，不能直接推出结论。”
4. 显式题外补设识别：
“哪一步引入了题干外前提？”
如果必须使用上述题型，必须进一步改造成一个业务判断竞争：
- 多个近似报告表述竞争；
- 两个看似合理的处置结论竞争；
- 一个单变量事实改变原评价保留范围；
- 一个新增事实改变原评价是否保留；
- 一个看似题干内的表述实际需要题面没有给出的连接；
- 一个反常事实导致研判方向表述发生变化。
# 第三步：避免“讲清楚反而变简单”
生成新题前必须逐项自检；任一项失败都必须重写 evolved_prompt：
1. 新题是否只是“第一层成立、第二层不成立、还缺什么”的换壳？
如果是，且候选答案已经能处理这一类题，必须重做。
2. 新题是否把真正难点提前说破？
如果弱模型只要复述题面中的“缺某阈值 / 缺某记录 / 不能定性 / 需要人工确认 / 还不能推出”等句式就能高分，必须重做。
3. 新题是否缺少足够接近的竞争判断？
如果没有至少两个看似都有道理但实际层级不同的候选判断，必须重做。
4. 新题是否只是更清楚、更聚焦，但没有改变竞争点？
如果是，这种改写很可能会让题更容易，不应采用。
5. 新题是否主要依靠复杂格式压分？
如果是，必须重做。
6. 正确项是否独占同主体、同地点、同时间尺度、同判断对象四个优势？
如果是，说明干扰项只是陪跑项，必须重做。
7. 新题是否在问材料分类，而不是问业务判断？
如果作答任务要求比较材料强弱、材料用途或材料类别，必须改写成报告表述、处置结论或原评价保留判断。
# evolved_prompt 题面泄漏限制
以下约束只针对 evolved_prompt。边界类型、评分关注点和答案标签只能写入 metadata 字段，不能写进题面。
evolved_prompt 禁止出现以下黑名单内容。黑名单例句只能用于本段自检，不得复制到 evolved_prompt：
1. operator 名称、O10/O11/O12/O13/O14/O15/O16/O17/O18/O9+、算子能力名称；
2. 答案标签，例如“直接支撑 / 补强线索 / 更低层结论 / 方向错误 / 共同必要条件 / 信息闭包 / 题外前提 / 最小缺口 / 已支持层 / 未支持层”等；
3. 同义泄漏句式，例如“尚未建立统计阈值 / 没有均值 / 没有标准差 / 缺正常分布 / 缺分级阈值 / 缺基准样本 / 直接改变支撑基础 / 比较作用边界 / 足以推出 / 最能支撑 / 支撑强弱 / 证据层级 / 还不能定性 / 需要人工确认”；
4. 会直接提示正确解法的完整推理路径，例如先把事实分层，再判断哪层成立，再说缺哪一条；
5. 显式提醒“不要补充题外事实”“只能根据题干闭包”等会让弱模型靠规则复述得分的提示。
只有当上述词句是原题中不可删除的核心事实原文时，才可以保留；否则必须换成具体场景事实或具体报告表述。
evolved_prompt 应该只给：
1. 原题相关的事实；
2. 候选判断；
3. 最小反事实；
4. 一个需要作答者自行完成边界判断的问题。
如果生成出的题面必须依靠答案标签才能成立，说明该算子不适合该样本，应换用更隐性的竞争判断。
# 竞争项接近度硬规则
凡是出现候选判断、候选表述或候选事实，必须满足：
1. 候选项共享同一主体、同一场景、同一判断目标；
2. 候选项不得让正确项独占同地点、同时间尺度、同判断对象、同证据来源；
3. 每个干扰项都必须影响同一个核心判断，只能差在一个微小变量上；
4. 干扰项不能只是背景事实、反向事实、外围主体或无关时间段；
5. 如果无法构造足够接近的候选项，改成两个业务表述竞争，不要生成三选项题。
# 最小变量变化硬规则
本轮新题最多允许 1 个核心变量改变结论层级。若使用新增事实或反事实：
1. 必须在题面写明其他关键事实保持不变；
2. 新增事实只能有 1 条能改变判断层级；
3. 不得让多个新增事实共同改变结论；
4. 不得同时改变主体、地点、时间尺度和判断目标；
5. 如果一个变量只能降低异常强度，不能把题目写成风险直接消失。
# 第四步：发现模式复杂度预算
本 prompt 用于发现当前弱模型的新失分点，因此允许 operator-specific 的局部复杂度放宽，但必须保持单主轴和稳定评分。
通用预算：
1. 新题必须只有 1 个清晰主轴；
2. 新增事实或场景条件通常不超过 4 条；
3. 输出任务通常不超过 2 个；O15 也应优先压缩为“变化后是否升格/降级 + 原判断哪一处需调整”；
4. 不得要求大表格、多层标签体系、固定句数、复杂编号系统；
5. 不得把难度主要建立在长篇格式、繁琐约束、复杂任务编排上；
6. 题目必须完整、可独立作答，不能依赖题外专业知识。
operator-specific 预算：
1. O10：候选项最多 3 个，必须同主体、同场景、同目标，不能做显式分类表或材料排序题；
2. O11：可允许较长题干，但必须只围绕一个不可见状态门槛，任务必须是处置或报告表述竞争；
3. O12：只允许两个业务判断竞争，不得列条件清单、统计缺口或待补门槛；
4. O13：候选事实最多 3 个，全部影响同一原评价，不能强弱悬殊；
5. O14：不得直接提示抽象推理标签，必须通过两个业务表述比较体现；
6. O15：只设置 1 个核心反事实，并写明其他关键事实保持不变；
7. O16：只新增 1 个正常化事实，考察异常强度下调而非风险消失；
8. O17：只比较报告或处置表述，不能出现处置/事实门槛标签；
9. O18：只给同域基线或统计来源，不能明说口径错配。
# 第五步：可回答性与评分稳定性检查
1. 题干必须提供完成任务所需事实，不得要求题外知识才能回答。
2. 新题应让弱模型主要在 1 个核心错误上失分，而不是在多个任务点上同时失分。
3. 不得设置只能靠猜、靠经验、靠题外法律评价才能作答的陷阱。
4. 如果参考答案需要大量新增知识才能回答，说明改写失败。
5. 如果 judge 无法根据 rubric 稳定判断对错，说明改写失败。
6. 如果强模型也很可能因为题面歧义而答错，说明改写失败。
# 第六步：强弱模型差距预测
输出前必须判断：
1. 当前弱模型最可能错在哪里；
2. 这个错误是否不同于旧式“普通两层拆分 / 显式最小缺口”；
3. 强模型为什么有机会答对；
4. judge 应该抓住哪些具体点评分；
5. 该题是否适合进入 replay-based selection。
# 禁止的进化方式
1. 禁止把难度主要建立在题目更长、任务更多、格式更复杂上。
2. 禁止连续多轮只复用“最小关键事实 / 最小前提 / 最小跳步 / 哪一层成立”这一类旧题型。
3. 禁止把开放题统一改写成边界题后就停住，不再继续追问真正决定胜负的差异。
4. 禁止让题目主要考“遵循复杂指令”，而不是考原题对应的专业判断能力。
5. 禁止在题面直接暴露答案模板，让弱模型靠复述题面得分。
6. 禁止靠题外知识、冷门知识、主观评价或不可验证猜测制造难度。
7. 禁止在 evolved_prompt 中出现 operator 名称、边界类型名或评分标签；这些只能出现在 JSON metadata 字段。
8. 禁止把 evaluation_focus、target_current_qwen_failure 或 scoring_alignment 中的判断点原样搬进题面。
# 输入
## 原题
{|prompt|}

## 参考答案
{|response1|}

## 候选答案
{|response2|}

## 现有评分标准
{|rubrics|}

# 输出要求
返回合法 JSON 对象，不要输出 Markdown 标记或额外解释。
必须包含以下字段：
```json
{
  "evolved_prompt": "升级后的新题目。必须完整、可独立作答、聚焦一个主轴，并遵守发现模式复杂度预算。",
  "evolution_strategy": "说明本轮选择了哪一个算子和题面竞争形式、为什么没有继续沿用旧式两层拆分或显式最小缺口问法，并说明题面如何避免泄漏答案标签。",
  "target_current_qwen_failure": "预测当前弱模型最可能错在哪里，必须具体到一个可评分的判断点。",
  "why_old_operator_not_enough": "说明为什么普通两层拆分、显式最小缺口、常规线索/结论区分等旧题型不足以继续压测本题。",
  "new_boundary_type": "O10_evidence_sufficiency_ladder / O11_unobserved_state_attribution / O12_conjunctive_necessity / O13_minimal_disqualifier / O14_information_closure / O15_counterfactual_threshold_shift / O16_close_alternative_normalization / O17_action_vs_fact_threshold / O18_baseline_scope_mismatch / O9_abnormal_mainline_switch 之一。",
  "expected_gpt_advantage": "说明强模型为什么应该能答对，优势来自对相近表述、事实变化和评价保留范围的精确区分，而不是题外知识。",
  "evaluation_focus": [
    "后续评分时最该检查的错误1",
    "后续评分时最该检查的错误2",
    "后续评分时最该检查的错误3"
  ],
  "complexity_budget": {
    "main_axis": "一句话说明本题核心考什么",
    "chosen_boundary_type": "O10/O11/O12/O13/O14/O15/O16/O17/O18/O9+ 之一",
    "target_subclaim_or_threshold": "本轮具体压测哪一个业务判断、报告表述、处置结论或评价保留范围",
    "new_facts_count": 0,
    "output_tasks_count": 0,
    "candidate_options_count": 0,
    "counterfactual_count": 0,
    "estimated_prompt_chars": 0,
    "operator_specific_budget_used": "说明是否使用了 operator-specific 放宽，以及为什么仍然不是复杂度堆叠",
    "clarity_trap_checked": true,
    "why_within_budget": "说明为什么没有靠加长、加任务、加格式制造难度，也没有把题改得更清楚却更容易答"
  },
  "scoring_alignment": {
    "rubric_should_reward": [
      "应该得分的关键判断1",
      "应该得分的关键判断2"
    ],
    "rubric_should_penalize": [
      "应该扣分的典型错误1",
      "应该扣分的典型错误2"
    ],
    "is_replay_selection_ready": true
  },
  "prompt_leakage_check": {
    "contains_operator_name_or_boundary_label": false,
    "contains_answer_label": false,
    "contains_forbidden_threshold_paraphrase": false,
    "contains_full_reasoning_path": false,
    "why_not_hint_leakage": "说明题面为什么只给事实和竞争判断，没有把答案标签、同义门槛泄漏句式或评分关注点写给模型"
  },
  "competition_closeness_check": {
    "same_subject": true,
    "same_scene": true,
    "same_target_judgment": true,
    "correct_option_does_not_monopolize_core_match": true,
    "one_micro_variable_changes_level": true,
    "why_distractors_are_not_runners_up_only": "说明干扰项为什么不是背景、外围、反向或无关事实"
  },
  "notes_for_reference": "如果参考答案需要补充或调整才能完美回答新题，请简要说明；如果基本适用则写'基本适用'"
}
```
# 最终质量自检
输出前逐项确认：
1. 新题不是简单加长版；
2. 新题只有 1 个主轴；
3. 新题没有继续复用已失效的旧式边界题模板；
4. 新题至少包含两个足够接近但实际层级、方向、闭包状态或门槛不同的竞争判断；
5. 正确项没有独占同主体、同地点、同时间尺度、同判断对象；
6. 题面没有黑名单中的显性缺口、材料强弱或结论推出句式；
7. 新题问的是报告表述、处置结论或原评价保留，不是材料分类；
8. 新题不会因为“更清楚”而让候选答案更容易顺着答；
9. 新题的主要失分点能被 rubric 稳定捕捉；
10. 新题能服务于 replay-based selection，即可以比较当前弱模型得分、强模型得分和二者 gap。
""".strip()


QUESTION_EVOLUTION_PROMPT_TEMPLATE_V2 = """
# 角色
你是一位负责"题目难度升级与反模板化"的评测专家。当前题目的瓶颈是：候选答案（通常来自较小模型）靠泛化扩写、专业术语堆砌或通用流程覆盖拿到了高分，但实际质量明显弱于参考答案。你需要把原题改写成一道能压制这类"虚高回答"的升级题。

# 目标
生成的新题必须同时满足：
1. 参考答案中的核心判断和关键依据仍然可以直接使用，或经过简单延伸即可回答；
2. 候选答案那种"看起来很长、很专业，但缺乏对本题具体因果链的聚焦"的回答应失分；
3. 新题能迫使模型给出：紧扣题意的因果链、具体情境下的边界判断、对干扰项的排除、或受约束的精确结论。

# 推荐升级方向（择其适用者，不要全部使用）
1. **要求说明"为什么不是其他选项/其他可能"**：迫使模型给出排除性推理，而不是罗列知识点。
2. **加入具体但合理的限制**：如"假设只能使用视频中出现的证据""不考虑额外的技术鉴定""在资源受限的情况下"，测试模型能否在约束下聚焦。
3. **把开放性问题改为带条件的判断**：如"如果嫌疑人声称X，该如何根据现有证据反驳/支持？"要求模型把证据和结论绑定。
4. **要求给出最小充分条件**：如"要证明该结论，最关键的两条证据是什么？"抑制泛泛而谈。
5. **要求识别题目中的误导或冗余信息**：如"上述情境中哪些信息对判断没有实质帮助？"测试模型是否能聚焦主线。

# 约束
1. 不得改变原题的核心事实、领域和基本案情。
2. 不得引入需要外部未提供知识才能回答的条件。
3. 新题语言与原题一致。
4. 不要设置只能靠猜的陷阱；升级应体现在推理深度和聚焦度上。

# 输入
## 原题
{|prompt|}

## 参考答案
{|response1|}

## 候选答案（当前得分虚高，需要题目升级以压制泛化回答）
{|response2|}

## 现有评分标准
{|rubrics|}

# 输出
返回合法 JSON 对象，不要输出 Markdown 标记或额外解释：
```json
{
  "evolved_prompt": "升级后的新题目。必须是一个完整、可独立作答的问题。",
  "evolution_strategy": "说明采用了哪些策略，以及为什么能压制候选答案的虚高并拉开模型差距",
  "notes_for_reference": "如果参考答案需要补充或调整才能完美回答新题，请简要说明；如果基本适用则写'基本适用'"
}
```
""".strip()


def get_evolution_prompt_template(version: str) -> str:
    version = (version or "").strip().lower()
    if version in {"v1", "baseline", "default", ""}:
        return QUESTION_EVOLUTION_PROMPT_TEMPLATE_V1
    if version in {"v2", "anti-verbosity", "focused"}:
        return QUESTION_EVOLUTION_PROMPT_TEMPLATE_V2
    raise ValueError(f"不支持的 question evolution prompt 版本: {version}")


def extract_answer(resp) -> str:
    choices = getattr(resp, "choices", None)
    if choices:
        first_choice = choices[0]
        message = getattr(first_choice, "message", None)
        return (getattr(message, "content", "") or "").strip()

    if hasattr(resp, "model_dump"):
        payload = resp.model_dump()
        choices = payload.get("choices")
        if choices:
            message = choices[0].get("message", {})
            return (message.get("content", "") or "").strip()

    if isinstance(resp, str):
        payload = resp
        if payload.startswith("data:"):
            payload = payload[len("data:"):].strip()
        parsed = json.loads(payload)
        return (parsed["choices"][0]["message"]["content"] or "").strip()

    raise TypeError(f"Unsupported or empty response type: {type(resp)}")


class RotatingAPIClient:
    """支持自动切换 API Key 的 OpenAI 兼容客户端包装器。"""

    def __init__(self, base_url: str, api_keys: List[str], request_timeout: float = REQUEST_TIMEOUT_SECONDS):
        if not api_keys:
            raise ValueError("api_keys 不能为空")
        self.base_url = base_url
        self.api_keys = api_keys
        self.request_timeout = request_timeout
        self.current_key_index = 0
        self.client: Optional[Any] = None
        self._lock = asyncio.Lock()
        self._init_client()

    def _init_client(self):
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise RuntimeError("Missing dependency: install openai to run question_evolution.py.") from exc

        current_key = self.api_keys[self.current_key_index]
        kwargs = {
            "api_key": current_key,
            "timeout": self.request_timeout,
        }
        if self.base_url:
            kwargs["base_url"] = self.base_url
        self.client = AsyncOpenAI(**kwargs)
        logger.info(
            f"使用 question evolution API Key [{self.current_key_index + 1}/{len(self.api_keys)}]: "
            f"{current_key[:8]}..."
        )

    async def close(self):
        if self.client:
            await self.client.close()

    def _is_token_exhausted_error(self, error: Exception) -> bool:
        error_str = str(error)
        return (
            "401" in error_str and
            ("TokenStatusExhausted" in error_str or "令牌额度已用尽" in error_str)
        )

    async def switch_to_next_key(self) -> bool:
        async with self._lock:
            self.current_key_index += 1
            if self.current_key_index >= len(self.api_keys):
                logger.error("所有 question evolution API Key 额度已用尽")
                return False
            if self.client is not None:
                await self.client.close()
            self._init_client()
            return True

    async def chat_completions_create(self, **kwargs):
        for _ in range(len(self.api_keys)):
            try:
                return await self.client.chat.completions.create(**kwargs)
            except Exception as e:
                if self._is_token_exhausted_error(e):
                    logger.warning(f"question evolution API Key [{self.current_key_index + 1}] 额度用尽: {str(e)[:100]}")
                    if await self.switch_to_next_key():
                        continue
                    raise Exception("所有 question evolution API Key 额度已用尽") from e
                raise
        raise Exception("所有 question evolution API Key 额度已用尽")


def collect_json_candidate_texts(response_text: str) -> List[str]:
    text = response_text if isinstance(response_text, str) else str(response_text)
    stripped = text.strip()
    candidates: List[str] = []
    seen = set()

    def add(candidate: str) -> None:
        candidate = candidate.strip()
        if candidate and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    if not stripped:
        return candidates

    code_fence_pattern = re.compile(r"```(?:json)?\s*([\s\S]+?)\s*```", re.IGNORECASE)
    for match in reversed(list(code_fence_pattern.finditer(stripped))):
        add(match.group(1))

    outside_text = re.sub(r"```[\s\S]+?```", "\n", stripped).strip()
    if outside_text and outside_text != stripped:
        add(outside_text)

    object_start, object_end = stripped.find("{"), stripped.rfind("}")
    if object_start != -1 and object_end != -1 and object_end > object_start:
        add(stripped[object_start:object_end + 1])

    if not candidates:
        add(stripped)

    return candidates


def loads_json_with_repair(json_str: str) -> Any:
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        repaired = re.sub(r",\s*([\]}])", r"\1", json_str.strip())
        decoder = json.JSONDecoder()
        try:
            obj, _ = decoder.raw_decode(repaired.lstrip())
            return obj
        except Exception:
            object_start, object_end = repaired.find("{"), repaired.rfind("}")
            if object_start != -1 and object_end != -1 and object_end > object_start:
                return json.loads(repaired[object_start:object_end + 1])
            raise


def parse_evolution_response(response_text: str) -> Dict[str, Any]:
    last_error: Optional[Exception] = None
    for candidate in collect_json_candidate_texts(response_text):
        try:
            parsed = loads_json_with_repair(candidate)
            if not isinstance(parsed, dict):
                raise ValueError("evolution 响应必须是 JSON 对象")
            if "evolved_prompt" not in parsed:
                raise ValueError("evolution 响应缺少 evolved_prompt 字段")
            evolved_prompt = str(parsed["evolved_prompt"]).strip()
            if not evolved_prompt:
                raise ValueError("evolved_prompt 不能为空")
            parsed["evolved_prompt"] = evolved_prompt
            parsed["evolution_strategy"] = str(parsed.get("evolution_strategy", "")).strip()
            parsed["notes_for_reference"] = str(parsed.get("notes_for_reference", "")).strip()
            return parsed
        except Exception as e:
            last_error = e
    raise ValueError(f"无法解析有效 question evolution JSON: {last_error}")


def validate_evolved_question(original_prompt: str, evolved_prompt: str) -> None:
    if not evolved_prompt or not evolved_prompt.strip():
        raise ValueError("进化后的问题不能为空")
    if evolved_prompt.strip() == original_prompt.strip():
        raise ValueError("进化后的问题与原题完全相同")
    if len(evolved_prompt) < 0.5 * len(original_prompt):
        raise ValueError("进化后的问题明显短于原题，疑似丢失信息")


def load_json_or_jsonl(input_path: str) -> List[Dict[str, Any]]:
    return load_json_records(input_path, stage="question_evolution")


def get_item_key(item: Dict[str, Any]) -> str:
    return stable_record_key(item)


def load_processed_keys(output_path: str) -> set:
    processed_keys = set()
    if not os.path.exists(output_path):
        return processed_keys

    try:
        with open(output_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                processed_keys.add(get_item_key(item))
        logger.info(f"从输出文件加载了 {len(processed_keys)} 条已输出记录")
    except Exception as e:
        logger.warning(f"读取已有输出文件失败: {e}，将从头开始处理")

    return processed_keys


def _coerce_score_rate(value: Any) -> Optional[float]:
    try:
        score_rate = float(value)
    except (TypeError, ValueError):
        return None
    if 0 <= score_rate <= 1:
        return score_rate
    return None


def get_score_rate(item: Dict[str, Any]) -> Optional[float]:
    top_level_score_rate = _coerce_score_rate(item.get("score_rate"))
    if top_level_score_rate is not None:
        return top_level_score_rate

    scoring_result = item.get("scoring_result")
    if not isinstance(scoring_result, dict):
        return None

    try:
        awarded = float(scoring_result.get("total_awarded", 0) or 0)
        possible = float(scoring_result.get("total_possible", 0) or 0)
    except Exception:
        return None

    if possible <= 0:
        return None
    return awarded / possible


def _normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def get_evolution_action(item: Dict[str, Any]) -> str:
    return str(item.get("evolution_action", "") or "").strip()


def uses_stage_action_contract(item: Dict[str, Any]) -> bool:
    return bool(get_evolution_action(item))


def action_requires_evolution(item: Dict[str, Any]) -> bool:
    return get_evolution_action(item) in EVOLUTION_REQUIRED_ACTIONS


def get_operator_route(item: Dict[str, Any]) -> Dict[str, Any]:
    route = item.get("operator_route")
    return route if isinstance(route, dict) else {}


def get_evolution_state(item: Dict[str, Any]) -> Dict[str, Any]:
    state = item.get("evolution_state")
    return state if isinstance(state, dict) else {}


def get_round0_recommended_budget(item: Dict[str, Any]) -> Optional[int]:
    route = item.get("operator_route")
    if isinstance(route, dict):
        try:
            route_budget = int(route.get("recommended_num_candidates"))
        except (TypeError, ValueError):
            route_budget = None
        if route_budget is not None:
            return max(0, route_budget)

    evolution_budget = item.get("evolution_budget")
    if isinstance(evolution_budget, dict):
        try:
            budget = int(evolution_budget.get("recommended_num_candidates"))
        except (TypeError, ValueError):
            budget = None
        if budget is not None:
            return max(0, budget)

    summary = item.get("round0_score_summary")
    if not isinstance(summary, dict):
        return None
    try:
        budget = int(summary.get("recommended_evolution_budget"))
    except (TypeError, ValueError):
        return None
    return max(0, budget)


def get_sample_profile(item: Dict[str, Any]) -> Dict[str, Any]:
    profile = item.get("sample_profile")
    return profile if isinstance(profile, dict) else {}


def get_overscore_diagnosis(item: Dict[str, Any]) -> Dict[str, Any]:
    diagnosis = item.get("overscore_diagnosis")
    return diagnosis if isinstance(diagnosis, dict) else {}


def resolve_operator_id(item: Dict[str, Any]) -> str:
    action = get_evolution_action(item)
    if action not in EVOLUTION_REQUIRED_ACTIONS:
        raise ValueError(f"evolution_action={action or '<missing>'} does not require operator evolution")

    route = get_operator_route(item)
    if not route:
        raise ValueError("缺少 operator_route；请先运行 operator_router.py")

    operator_id = str(route.get("primary_operator") or "").strip()
    if not operator_id:
        raise ValueError("operator_route.primary_operator 不能为空")

    get_operator_spec(operator_id)
    return operator_id


def get_candidate_group_id(item: Dict[str, Any]) -> str:
    for field in ("sample_id", "index"):
        value = item.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    prompt = str(item.get("prompt", "") or "")
    digest = hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
    return f"prompt_{digest}"


def resolve_candidate_operator_ids(item: Dict[str, Any], max_candidates: int) -> List[str]:
    if max_candidates < 1:
        raise ValueError("--num-candidates 必须大于等于 1")
    if not uses_stage_action_contract(item):
        return [resolve_operator_id(item)] if should_evolve(item, 0) else []
    if not action_requires_evolution(item):
        return []

    route = get_operator_route(item)
    if not route:
        raise ValueError("缺少 operator_route；请先运行 operator_router.py")

    avoid = {
        str(operator).strip()
        for operator in route.get("avoid_operators", [])
        if isinstance(operator, str) and operator.strip()
    }
    candidates: List[str] = []
    for operator_id in [route.get("primary_operator")] + list(route.get("backup_operators", [])):
        if not isinstance(operator_id, str):
            continue
        operator_id = operator_id.strip()
        if not operator_id or operator_id in avoid or operator_id in candidates:
            continue
        get_operator_spec(operator_id)
        candidates.append(operator_id)
        if len(candidates) >= max_candidates:
            break

    if not candidates:
        raise ValueError("operator_route 未提供可用候选算子")
    return candidates


def classify_generation_failure(error: Exception) -> str:
    error_text = str(error)
    no_operator_markers = (
        "operator_route",
        "primary_operator",
        "可用候选算子",
        "does not require operator evolution",
    )
    if any(marker in error_text for marker in no_operator_markers):
        return NO_AVAILABLE_OPERATOR_STATUS
    return GENERATION_FAILED_STATUS


def make_passthrough_record(
    item: Dict[str, Any],
    *,
    generation_status: Optional[str] = None,
    failure_reason: Optional[str] = None,
) -> Dict[str, Any]:
    result = dict(item)
    result["question_evolved"] = False
    if generation_status:
        result["question_evolution_status"] = generation_status
    if failure_reason:
        result["question_evolution_error"] = failure_reason

    meta_info = result.get("meta_info")
    if not isinstance(meta_info, dict):
        meta_info = {}
    else:
        meta_info = dict(meta_info)

    existing_metadata = meta_info.get("question_evolution_metadata")
    if isinstance(existing_metadata, dict):
        metadata = dict(existing_metadata)
    else:
        metadata = {}
    metadata["question_evolved"] = False

    score_rate = get_score_rate(item)
    if score_rate is not None:
        metadata.setdefault("trigger_score_rate", score_rate)
    if generation_status:
        metadata["question_evolution_status"] = generation_status
    if failure_reason:
        metadata["question_evolution_error"] = failure_reason

    meta_info["question_evolution_metadata"] = metadata
    result["meta_info"] = meta_info
    return result


def make_passthrough_candidate_record(
    item: Dict[str, Any],
    requested_candidates: int,
    *,
    generation_status: Optional[str] = None,
    failure_reason: Optional[str] = None,
) -> Dict[str, Any]:
    result = make_passthrough_record(
        item,
        generation_status=generation_status,
        failure_reason=failure_reason,
    )
    group_id = get_candidate_group_id(item)
    candidate_suffix = generation_status or "pass_through"
    result["candidate_group_id"] = group_id
    result["candidate_id"] = f"{group_id}::{candidate_suffix}"
    result["candidate_generation"] = {
        "candidate_index": 0,
        "num_candidates_requested": requested_candidates,
        "operator_id": None,
        "operator_source": "pass_through",
        "generation_status": generation_status or "pass_through",
    }
    if failure_reason:
        result["candidate_generation"]["failure_reason"] = failure_reason
    return result


def make_generation_failure_passthrough_record(item: Dict[str, Any], error: Exception) -> Dict[str, Any]:
    status = classify_generation_failure(error)
    return make_passthrough_record(
        item,
        generation_status=status,
        failure_reason=str(error),
    )


def make_generation_failure_passthrough_candidate_record(
    item: Dict[str, Any],
    requested_candidates: int,
    error: Exception,
) -> Dict[str, Any]:
    status = classify_generation_failure(error)
    return make_passthrough_candidate_record(
        item,
        requested_candidates,
        generation_status=status,
        failure_reason=str(error),
    )


def should_evolve(item: Dict[str, Any], min_score_rate: float) -> bool:
    if uses_stage_action_contract(item):
        budget = get_round0_recommended_budget(item)
        if budget == 0:
            return False
        return action_requires_evolution(item)
    score_rate = get_score_rate(item)
    if score_rate is None:
        return False
    return score_rate >= min_score_rate


def get_reference_answer(item: Dict[str, Any]) -> str:
    meta_info = item.get("meta_info")
    if isinstance(meta_info, dict):
        references = meta_info.get("references")
        if isinstance(references, list) and references and isinstance(references[0], str) and references[0].strip():
            return references[0].strip()

        answers_list = meta_info.get("answers_list")
        if isinstance(answers_list, list) and answers_list and isinstance(answers_list[0], str) and answers_list[0].strip():
            return answers_list[0].strip()

    for field in ("reference_answer", "answer_from_book"):
        value = item.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()

    raise ValueError("缺少有效 reference_answer/meta_info.references[0]")


def get_candidate_answer(item: Dict[str, Any]) -> str:
    scoring_result = item.get("scoring_result")
    if isinstance(scoring_result, dict):
        value = scoring_result.get("candidate_answer")
        if isinstance(value, str) and value.strip():
            return value.strip()
    value = item.get("candidate_answer")
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValueError("缺少有效 scoring_result.candidate_answer")


def build_evolution_prompt(
    item: Dict[str, Any],
    prompt_version: str = "v1",
    operator_id: Optional[str] = None,
    validation_reject_reason: Optional[str] = None,
) -> str:
    prompt = item.get("prompt")
    rubric = item.get("rubric")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("缺少有效 prompt")

    if operator_id:
        user_prompt = build_operator_prompt(
            operator_id,
            prompt=prompt.strip(),
            reference_answer=get_reference_answer(item),
            candidate_answer=get_candidate_answer(item),
            rubric=rubric if isinstance(rubric, list) else [],
            sample_profile=get_sample_profile(item),
            overscore_diagnosis=get_overscore_diagnosis(item),
            evolution_state=get_evolution_state(item),
            operator_route=get_operator_route(item),
        )
        return append_validation_retry_instruction(user_prompt, validation_reject_reason)

    replacements = {
        "{|prompt|}": prompt.strip(),
        "{|rubrics|}": json.dumps(rubric if isinstance(rubric, list) else [], ensure_ascii=False, indent=2),
        "{|response1|}": get_reference_answer(item),
        "{|response2|}": get_candidate_answer(item),
    }
    user_prompt = get_evolution_prompt_template(prompt_version)
    for placeholder, value in replacements.items():
        user_prompt = user_prompt.replace(placeholder, value)
    return append_validation_retry_instruction(user_prompt, validation_reject_reason)


def build_validation_probe_record(item: Dict[str, Any], evolved: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    original_prompt = str(item.get("prompt", "") or "").strip()
    result["prompt"] = str(evolved.get("evolved_prompt", "") or "").strip()
    result["question_evolved"] = True

    meta_info = result.get("meta_info")
    if not isinstance(meta_info, dict):
        meta_info = {}
    else:
        meta_info = dict(meta_info)
    meta_info.setdefault("prompt_old", original_prompt)

    metadata = meta_info.get("question_evolution_metadata")
    if not isinstance(metadata, dict):
        metadata = {}
    else:
        metadata = dict(metadata)
    metadata["question_evolved"] = True

    complexity_budget = evolved.get("complexity_budget")
    if isinstance(complexity_budget, dict):
        metadata["complexity_budget"] = complexity_budget

    operator_used = evolved.get("operator_used")
    if isinstance(operator_used, str) and operator_used.strip():
        metadata["operator_used"] = operator_used.strip()

    meta_info["question_evolution_metadata"] = metadata
    result["meta_info"] = meta_info
    if isinstance(operator_used, str) and operator_used.strip():
        result["candidate_operator"] = operator_used.strip()
    return result


def validate_evolved_result_against_stage_rules(
    item: Dict[str, Any],
    evolved: Dict[str, Any],
) -> Dict[str, Any]:
    probe = build_validation_probe_record(item, evolved)
    return validation_stage.validate_record(probe)


def enrich_evolution_result_with_operator(
    evolved: Dict[str, Any],
    item: Dict[str, Any],
    operator_id: str,
) -> Dict[str, Any]:
    spec = get_operator_spec(operator_id)
    route = get_operator_route(item)
    diagnosis = get_overscore_diagnosis(item)
    enriched = dict(evolved)
    enriched["operator_used"] = operator_id
    enriched["ability_axis"] = spec.ability_axis

    target_failure = str(diagnosis.get("target_failure_mode", "") or "").strip()
    cause = str(diagnosis.get("candidate_overscore_cause", "") or "").strip()
    routing_reason = str(route.get("routing_reason", "") or "").strip()

    if routing_reason:
        enriched.setdefault("boundary_hypothesis", routing_reason)
    if target_failure or cause:
        enriched.setdefault("expected_qwen_failure", target_failure or cause)
    if not _normalize_string_list(
        enriched.get("expected_evaluation_focus", enriched.get("evaluation_focus"))
    ):
        enriched["expected_evaluation_focus"] = list(spec.default_evaluation_focus)
    return enriched


def make_evolved_record(item: Dict[str, Any], evolved: Dict[str, Any], score_rate: Optional[float], model: str) -> Dict[str, Any]:
    """构造进化后的记录。注意：rubric/score_prompt/scoring_result 对已改变 prompt 的题目已失效，移到 meta_info 中保存。"""
    result = dict(item)
    original_prompt = str(item.get("prompt", "")).strip()
    evolved_prompt = evolved["evolved_prompt"]

    # 保留旧 prompt 与旧评分产物
    meta_info = result.get("meta_info")
    if not isinstance(meta_info, dict):
        meta_info = {}
    else:
        meta_info = dict(meta_info)

    # 保存直接父题的一层快照，供候选全部无效或 score_increased 时完整回滚。
    # 不递归保存更早快照，保持当前实现的单层父节点语义。
    parent_snapshot = {
        "prompt": original_prompt,
        "rubric": item.get("rubric"),
        "rubric_thought_process": item.get("rubric_thought_process"),
        "score_prompt": item.get("score_prompt"),
        "scoring_result": item.get("scoring_result"),
        "score_rate": get_score_rate(item),
        "question_evolved": item.get("question_evolved", False),
        "references": meta_info.get("references"),
        "prompt_old": meta_info.get("prompt_old"),
        "question_evolution_metadata": meta_info.get("question_evolution_metadata"),
    }
    meta_info["parent_snapshot"] = parent_snapshot
    meta_info["prompt_old"] = original_prompt
    meta_info["stale_references"] = meta_info.get("references")
    meta_info["stale_rubric"] = result.pop("rubric", None)
    meta_info["stale_rubric_thought_process"] = result.pop("rubric_thought_process", None)
    meta_info["stale_score_prompt"] = result.pop("score_prompt", None)
    meta_info["stale_scoring_result"] = result.pop("scoring_result", None)

    # 写入 question evolution 元数据。expected_evaluation_focus 只保存在这里，
    # 不传入 rubric 生成或评分 prompt。
    metadata = {
        "question_evolved": True,
        "trigger_score_rate": score_rate,
        "question_evolution_model": model,
        "evolution_strategy": evolved.get("evolution_strategy", ""),
        "notes_for_reference": evolved.get("notes_for_reference", ""),
        "question_evolution_raw_response": evolved.get("question_evolution_raw_response", ""),
    }

    expected_evaluation_focus = _normalize_string_list(
        evolved.get("expected_evaluation_focus", evolved.get("evaluation_focus"))
    )
    if expected_evaluation_focus:
        metadata["expected_evaluation_focus"] = expected_evaluation_focus

    for field in (
        "operator_used",
        "ability_axis",
        "target_subclaim",
        "boundary_hypothesis",
        "expected_qwen_failure",
    ):
        value = evolved.get(field)
        if isinstance(value, str) and value.strip():
            metadata[field] = value.strip()

    complexity_budget = evolved.get("complexity_budget")
    if isinstance(complexity_budget, dict):
        metadata["complexity_budget"] = complexity_budget

    validation_retry = evolved.get("validation_retry")
    if isinstance(validation_retry, dict):
        metadata["validation_retry"] = validation_retry

    local_validation_result = evolved.get("_local_validation_result")
    if isinstance(local_validation_result, dict):
        metadata["local_validation_result"] = dict(local_validation_result)

    meta_info["question_evolution_metadata"] = metadata

    result["prompt"] = evolved_prompt
    result["meta_info"] = meta_info
    result["question_evolved"] = True
    return result


def make_evolved_candidate_record(
    item: Dict[str, Any],
    evolved: Dict[str, Any],
    score_rate: Optional[float],
    model: str,
    *,
    candidate_index: int,
    requested_candidates: int,
    operator_id: Optional[str],
) -> Dict[str, Any]:
    result = make_evolved_record(item, evolved, score_rate, model)
    group_id = get_candidate_group_id(item)
    candidate_operator = operator_id or evolved.get("operator_used")
    candidate_operator = candidate_operator.strip() if isinstance(candidate_operator, str) else ""
    result["candidate_group_id"] = group_id
    result["candidate_id"] = f"{group_id}::cand_{candidate_index}"
    result["candidate_operator"] = candidate_operator
    if candidate_operator:
        meta_info = result.get("meta_info")
        meta_info = dict(meta_info) if isinstance(meta_info, dict) else {}
        metadata = meta_info.get("question_evolution_metadata")
        metadata = dict(metadata) if isinstance(metadata, dict) else {}
        metadata["operator_used"] = candidate_operator
        metadata["question_evolved"] = True
        meta_info["question_evolution_metadata"] = metadata
        result["meta_info"] = meta_info
    result["candidate_generation"] = {
        "candidate_index": candidate_index,
        "num_candidates_requested": requested_candidates,
        "operator_id": operator_id,
        "operator_source": "primary" if candidate_index == 1 else f"backup_{candidate_index - 1}",
    }
    return result


class QuestionEvolutionProcessor:
    def __init__(
        self,
        client: RotatingAPIClient,
        model: str,
        max_concurrent: int = 20,
        max_retries: int = 3,
        min_score_rate: float = 0.8,
        prompt_version: str = "v1",
        num_candidates: int = 1,
        max_validation_retries: int = DEFAULT_MAX_VALIDATION_RETRIES,
        max_candidate_budget: int = 0,
    ):
        self.client = client
        self.model = model
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.write_lock = asyncio.Lock()
        self.max_retries = max_retries
        self.min_score_rate = min_score_rate
        self.prompt_version = prompt_version
        self.num_candidates = num_candidates
        self.max_validation_retries = max(0, max_validation_retries)
        self.max_candidate_budget = max_candidate_budget

    async def evolve_once(
        self,
        item: Dict[str, Any],
        operator_id: Optional[str] = None,
        validation_reject_reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        if operator_id:
            get_operator_spec(operator_id)
        elif uses_stage_action_contract(item):
            operator_id = resolve_operator_id(item)
        user_prompt = build_evolution_prompt(
            item,
            self.prompt_version,
            operator_id=operator_id,
            validation_reject_reason=validation_reject_reason,
        )
        response = await self.client.chat_completions_create(
            model=self.model,
            messages=[
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.3,
            max_tokens=MAX_OUTPUT_TOKENS
        )
        content = extract_answer(response)
        evolved = parse_evolution_response(content)
        if operator_id:
            evolved = enrich_evolution_result_with_operator(evolved, item, operator_id)
        evolved["question_evolution_raw_response"] = content
        return evolved

    async def _evolve_once_with_model_retry(
        self,
        item: Dict[str, Any],
        operator_id: Optional[str],
        validation_reject_reason: Optional[str],
    ) -> Dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            try:
                return await self.evolve_once(
                    item,
                    operator_id=operator_id,
                    validation_reject_reason=validation_reject_reason,
                )
            except Exception as e:
                logger.warning(
                    f"question 进化失败 (尝试 {attempt + 1}/{self.max_retries + 1}) "
                    f"index={item.get('index')}: {str(e)[:200]}"
                )
                if attempt < self.max_retries:
                    error_text = str(e)
                    if "调用频率" in error_text or "qpm" in error_text.lower() or "0x04030020" in error_text:
                        await asyncio.sleep(30)
                    else:
                        await asyncio.sleep(attempt + 1)
                else:
                    raise
        raise RuntimeError("question 进化重试逻辑异常退出")

    async def evolve_with_retry(self, item: Dict[str, Any], operator_id: Optional[str] = None) -> Dict[str, Any]:
        reject_reason: Optional[str] = None
        first_reject_reason: Optional[str] = None
        for validation_attempt in range(self.max_validation_retries + 1):
            evolved = await self._evolve_once_with_model_retry(
                item,
                operator_id=operator_id,
                validation_reject_reason=reject_reason,
            )
            validation_result = validate_evolved_result_against_stage_rules(item, evolved)
            if validation_result.get("passed") is True:
                evolved["_local_validation_result"] = validation_result
                if validation_attempt:
                    evolved["validation_retry"] = {
                        "attempts": validation_attempt,
                        "max_validation_retries": self.max_validation_retries,
                        "first_reject_reason": first_reject_reason,
                        "final_reject_reason": None,
                    }
                return evolved

            reject_reason = str(validation_result.get("reject_reason") or "未通过复杂度/可回答性校验").strip()
            first_reject_reason = first_reject_reason or reject_reason
            if validation_attempt < self.max_validation_retries:
                logger.warning(
                    "候选题未通过独立校验，将带 reject_reason 使用同一 operator 重试 "
                    f"({validation_attempt + 1}/{self.max_validation_retries}) "
                    f"index={item.get('index')} reason={reject_reason[:200]}"
                )
                continue

            evolved["validation_retry"] = {
                "attempts": validation_attempt,
                "max_validation_retries": self.max_validation_retries,
                "first_reject_reason": first_reject_reason,
                "final_reject_reason": reject_reason,
            }
            evolved["_local_validation_result"] = validation_result
            return evolved

    async def process_item(self, item: Dict[str, Any]) -> Dict[str, Any]:
        async with self.semaphore:
            if not should_evolve(item, self.min_score_rate):
                # 未触发进化，原样输出，仅加标记
                return make_passthrough_record(item)

            score_rate = get_score_rate(item)
            try:
                evolved = await self.evolve_with_retry(item)
                return make_evolved_record(item, evolved, score_rate, self.model)
            except Exception as e:
                logger.error(
                    f"question 进化失败，改为透传 index={item.get('index')} "
                    f"prompt={str(item.get('prompt', ''))[:80]} error={e}"
                )
                return make_generation_failure_passthrough_record(item, e)

    async def process_item_candidates(
        self,
        item: Dict[str, Any],
        requested_candidates: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        async with self.semaphore:
            candidate_count = self.num_candidates if requested_candidates is None else requested_candidates
            if not should_evolve(item, self.min_score_rate):
                return [make_passthrough_candidate_record(item, candidate_count)]

            score_rate = get_score_rate(item)
            try:
                if uses_stage_action_contract(item):
                    operator_ids = resolve_candidate_operator_ids(item, candidate_count)
                else:
                    operator_ids = [None]
            except Exception as e:
                logger.error(
                    f"候选算子解析失败，改为透传 index={item.get('index')} "
                    f"prompt={str(item.get('prompt', ''))[:80]} error={e}"
                )
                return [make_generation_failure_passthrough_candidate_record(item, candidate_count, e)]

            candidates: List[Dict[str, Any]] = []
            generation_errors: List[str] = []
            for candidate_index, operator_id in enumerate(operator_ids, start=1):
                try:
                    evolved = await self.evolve_with_retry(item, operator_id=operator_id)
                    candidates.append(
                        make_evolved_candidate_record(
                            item,
                            evolved,
                            score_rate,
                            self.model,
                            candidate_index=candidate_index,
                            requested_candidates=candidate_count,
                            operator_id=operator_id,
                        )
                    )
                except Exception as e:
                    logger.error(
                        f"候选生成失败 index={item.get('index')} "
                        f"operator={operator_id or '<legacy>'} error={e}"
                    )
                    generation_errors.append(f"{operator_id or '<legacy>'}: {e}")
                    continue
            if not candidates:
                error_text = "所有候选生成失败"
                if generation_errors:
                    error_text += "；" + "；".join(generation_errors[:3])
                error = RuntimeError(error_text)
                logger.error(
                    f"所有候选生成失败，改为透传 index={item.get('index')} "
                    f"prompt={str(item.get('prompt', ''))[:80]}"
                )
                return [make_generation_failure_passthrough_candidate_record(item, candidate_count, error)]
            return candidates

    def recommended_candidate_count(self, item: Dict[str, Any]) -> int:
        if not should_evolve(item, self.min_score_rate):
            return 1

        count = 1
        route = get_operator_route(item)
        state = get_evolution_state(item)
        action = get_evolution_action(item)

        if route.get("is_high_value_sample") is True:
            count = max(count, 2)
        if route.get("should_use_local_tree_search") is True:
            count = max(count, 2)
        if action == RECONSTRUCT_LOW_SCORE_BOUNDARY:
            count = max(count, 2)

        try:
            full_score_count = int(state.get("consecutive_full_score_count", 0) or 0)
        except (TypeError, ValueError):
            full_score_count = 0
        if full_score_count >= 1:
            count = max(count, 2)
        if full_score_count >= 2:
            count = max(count, 3)

        previous_effect = str(state.get("previous_effect_status", "") or "")
        try:
            invalid_count = int(state.get("consecutive_invalid_generation_count", 0) or 0)
        except (TypeError, ValueError):
            invalid_count = 0
        if invalid_count >= 2 or previous_effect in {"invalid_complexity", "no_clear_effect"}:
            count = max(count, 3)

        round0_budget = get_round0_recommended_budget(item)
        if round0_budget is not None and round0_budget > 0:
            count = min(count, round0_budget)

        return max(1, min(self.num_candidates, count))

    def allocate_candidate_counts(self, items: List[Dict[str, Any]]) -> Dict[str, int]:
        target_items = [item for item in items if should_evolve(item, self.min_score_rate)]
        if not target_items:
            return {}

        budget = self.max_candidate_budget
        if budget <= 0:
            budget = len(target_items) * 2
        if budget < len(target_items):
            raise ValueError(
                f"max_candidate_budget={budget} 小于待进化样本数 {len(target_items)}，无法保证每个样本至少 1 个候选"
            )

        counts = {get_item_key(item): 1 for item in target_items}
        remaining = budget - len(target_items)
        ranked_items = sorted(
            target_items,
            key=lambda item: self.recommended_candidate_count(item),
            reverse=True,
        )
        for item in ranked_items:
            key = get_item_key(item)
            desired = self.recommended_candidate_count(item)
            while counts[key] < desired and remaining > 0:
                counts[key] += 1
                remaining -= 1
            if remaining <= 0:
                break
        return counts

    async def process_file(
        self,
        input_path: str,
        output_path: str,
        *,
        performance_path: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        if not os.path.exists(input_path):
            raise FileNotFoundError(f"输入文件不存在: {input_path}")

        stage = "question_evolution"
        resolved_config = config or {
            "model": self.model,
            "min_score_rate": self.min_score_rate,
            "num_candidates": self.num_candidates,
            "max_candidate_budget": self.max_candidate_budget,
            "validation_retries": self.max_validation_retries,
        }
        valid, _ = validate_published_artifact(
            output_path,
            stage=stage,
            input_path=input_path,
            config=resolved_config,
        )
        if valid:
            logger.info("Verified published evolution artifact; skipping %s", output_path)
            return

        metrics = StageMetrics(stage)
        metrics.input_bytes = os.path.getsize(input_path)
        # Candidate allocation is intentionally global and therefore still
        # reads the round input before starting workers.
        parse_started = time.monotonic()
        items = list(iter_json_records(input_path, stage=stage))
        metrics.parse_seconds += time.monotonic() - parse_started
        target_items = [item for item in items if should_evolve(item, self.min_score_rate)]
        logger.info(
            "读取到 %s 条记录，其中需要 question evolution 的记录 %s 条",
            len(items),
            len(target_items),
        )

        writer = AtomicJsonlStageWriter(
            output_path,
            stage=stage,
            input_path=input_path,
            config=resolved_config,
            code_paths=[__file__, validation_stage.__file__],
            metrics=metrics,
        )
        # Candidate allocation is derived from the complete round input on every
        # run.  Recovery only filters execution, so the original per-round budget
        # cannot be redistributed to the remaining samples.
        candidate_counts = self.allocate_candidate_counts(items) if self.num_candidates > 1 else {}
        pending_items = [item for item in items if get_item_key(item) not in writer.processed_keys]
        sidecar_path = output_path + ".evolution_traces.jsonl.gz"
        traces = TraceStore(stage, recovery_path=sidecar_path + ".partial")
        failed_path = output_path + ".failed"
        failed_count = 0

        def externalize_trace(record: Dict[str, Any]) -> Dict[str, Any]:
            result = dict(record)
            meta_info = result.get("meta_info")
            if not isinstance(meta_info, dict):
                return result
            meta_info = dict(meta_info)
            metadata = meta_info.get("question_evolution_metadata")
            if not isinstance(metadata, dict):
                return result
            metadata = dict(metadata)
            raw_response = metadata.pop("question_evolution_raw_response", None)
            if isinstance(raw_response, str):
                metadata["question_evolution_raw_response_trace_id"] = traces.add(
                    record_key=str(result.get("candidate_id") or get_item_key(result)),
                    raw_text=raw_response,
                    trace_kind="question_evolution_model_response",
                    metadata={"model": self.model},
                )
            meta_info["question_evolution_metadata"] = metadata
            result["meta_info"] = meta_info
            return result

        async def worker(item: Dict[str, Any]):
            try:
                if self.num_candidates > 1:
                    records = await self.process_item_candidates(
                        item,
                        requested_candidates=candidate_counts.get(get_item_key(item), 1),
                    )
                else:
                    records = [await self.process_item(item)]
                return [externalize_trace(record) for record in records], None
            except Exception as exc:
                requested = candidate_counts.get(get_item_key(item), 1) if self.num_candidates > 1 else 1
                fallback = (
                    make_generation_failure_passthrough_candidate_record(item, requested, exc)
                    if self.num_candidates > 1
                    else make_generation_failure_passthrough_record(item, exc)
                )
                failed = dict(item)
                failed["question_evolution_error"] = str(exc)
                return [fallback], failed

        async def on_result(_sequence: int, item: Dict[str, Any], outcome) -> None:
            nonlocal failed_count
            records, failed = outcome
            if failed is None:
                writer.add_group(get_item_key(item), records)
            else:
                os.makedirs(os.path.dirname(os.path.abspath(failed_path)), exist_ok=True)
                with open(failed_path, "a", encoding="utf-8") as target:
                    target.write(json.dumps(failed, ensure_ascii=False) + "\n")
                    target.flush()
                failed_count += 1

        try:
            await bounded_async_map(
                pending_items,
                worker,
                concurrency=max(1, self.semaphore._value),
                on_result=on_result,
                metrics=metrics,
            )
            if failed_count:
                raise RuntimeError(
                    f"question evolution 阶段有 {failed_count}/{len(pending_items)} 条记录失败；"
                    f"失败详情见 {failed_path}，已停止后续流水线。"
                )
            trace_path, trace_count = traces.write(sidecar_path)
            writer.register_sidecar(
                trace_path,
                kind="question_evolution_raw_response",
                record_count=trace_count,
            )
            writer.publish()
            traces.finalize_recovery()
        except Exception:
            writer.close()
            append_performance_event(performance_path, metrics.event(status="failed"))
            raise

        if os.path.exists(failed_path):
            os.remove(failed_path)
        append_performance_event(performance_path, metrics.event())
        logger.info("question 进化/全量输出完成，结果保存至: %s", output_path)


async def main():
    parser = argparse.ArgumentParser(
        description="全量输出 scoring 结果，并为得分率过高的记录生成更难、更具区分度的进化后问题"
    )
    parser.add_argument("--input", type=str, required=True, help="scoring.py 输出的 jsonl/json 文件路径")
    parser.add_argument("--output", type=str, help="输出 jsonl 文件路径，默认在输入文件名后追加 _question_evolved")
    parser.add_argument("--concurrency", type=int, default=20, help="并行处理的题目数量")
    parser.add_argument("--retries", type=int, default=3, help="模型调用失败时的重试次数")
    parser.add_argument(
        "--min-score-rate",
        type=float,
        default=0.8,
        help="触发 question 进化的最低得分率，默认 0.8"
    )
    parser.add_argument("--model", type=str, default=EVOLVE_MODEL, help="question evolution 模型名称")
    parser.add_argument("--base-url", type=str, default=EVOLVE_BASE_URL, help="OpenAI 兼容 base_url")
    parser.add_argument("--api-key", action="append", default=None, help="API key；可多次传入覆盖脚本默认 key")
    parser.add_argument("--request-timeout", type=float, default=REQUEST_TIMEOUT_SECONDS, help="单次请求 timeout 秒数")
    parser.add_argument(
        "--performance-events",
        default=None,
        help="Append metrics to this performance_events.jsonl file.",
    )
    parser.add_argument(
        "--prompt-version",
        default="v1",
        help="question evolution prompt 版本: v1=baseline, v2=反模板化/聚焦"
    )
    parser.add_argument(
        "--num-candidates",
        type=int,
        default=1,
        help="每条需进化样本生成的候选题数量；大于 1 时输出候选记录供 validate/select 阶段消费"
    )
    parser.add_argument(
        "--max-candidate-budget",
        type=int,
        default=0,
        help="单轮待进化样本的候选总预算；<=0 时默认为待进化样本数 * 2"
    )
    parser.add_argument(
        "--validation-retries",
        type=int,
        default=DEFAULT_MAX_VALIDATION_RETRIES,
        help="候选题未通过 validate_evolved_question 规则校验时，使用同一 operator 带 reject_reason 重试的次数"
    )
    args = parser.parse_args()

    if args.min_score_rate < 0 or args.min_score_rate > 1:
        raise ValueError("--min-score-rate 必须在 [0, 1] 之间")
    if args.num_candidates < 1 or args.num_candidates > 4:
        raise ValueError("--num-candidates 必须在 [1, 4] 之间")
    if args.validation_retries < 0 or args.validation_retries > 1:
        raise ValueError("--validation-retries 当前只允许 0 或 1，避免无限修正循环")

    if not args.output:
        base, ext = os.path.splitext(args.input)
        args.output = f"{base}_question_evolved{ext or '.jsonl'}"

    api_keys = parse_api_keys(args.api_key)
    client = RotatingAPIClient(
        base_url=args.base_url or EVOLVE_BASE_URL,
        api_keys=api_keys,
        request_timeout=args.request_timeout
    )

    processor = QuestionEvolutionProcessor(
        client=client,
        model=args.model or EVOLVE_MODEL,
        max_concurrent=args.concurrency,
        max_retries=args.retries,
        min_score_rate=args.min_score_rate,
        prompt_version=args.prompt_version,
        num_candidates=args.num_candidates,
        max_validation_retries=args.validation_retries,
        max_candidate_budget=args.max_candidate_budget,
    )

    try:
        await processor.process_file(
            args.input,
            args.output,
            performance_path=args.performance_events,
            config={
                "model": args.model or EVOLVE_MODEL,
                "base_url": args.base_url or EVOLVE_BASE_URL,
                "concurrency": args.concurrency,
                "retries": args.retries,
                "min_score_rate": args.min_score_rate,
                "prompt_version": args.prompt_version,
                "num_candidates": args.num_candidates,
                "max_candidate_budget": args.max_candidate_budget,
                "validation_retries": args.validation_retries,
            },
        )
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())
