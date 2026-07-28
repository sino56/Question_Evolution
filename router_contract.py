"""Shared schema and validation rules for the hybrid LLM operator router.

The Router prompt, parser, cache, and tests import this module instead of
maintaining parallel copies of the response contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set


ROUTING_SCHEMA_VERSION = "hybrid-router-v1"
ROUTER_PROMPT_VERSION = "hybrid-router-prompt-v1"
ROUTER_TRANSPORT_POLICY_VERSION = "router-transport-v1"
ROUTER_REGISTRY_POLICY_VERSION = "eligible-operators-v1"

TOP_LEVEL_FIELDS = frozenset(
    {
        "routing_schema_version",
        "reasoning_objects",
        "operator_candidates",
        "not_selected_reasons",
        "router_comment",
    }
)
REASONING_OBJECT_FIELDS = frozenset({"name", "evidence_spans", "confidence"})
OPERATOR_CANDIDATE_FIELDS = frozenset(
    {
        "operator_id",
        "rank",
        "applicability",
        "confidence",
        "reasoning_object",
        "evidence_spans",
        "why_fit",
        "why_not_adjacent",
    }
)

APPLICABILITY_VALUES = frozenset({"applicable", "unknown", "not_applicable"})
MAX_EVIDENCE_SPANS = 2
MIN_EVIDENCE_SPANS = 1
MAX_EVIDENCE_SPAN_CHARS = 240
MAX_REASONING_OBJECT_CHARS = 120
MAX_EXPLANATION_CHARS = 180
MAX_NOT_SELECTED_REASONS = 1


class RouterContractError(ValueError):
    """A router response violates the shared response contract."""

    def __init__(self, classification: str, message: str):
        super().__init__(message)
        self.classification = classification


@dataclass(frozen=True)
class ParsedRouterResponse:
    routing_schema_version: str
    reasoning_objects: List[Dict[str, Any]]
    valid_candidates: List[Dict[str, Any]]
    rejected_candidates: List[Dict[str, Any]]
    not_selected_reasons: List[str]
    router_comment: str


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _assert_exact_fields(value: Mapping[str, Any], allowed: Set[str], label: str) -> None:
    keys = set(value)
    missing = sorted(allowed - keys)
    extra = sorted(keys - allowed)
    if missing or extra:
        details = []
        if missing:
            details.append(f"missing={missing}")
        if extra:
            details.append(f"extra={extra}")
        raise RouterContractError("schema_error", f"{label} fields are invalid: {', '.join(details)}")


def _validate_confidence(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RouterContractError("schema_error", f"{label}.confidence must be numeric")
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise RouterContractError("schema_error", f"{label}.confidence must be within [0, 1]")
    return parsed


def _validate_evidence_spans(
    value: Any,
    *,
    label: str,
    evidence_source_text: str,
) -> List[str]:
    if not isinstance(value, list) or not MIN_EVIDENCE_SPANS <= len(value) <= MAX_EVIDENCE_SPANS:
        raise RouterContractError(
            "schema_error",
            f"{label}.evidence_spans must contain {MIN_EVIDENCE_SPANS}-{MAX_EVIDENCE_SPANS} spans",
        )
    spans: List[str] = []
    for index, raw_span in enumerate(value):
        if not isinstance(raw_span, str):
            raise RouterContractError("schema_error", f"{label}.evidence_spans[{index}] must be a string")
        span = raw_span.strip()
        if not span or len(span) > MAX_EVIDENCE_SPAN_CHARS:
            raise RouterContractError("schema_error", f"{label}.evidence_spans[{index}] has invalid length")
        if span not in evidence_source_text:
            raise RouterContractError(
                "hallucinated_evidence",
                f"{label}.evidence_spans[{index}] is not copied from the router input",
            )
        spans.append(span)
    return spans


def _validate_text(value: Any, *, label: str, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise RouterContractError("schema_error", f"{label} must be a string")
    text = value.strip()
    if (not allow_empty and not text) or len(text) > maximum:
        raise RouterContractError("schema_error", f"{label} has invalid length")
    return text


def _validate_reasoning_object(
    value: Any,
    *,
    index: int,
    evidence_source_text: str,
) -> Dict[str, Any]:
    label = f"reasoning_objects[{index}]"
    if not isinstance(value, Mapping):
        raise RouterContractError("schema_error", f"{label} must be an object")
    _assert_exact_fields(value, set(REASONING_OBJECT_FIELDS), label)
    return {
        "name": _validate_text(
            value["name"],
            label=f"{label}.name",
            maximum=MAX_REASONING_OBJECT_CHARS,
        ),
        "evidence_spans": _validate_evidence_spans(
            value["evidence_spans"],
            label=label,
            evidence_source_text=evidence_source_text,
        ),
        "confidence": _validate_confidence(value["confidence"], label),
    }


def _validate_candidate(
    value: Any,
    *,
    index: int,
    eligible_operator_ids: Set[str],
    adjacent_operator_ids: Mapping[str, Set[str]],
    evidence_source_text: str,
) -> Dict[str, Any]:
    label = f"operator_candidates[{index}]"
    if not isinstance(value, Mapping):
        raise RouterContractError("schema_error", f"{label} must be an object")
    _assert_exact_fields(value, set(OPERATOR_CANDIDATE_FIELDS), label)

    operator_id = _validate_text(value["operator_id"], label=f"{label}.operator_id", maximum=200)
    if operator_id not in eligible_operator_ids:
        raise RouterContractError("ineligible_operator", f"{label}.operator_id is not eligible: {operator_id}")
    rank = value["rank"]
    if isinstance(rank, bool) or not isinstance(rank, int) or rank < 1:
        raise RouterContractError("schema_error", f"{label}.rank must be a positive integer")
    applicability = _validate_text(value["applicability"], label=f"{label}.applicability", maximum=40)
    if applicability not in APPLICABILITY_VALUES:
        raise RouterContractError("schema_error", f"{label}.applicability is invalid")
    why_not_adjacent = value["why_not_adjacent"]
    if not isinstance(why_not_adjacent, Mapping) or len(why_not_adjacent) != 1:
        raise RouterContractError("schema_error", f"{label}.why_not_adjacent must contain exactly one operator")
    adjacent_operator, adjacent_reason = next(iter(why_not_adjacent.items()))
    if not isinstance(adjacent_operator, str) or adjacent_operator not in adjacent_operator_ids.get(operator_id, set()):
        raise RouterContractError("invalid_adjacent_operator", f"{label}.why_not_adjacent does not name an adjacent operator")
    return {
        "operator_id": operator_id,
        "rank": rank,
        "applicability": applicability,
        "confidence": _validate_confidence(value["confidence"], label),
        "reasoning_object": _validate_text(
            value["reasoning_object"],
            label=f"{label}.reasoning_object",
            maximum=MAX_REASONING_OBJECT_CHARS,
        ),
        "evidence_spans": _validate_evidence_spans(
            value["evidence_spans"],
            label=label,
            evidence_source_text=evidence_source_text,
        ),
        "why_fit": _validate_text(value["why_fit"], label=f"{label}.why_fit", maximum=MAX_EXPLANATION_CHARS),
        "why_not_adjacent": {
            adjacent_operator: _validate_text(
                adjacent_reason,
                label=f"{label}.why_not_adjacent[{adjacent_operator!r}]",
                maximum=MAX_EXPLANATION_CHARS,
            )
        },
    }


def parse_router_response(
    raw_response: str,
    *,
    eligible_operator_ids: Iterable[str],
    adjacent_operator_ids: Mapping[str, Set[str]],
    evidence_source_text: str,
) -> ParsedRouterResponse:
    """Strictly parse one Router response while retaining valid siblings.

    Top-level and reasoning-object contract violations invalidate the response.
    Individual candidate violations are audited and do not discard valid sibling
    candidates.  The caller uses deterministic fallback only when no selectable
    candidate remains.
    """

    try:
        payload = json.loads(str(raw_response or "").strip())
    except (TypeError, json.JSONDecodeError) as exc:
        raise RouterContractError("invalid_json", "router response is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise RouterContractError("schema_error", "router response must be an object")
    _assert_exact_fields(payload, set(TOP_LEVEL_FIELDS), "router response")
    if payload.get("routing_schema_version") != ROUTING_SCHEMA_VERSION:
        raise RouterContractError("schema_error", "router response uses an unsupported schema version")
    if not isinstance(payload["reasoning_objects"], list):
        raise RouterContractError("schema_error", "reasoning_objects must be an array")
    if not isinstance(payload["operator_candidates"], list):
        raise RouterContractError("schema_error", "operator_candidates must be an array")
    if not isinstance(payload["not_selected_reasons"], list):
        raise RouterContractError("schema_error", "not_selected_reasons must be an array")
    if len(payload["not_selected_reasons"]) > MAX_NOT_SELECTED_REASONS:
        raise RouterContractError("schema_error", "not_selected_reasons exceeds the contract maximum")

    reasoning_objects = [
        _validate_reasoning_object(value, index=index, evidence_source_text=evidence_source_text)
        for index, value in enumerate(payload["reasoning_objects"])
    ]
    not_selected_reasons = [
        _validate_text(
            value,
            label=f"not_selected_reasons[{index}]",
            maximum=MAX_EXPLANATION_CHARS,
        )
        for index, value in enumerate(payload["not_selected_reasons"])
    ]
    router_comment = _validate_text(
        payload["router_comment"],
        label="router_comment",
        maximum=MAX_EXPLANATION_CHARS,
        allow_empty=True,
    )

    eligible = set(eligible_operator_ids)
    valid_candidates: List[Dict[str, Any]] = []
    rejected_candidates: List[Dict[str, Any]] = []
    seen_operator_ids: Set[str] = set()
    for index, raw_candidate in enumerate(payload["operator_candidates"]):
        raw_operator_id = raw_candidate.get("operator_id") if isinstance(raw_candidate, Mapping) else None
        operator_id = _clean(raw_operator_id)
        if operator_id and operator_id in seen_operator_ids:
            rejected_candidates.append(
                {
                    "candidate_index": index,
                    "operator_id": operator_id,
                    "reason": "duplicate_operator_id",
                }
            )
            continue
        if operator_id:
            seen_operator_ids.add(operator_id)
        try:
            candidate = _validate_candidate(
                raw_candidate,
                index=index,
                eligible_operator_ids=eligible,
                adjacent_operator_ids=adjacent_operator_ids,
                evidence_source_text=evidence_source_text,
            )
        except RouterContractError as exc:
            rejected_candidates.append(
                {
                    "candidate_index": index,
                    "operator_id": operator_id or None,
                    "reason": exc.classification,
                    "detail": str(exc),
                }
            )
            continue
        valid_candidates.append(candidate)

    selectable = [
        candidate
        for candidate in valid_candidates
        if candidate["applicability"] in {"applicable", "unknown"}
    ]
    if not selectable:
        raise RouterContractError("no_valid_candidates", "router response contains no selectable valid candidates")
    valid_candidates.sort(key=lambda candidate: (candidate["rank"], candidate["operator_id"]))
    return ParsedRouterResponse(
        routing_schema_version=ROUTING_SCHEMA_VERSION,
        reasoning_objects=reasoning_objects,
        valid_candidates=valid_candidates,
        rejected_candidates=rejected_candidates,
        not_selected_reasons=not_selected_reasons,
        router_comment=router_comment,
    )


def prompt_contract_text() -> str:
    """Render the response rules from the same constants used by the parser."""

    return f"""
输出必须是唯一 JSON 对象，顶层字段必须且只能是：
{sorted(TOP_LEVEL_FIELDS)}

routing_schema_version 必须是 {ROUTING_SCHEMA_VERSION}。
reasoning_objects 的每项字段必须且只能是 {sorted(REASONING_OBJECT_FIELDS)}。
operator_candidates 的每项字段必须且只能是 {sorted(OPERATOR_CANDIDATE_FIELDS)}。
rank 必须为正整数；applicability 只能为 {sorted(APPLICABILITY_VALUES)}；confidence 必须在 0 到 1 之间。
每个 evidence_spans 必须有 {MIN_EVIDENCE_SPANS} 至 {MAX_EVIDENCE_SPANS} 条，每条逐字复制自“样本输入”，且最多 {MAX_EVIDENCE_SPAN_CHARS} 个字符；不得从算子卡片取证。
reasoning_object 最多 {MAX_REASONING_OBJECT_CHARS} 个字符。why_fit、why_not_adjacent 中的说明、router_comment 和 not_selected_reasons 的每项最多 {MAX_EXPLANATION_CHARS} 个字符。
why_not_adjacent 必须是恰好包含一个相邻算子 ID 到说明文字的对象；只能使用卡片声明的相邻算子。
not_selected_reasons 默认 []，最多 {MAX_NOT_SELECTED_REASONS} 项。不要输出额外字段、Markdown 或解释文字。
""".strip()
