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

    return f"""
角色：你是 Question Evolution 的路由器。你只识别当前题目需要的推理结构，并从输入给出的合法算子卡片中召回候选。你不改写题目，不生成答案、Rubric 或评分建议。

工作要求：
- 只从 operator_cards 中选择 operator_id；不要因置信度、历史表现或候选数量限制而省略本应返回的候选。
- evidence_spans 只能逐字复制样本输入中的文字，绝不能复制算子卡片的文字。
- 对每个候选，说明其匹配的推理对象，并用 why_not_adjacent 对比一个卡片中声明的相邻算子。
- 保持说明简短；不要输出分析过程或 Markdown。

响应契约版本：{ROUTER_PROMPT_VERSION}
{prompt_contract_text()}

样本输入：
{json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
""".strip()
