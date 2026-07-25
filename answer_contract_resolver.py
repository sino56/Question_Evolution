"""Blind-solver prompt and deterministic answer-contract agreement resolver."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping, Sequence


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def build_blind_solver_prompt(
    *,
    evolved_prompt: str,
    fact_ledger: Sequence[Mapping[str, Any]],
) -> str:
    """Build a prompt that excludes generator keys and operator internals."""

    return (
        "你是独立 Blind Solver。请只根据题目和事实账本作答。\n"
        "你不会获得 operator ID、生成器答案键、决定性 fact ID、预期失败信息或评分关注点。"
        "不要猜测这些隐藏信息，也不要输出完整思维过程。\n\n"
        f"# 题目\n{evolved_prompt}\n\n"
        f"# 事实账本\n{json.dumps(list(fact_ledger), ensure_ascii=False, indent=2)}\n\n"
        "# 输出\n"
        "只返回合法 JSON：\n"
        "{\n"
        '  "target_claim": "从题面识别的目标命题，保持结构化值",\n'
        '  "conclusion_layer": "题目要求判断的单一结论层级",\n'
        '  "answer_key": "结构化正确关系、方向或答案对象",\n'
        '  "decisive_fact_ids": ["独立求解识别的 fact ID"],\n'
        '  "answer_summary": "简短可观察答案，不输出完整思维过程"\n'
        "}"
    )


def normalize_blind_solver_result(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("blind solver result must be an object")
    required = ("target_claim", "conclusion_layer", "answer_key", "decisive_fact_ids")
    missing = [
        field
        for field in required
        if raw.get(field) is None or raw.get(field) == "" or raw.get(field) == []
    ]
    if missing:
        raise ValueError("blind solver result missing fields: " + ", ".join(missing))
    if not isinstance(raw.get("decisive_fact_ids"), list):
        raise ValueError("blind solver decisive_fact_ids must be an array")
    return {
        "target_claim": raw.get("target_claim"),
        "conclusion_layer": str(raw.get("conclusion_layer")).strip(),
        "answer_key": raw.get("answer_key"),
        "decisive_fact_ids": list(raw.get("decisive_fact_ids")),
        "answer_summary": str(raw.get("answer_summary") or "").strip(),
    }

def resolve_answer_contract_hypotheses(
    *,
    target_claim: Any,
    conclusion_layer: str,
    generator_answer_contract: Mapping[str, Any],
    blind_solver_result: Mapping[str, Any],
    deterministic_findings: Sequence[str] = (),
) -> Dict[str, Any]:
    blind = normalize_blind_solver_result(blind_solver_result)
    if not isinstance(generator_answer_contract, Mapping):
        raise ValueError("generator answer_contract must be an object")
    generator_view = {
        "target_claim": target_claim,
        "conclusion_layer": str(conclusion_layer).strip(),
        "answer_key": generator_answer_contract.get("answer_key"),
        "decisive_fact_ids": list(generator_answer_contract.get("decisive_fact_ids") or []),
    }
    conflicts = [
        field
        for field in (
            "target_claim",
            "conclusion_layer",
            "answer_key",
            "decisive_fact_ids",
        )
        if generator_view[field] != blind[field]
    ]
    blocking_findings = [str(finding).strip() for finding in deterministic_findings if str(finding).strip()]
    if conflicts or blocking_findings:
        return {
            "status": "conflict",
            "agreement_fields": [
                field
                for field in generator_view
                if field not in conflicts
            ],
            "conflict_fields": conflicts,
            "deterministic_findings": blocking_findings,
            "blind_solver_result_hash": _hash(blind),
            "resolved_answer_contract": None,
        }

    resolved = dict(generator_answer_contract)
    resolved.update(
        {
            "target_claim": target_claim,
            "conclusion_layer": str(conclusion_layer).strip(),
            "answer_key": blind["answer_key"],
            "decisive_fact_ids": blind["decisive_fact_ids"],
        }
    )
    return {
        "status": "resolved",
        "agreement_fields": list(generator_view),
        "conflict_fields": [],
        "deterministic_findings": [],
        "blind_solver_result_hash": _hash(blind),
        "resolved_answer_contract": resolved,
    }
