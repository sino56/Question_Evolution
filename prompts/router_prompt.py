"""Prompt builder for the strict hybrid LLM Router."""

from __future__ import annotations

import json
from typing import Any, Dict, Mapping, Optional, Sequence

from router_contract import ROUTER_PROMPT_VERSION, prompt_contract_text


def build_router_prompt(
    record: Mapping[str, Any],
    memory_summary: Optional[Dict[str, Any]] = None,
    *,
    compact_input: Optional[Mapping[str, Any]] = None,
    operator_cards: Optional[Sequence[Mapping[str, Any]]] = None,
) -> str:
    """Build one prompt from the same contract used by the response parser.

    ``memory_summary`` remains accepted for callers that used the old helper;
    production callers pass a pre-built compact input with the complete
    candidate space and no duplicated context.
    """

    payload: Dict[str, Any]
    if compact_input is not None:
        payload = dict(compact_input)
    else:
        payload = {
            "sample_id": record.get("sample_id", record.get("index")),
            "score_rate": record.get("score_rate"),
            "evolution_action": record.get("evolution_action"),
            "prompt": record.get("prompt", ""),
            "sample_profile": record.get("sample_profile", {}),
            "overscore_diagnosis": record.get("overscore_diagnosis", {}),
            "memory_operator_ids": list((memory_summary or {}).get("operator_ids", [])),
            "operator_cards": list(operator_cards or []),
        }

    frontier_instruction = ""
    if isinstance(payload.get("frontier_route"), Mapping) and payload["frontier_route"].get("enabled") is True:
        frontier_instruction = (
            "\n- 当前记录是已验证降分的 frontier：使用当前题面、当前评分证据和新的画像重建候选列表；"
            "不要重新执行仅适用于原始样本的‘是否值得进化’否决。\n"
        )

    return f"""
角色：你是 Question Evolution 的动态路由器。你只识别当前题目已经具备的推理结构，并从输入给出的合法算子卡片中选择可直接执行的候选。你不改写题目，不生成答案、Rubric 或评分建议。

内部判断顺序（不要写出这四步的分析过程）：
1. 重建任务契约：确定题目要求的输出类型、题面允许的最高结论层级、已给事实范围，以及绝不能补造的事实。
2. 定位目标失败机制：以 overscore_diagnosis.target_failure_mode 为锚点，说明高分答案具体错在错误闭环、错误绑定、忽略竞争解释、把线索上推为事实，还是把事实越级上推为可写/可行动结论；不要围绕表面主题扩散。
3. 对每个可能相关的算子检查卡片 required_slots：区分已满足与缺失的硬槽位，并判断强行构题是否会补造实体、时间窗、路径、节点、来源、规则、阈值或竞争解释。
4. 解决近邻重叠：同一失败机制只保留最具体、最直接且构题闭包更小的算子；只有存在独立真实失败机制时才并列选择多个算子。

候选门禁：
- operator_candidates 只能包含同时满足“直接压测目标失败机制或另一明确真实失败机制 + required_slots 全部已由题面给出 + 无需补造事实”的算子。
- 主题、对象或业务词相似不是硬槽位满足。缺路径节点/边/端点时间窗时不得选择路径算子；缺竞争实体及其跨节点绑定事实时不得选择实体绑定算子；缺可见端点、预期窗口、实际出口/缺席或候选比较约束时不得选择不可见区间算子。
- 任何缺硬槽位、只适合作人工复盘、或与已选算子重复压测同一失败机制的方向，都不得进入 operator_candidates。
- 不设置固定候选数量上限；只是不截断所有硬满足且有独立压测价值的候选。没有任何硬满足候选时，operator_candidates 必须返回 []，不要为了召回数量补选。

近邻选择策略：
- 失败是把观测或事实越级上推为可写/可行动结论时，优先 O27_cross_layer_conclusion_calibration；只有观测质量冲突本身决定结论时才选 O23，只有独立信息与同源重复的累积校准本身是失败时才选 O31。
- O20_multistage_event_breakpoint 足以表达一个阶段断点时，不要升级为 O28_multihop_chain_closure；O28 仅用于题面已给出跨实体、节点或路径的完整多跳闭合义务。
- 不得用“有路径相关词”替代 O22_path_topology_reachability 的图节点、边约束、端点和时间窗；不得用“多人出现”替代 O19_multi_entity_role_binding 的竞争实体、局部绑定线索与定向行为事实。

审计要求（只记录，不参与执行、排序、二次过滤或补选）：
- selected_operator_rationales：逐项给出失败机制、已满足硬槽位，并确认不需补造事实；它与 operator_candidates 必须一一对应。
- not_selected_operator_rationales：记录关键近邻或表面相关方向，说明它为何不如已选算子直接，或为何没有独立失败机制。
- uncertain_operator_rationales：记录接近但缺硬槽位的方向，明确缺失事实及强行生成会补造什么。
- operator_improvement_notes：只记录算子卡片的适用条件、required slot 或相邻边界不清之处；没有则返回 []。

工作要求：
- 只从 operator_cards 中选择 operator_id；不要因置信度、历史表现或候选数量限制而省略硬满足候选。
- evidence_spans 只能逐字复制样本输入中的文字，绝不能复制算子卡片的文字。
- 对每个候选，说明其匹配的推理对象，并用 why_not_adjacent 对比一个卡片中声明的相邻算子。
- 保持说明简短；不要输出分析过程或 Markdown。
{frontier_instruction}

响应契约版本：{ROUTER_PROMPT_VERSION}
{prompt_contract_text()}

样本输入：
{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
""".strip()
