"""Surface-safe context helpers for semantic-economy question generation.

The ledgers are intentionally small, deterministic and inspectable.  They do
not decide whether a candidate passes; that remains the independent validation
stage.  Their only job is to prevent answer/rubric material from being handed
to the question-surface generator by default.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from governance import analyze_source, clean_text as governance_clean_text, public_fact_projection, resolve_evolution_mode


LEDGER_VERSION = "semantic_ledger_v1"
BOUNDARY_MARKERS = (
    "不能直接",
    "不得直接",
    "只能作为线索",
    "仅作为线索",
    "需要结合",
    "还需结合",
    "不足以",
    "无法确认",
    "不能确认",
    "不宜认定",
    "结论边界",
    "最高支持",
    "最高能够",
    "不能推出",
    "不能说明",
    "不可认定",
)
RUBRIC_MARKERS = (
    "评分",
    "得分",
    "扣分",
    "考查",
    "应当说明",
    "回答应",
    "常见错误",
    "判定口径",
)
OBSERVABLE_MARKERS = (
    "画面",
    "视频",
    "可见",
    "显示",
    "记录",
    "时间",
    "地点",
    "位置",
    "物品",
    "动作",
    "出现",
    "经过",
    "进入",
    "离开",
    "来源",
    "规则",
    "阈值",
    "数值",
    "数据",
)
SURFACE_LEAK_PATTERNS = {
    "boundary_language_leak": (
        "结论边界",
        "最高支持",
        "最高能够",
        "不能直接推出",
        "仅凭现有材料",
        "仅凭视频",
        "不能单独写成",
        "只能作为线索",
        "支持到什么程度",
        "哪些说法不能",
    ),
    "rubric_axis_leak": (
        "评分维度",
        "评分标准",
        "得分点",
        "本题考查",
        "判定口径",
        "预期错误",
    ),
    "reasoning_path_leak": (
        "先判断",
        "再判断",
        "逐项说明",
        "分别列出",
        "按以下步骤",
        "先比较",
    ),
}
CAUTIOUS_OPTION_MARKERS = ("疑似", "线索", "初步", "核查", "限定范围", "后续核查")
ASSERTIVE_OPTION_MARKERS = ("已查明", "认定", "违法处置", "直接确认", "确定为")


def clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _sentences(value: Any) -> List[str]:
    text = clean_text(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])|\n+", text) if part.strip()]


def _ledger_entries(values: Iterable[str], *, prefix: str, source: str) -> List[Dict[str, str]]:
    seen = set()
    entries: List[Dict[str, str]] = []
    for value in values:
        text = clean_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        entries.append({"fact_id": f"{prefix}_{len(entries) + 1}", "text": text, "source": source})
    return entries


def build_reference_ledgers(
    *,
    original_prompt: Any,
    reference_answer: Any,
    rubric: Any = None,
) -> Dict[str, Any]:
    """Split supplied material into question facts, answer boundaries and rubric intent.

    Prompt text is treated as the authoritative observable context because it is
    already user-visible.  Reference-answer sentences enter the observable
    ledger only when they look like concrete observations/rules and do not look
    like an answer-side conclusion.  This conservative default favours keeping
    a fact out of the generator over leaking a conclusion into a new question.
    """

    prompt_text = clean_text(original_prompt)
    # Do not collapse the whole parent question into a single "fact".  This
    # used to smuggle source claims and answer direction through the same
    # channel as observations.  The deterministic source analyzer gives every
    # sentence its own provenance and keeps claims outside the public ledger.
    source_analysis = analyze_source({"prompt": prompt_text})
    observable = []
    for entry in source_analysis["source_observation_ledger"]:
        observable.append({
            "fact_id": entry["fact_id"],
            "text": entry["text"],
            "source": "source_observation",
            "world_id": entry["world_id"],
            "global_fact_key": entry["global_fact_key"],
            "origin_type": entry["origin_type"],
            "source_locator": entry["source_locator"],
        })
    boundary_values: List[str] = []
    rubric_values: List[str] = []
    observable_reference_values: List[str] = []

    for sentence in _sentences(reference_answer):
        if any(marker in sentence for marker in BOUNDARY_MARKERS):
            boundary_values.append(sentence)
        elif any(marker in sentence for marker in RUBRIC_MARKERS):
            rubric_values.append(sentence)
        elif any(marker in sentence for marker in OBSERVABLE_MARKERS):
            observable_reference_values.append(sentence)
        else:
            # A reference-answer sentence without an observable signal is an
            # answer-side assertion, not a fact safe to reveal to the generator.
            boundary_values.append(sentence)

    observable.extend(
        _ledger_entries(observable_reference_values, prefix="reference_fact", source="reference_answer")
    )
    if isinstance(rubric, list):
        for item in rubric:
            if isinstance(item, dict):
                title = clean_text(item.get("title"))
                description = clean_text(item.get("description"))
                if title:
                    rubric_values.append(title)
                if description:
                    rubric_values.append(description)
            else:
                rubric_values.append(clean_text(item))

    mode_decision = resolve_evolution_mode({"prompt": prompt_text}, source_analysis)
    projection = public_fact_projection(source_analysis, mode_decision)
    return {
        "ledger_version": LEDGER_VERSION,
        "classification_method": "deterministic_source_and_answer_material_split",
        "source_analysis": source_analysis,
        "mode_decision": mode_decision,
        "public_fact_projection": projection,
        "observable_fact_ledger": observable,
        "answer_boundary_ledger": _ledger_entries(boundary_values, prefix="boundary", source="answer_or_boundary"),
        "rubric_intent_ledger": _ledger_entries(rubric_values, prefix="rubric", source="rubric_or_intent"),
    }


def generator_visible_context(*, original_prompt: Any, ledgers: Dict[str, Any]) -> Dict[str, Any]:
    """Return exactly the context permitted to question-surface generation."""

    observable = ledgers.get("observable_fact_ledger", []) if isinstance(ledgers, dict) else []
    projection = ledgers.get("public_fact_projection") if isinstance(ledgers, dict) else None
    return {
        # original_question remains only for legacy compatibility.  New writer
        # prompts consume the projection and never receive answer-side fields.
        "original_question": clean_text(original_prompt),
        "observable_fact_ledger": list(observable) if isinstance(observable, list) else [],
        "public_fact_projection": dict(projection) if isinstance(projection, dict) else {},
        "evolution_mode": clean_text((ledgers.get("mode_decision") or {}).get("evolution_mode")) if isinstance(ledgers, dict) else "",
        "context_policy": "题面编写器只接收公开事实投影和中性任务；答案边界、评分意图、目标错误与隐藏规划不可见。",
    }


def answer_generation_context(question: Any, ledgers: Any) -> str:
    """Build answer-side context without changing the question-side prompt."""

    question_text = clean_text(question)
    if not isinstance(ledgers, dict):
        return question_text
    boundaries = ledgers.get("answer_boundary_ledger")
    intent = ledgers.get("rubric_intent_ledger")
    if not isinstance(boundaries, list) and not isinstance(intent, list):
        return question_text
    boundary_texts = [clean_text(entry.get("text")) for entry in boundaries or [] if isinstance(entry, dict)]
    intent_texts = [clean_text(entry.get("text")) for entry in intent or [] if isinstance(entry, dict)]
    details = [text for text in [*boundary_texts, *intent_texts] if text]
    if not details:
        return question_text
    return (
        "请为以下题目生成可供评分使用的标准答案。答案必须保留材料支持范围和必要判定口径；"
        "这些内容是答案侧指导，不能说成题面已明确提示的作答方向。\n\n"
        f"# 题目\n{question_text}\n\n"
        "# 答案侧边界与评分意图\n"
        + "\n".join(f"- {text}" for text in details)
    )


def detect_surface_leaks(prompt: Any) -> Dict[str, Any]:
    """Locate deterministic, high-confidence question-surface leakage signals.

    This deliberately does not flag a plain “请说明依据”: asking for reasons is
    natural, while revealing *which* boundary to reach is not.
    """

    text = clean_text(prompt)
    matches: List[Dict[str, str]] = []
    leak_types: List[str] = []
    for leak_type, markers in SURFACE_LEAK_PATTERNS.items():
        for marker in markers:
            if marker in text:
                leak_types.append(leak_type)
                matches.append({"type": leak_type, "text": marker})

    # Options are checked conservatively: a unique cautious option is only a
    # signal when the alternatives include conspicuously assertive language.
    option_lines = [line.strip() for line in re.split(r"[\n；;]", text) if re.search(r"(?:^|\s)[A-DＡ-Ｄ甲乙丙丁][\.、:：)]", line)]
    cautious = [line for line in option_lines if any(marker in line for marker in CAUTIOUS_OPTION_MARKERS)]
    assertive = [line for line in option_lines if any(marker in line for marker in ASSERTIVE_OPTION_MARKERS)]
    if len(option_lines) >= 2 and len(cautious) == 1 and assertive:
        leak_types.append("safe_option_leak")
        matches.append({"type": "safe_option_leak", "text": cautious[0]})

    unique_types = list(dict.fromkeys(leak_types))
    return {
        "surface_leak_risk": bool(unique_types),
        "surface_leak_type": unique_types,
        "surface_leak_evidence": matches,
    }


def suggested_same_operator_retry_reason(surface_leak: Dict[str, Any]) -> str:
    leak_types = surface_leak.get("surface_leak_type", []) if isinstance(surface_leak, dict) else []
    if not isinstance(leak_types, list) or not leak_types:
        return "删除重复或无职责内容，保留可观察事实、竞争结构和自然业务任务。"
    suggestions = []
    if "boundary_language_leak" in leak_types:
        suggestions.append("删除题面中的答案边界提示，将边界保留在答案键或评分规则中")
    if "safe_option_leak" in leak_types:
        suggestions.append("重写全部选项，使谨慎语言和模态强度在选项间平衡")
    if "rubric_axis_leak" in leak_types:
        suggestions.append("删除题面对评分维度或判定口径的直接说明")
    if "reasoning_path_leak" in leak_types:
        suggestions.append("删除预设推理步骤，改为一个自然业务任务")
    return "；".join(suggestions)
