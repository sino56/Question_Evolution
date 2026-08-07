"""Shared schema and validation rules for the hybrid LLM operator router.

The Router prompt, parser, cache, and tests import this module instead of
maintaining parallel copies of the response contract.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Set


ROUTING_SCHEMA_VERSION = "hybrid-router-v3"
ROUTER_PROMPT_VERSION = "hybrid-router-prompt-v3"
ROUTER_TRANSPORT_POLICY_VERSION = "router-transport-v1"
ROUTER_REGISTRY_POLICY_VERSION = "eligible-operators-v2-mode-aware"
ROUTE_REVISION = "hybrid-mode-aware-no-default-fallback-v3"

TOP_LEVEL_FIELDS = frozenset(
    {
        "routing_schema_version",
        "reasoning_objects",
        "operator_candidates",
        "operator_decision_audit",
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
OPERATOR_DECISION_AUDIT_FIELDS = frozenset(
    {
        "selected_operator_rationales",
        "not_selected_operator_rationales",
        "uncertain_operator_rationales",
        "operator_improvement_notes",
    }
)
SELECTED_OPERATOR_RATIONALE_FIELDS = frozenset(
    {
        "operator_id",
        "matched_failure_mechanism",
        "satisfied_hard_slots",
        "no_fabricated_facts",
    }
)
NOT_SELECTED_OPERATOR_RATIONALE_FIELDS = frozenset(
    {"operator_id", "reason", "nearer_selected_operator_id"}
)
UNCERTAIN_OPERATOR_RATIONALE_FIELDS = frozenset(
    {"operator_id", "missing_hard_slots", "would_need_fabricated_facts"}
)

# A candidate is an execution request.  Uncertain and not-applicable directions
# belong exclusively in operator_decision_audit, never in this list.
APPLICABILITY_VALUES = frozenset({"applicable"})
MAX_EVIDENCE_SPANS = 2
MIN_EVIDENCE_SPANS = 1
MAX_EVIDENCE_SPAN_CHARS = 240
MAX_REASONING_OBJECT_CHARS = 120
MAX_EXPLANATION_CHARS = 180
MAX_NOT_SELECTED_REASONS = 1
MAX_AUDIT_RATIONALES_PER_GROUP = 12
MAX_AUDIT_SLOTS_PER_RATIONALE = 8
MAX_AUDIT_SLOT_CHARS = 120
MAX_AUDIT_IMPROVEMENT_NOTES = 8


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
    operator_decision_audit: Dict[str, Any]
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


def _validate_audit_slot_list(value: Any, *, label: str, allow_empty: bool = False) -> List[str]:
    if not isinstance(value, list) or len(value) > MAX_AUDIT_SLOTS_PER_RATIONALE:
        raise RouterContractError("schema_error", f"{label} must be an array with at most {MAX_AUDIT_SLOTS_PER_RATIONALE} items")
    if not allow_empty and not value:
        raise RouterContractError("schema_error", f"{label} must not be empty")
    slots = [
        _validate_text(
            raw_slot,
            label=f"{label}[{index}]",
            maximum=MAX_AUDIT_SLOT_CHARS,
        )
        for index, raw_slot in enumerate(value)
    ]
    if len(set(slots)) != len(slots):
        raise RouterContractError("schema_error", f"{label} must not contain duplicate entries")
    return slots


def _validate_audit_operator_id(
    value: Any,
    *,
    label: str,
    eligible_operator_ids: Set[str],
) -> str:
    operator_id = _validate_text(value, label=label, maximum=200)
    if operator_id not in eligible_operator_ids:
        raise RouterContractError("ineligible_operator", f"{label} is not eligible: {operator_id}")
    return operator_id


def _validate_operator_decision_audit(
    value: Any,
    *,
    eligible_operator_ids: Set[str],
) -> Dict[str, Any]:
    """Validate audit-only route explanations without making them executable."""

    if not isinstance(value, Mapping):
        raise RouterContractError("schema_error", "operator_decision_audit must be an object")
    _assert_exact_fields(value, set(OPERATOR_DECISION_AUDIT_FIELDS), "operator_decision_audit")

    groups = (
        "selected_operator_rationales",
        "not_selected_operator_rationales",
        "uncertain_operator_rationales",
    )
    for group in groups:
        if not isinstance(value[group], list) or len(value[group]) > MAX_AUDIT_RATIONALES_PER_GROUP:
            raise RouterContractError(
                "schema_error",
                f"operator_decision_audit.{group} must be an array with at most {MAX_AUDIT_RATIONALES_PER_GROUP} items",
            )
    if (
        not isinstance(value["operator_improvement_notes"], list)
        or len(value["operator_improvement_notes"]) > MAX_AUDIT_IMPROVEMENT_NOTES
    ):
        raise RouterContractError(
            "schema_error",
            f"operator_decision_audit.operator_improvement_notes must be an array with at most {MAX_AUDIT_IMPROVEMENT_NOTES} items",
        )

    selected: List[Dict[str, Any]] = []
    for index, raw_rationale in enumerate(value["selected_operator_rationales"]):
        label = f"operator_decision_audit.selected_operator_rationales[{index}]"
        if not isinstance(raw_rationale, Mapping):
            raise RouterContractError("schema_error", f"{label} must be an object")
        _assert_exact_fields(raw_rationale, set(SELECTED_OPERATOR_RATIONALE_FIELDS), label)
        if raw_rationale["no_fabricated_facts"] is not True:
            raise RouterContractError("schema_error", f"{label}.no_fabricated_facts must be true")
        selected.append(
            {
                "operator_id": _validate_audit_operator_id(
                    raw_rationale["operator_id"],
                    label=f"{label}.operator_id",
                    eligible_operator_ids=eligible_operator_ids,
                ),
                "matched_failure_mechanism": _validate_text(
                    raw_rationale["matched_failure_mechanism"],
                    label=f"{label}.matched_failure_mechanism",
                    maximum=MAX_EXPLANATION_CHARS,
                ),
                "satisfied_hard_slots": _validate_audit_slot_list(
                    raw_rationale["satisfied_hard_slots"],
                    label=f"{label}.satisfied_hard_slots",
                ),
                "no_fabricated_facts": True,
            }
        )

    not_selected: List[Dict[str, Any]] = []
    for index, raw_rationale in enumerate(value["not_selected_operator_rationales"]):
        label = f"operator_decision_audit.not_selected_operator_rationales[{index}]"
        if not isinstance(raw_rationale, Mapping):
            raise RouterContractError("schema_error", f"{label} must be an object")
        _assert_exact_fields(raw_rationale, set(NOT_SELECTED_OPERATOR_RATIONALE_FIELDS), label)
        raw_nearer = raw_rationale["nearer_selected_operator_id"]
        if raw_nearer is not None and not isinstance(raw_nearer, str):
            raise RouterContractError("schema_error", f"{label}.nearer_selected_operator_id must be a string or null")
        nearer_selected_operator_id = (
            _validate_audit_operator_id(
                raw_nearer,
                label=f"{label}.nearer_selected_operator_id",
                eligible_operator_ids=eligible_operator_ids,
            )
            if raw_nearer is not None
            else None
        )
        not_selected.append(
            {
                "operator_id": _validate_audit_operator_id(
                    raw_rationale["operator_id"],
                    label=f"{label}.operator_id",
                    eligible_operator_ids=eligible_operator_ids,
                ),
                "reason": _validate_text(
                    raw_rationale["reason"],
                    label=f"{label}.reason",
                    maximum=MAX_EXPLANATION_CHARS,
                ),
                "nearer_selected_operator_id": nearer_selected_operator_id,
            }
        )

    uncertain: List[Dict[str, Any]] = []
    for index, raw_rationale in enumerate(value["uncertain_operator_rationales"]):
        label = f"operator_decision_audit.uncertain_operator_rationales[{index}]"
        if not isinstance(raw_rationale, Mapping):
            raise RouterContractError("schema_error", f"{label} must be an object")
        _assert_exact_fields(raw_rationale, set(UNCERTAIN_OPERATOR_RATIONALE_FIELDS), label)
        uncertain.append(
            {
                "operator_id": _validate_audit_operator_id(
                    raw_rationale["operator_id"],
                    label=f"{label}.operator_id",
                    eligible_operator_ids=eligible_operator_ids,
                ),
                "missing_hard_slots": _validate_audit_slot_list(
                    raw_rationale["missing_hard_slots"],
                    label=f"{label}.missing_hard_slots",
                ),
                "would_need_fabricated_facts": _validate_text(
                    raw_rationale["would_need_fabricated_facts"],
                    label=f"{label}.would_need_fabricated_facts",
                    maximum=MAX_EXPLANATION_CHARS,
                ),
            }
        )

    all_operator_ids = [
        *(entry["operator_id"] for entry in selected),
        *(entry["operator_id"] for entry in not_selected),
        *(entry["operator_id"] for entry in uncertain),
    ]
    if len(set(all_operator_ids)) != len(all_operator_ids):
        raise RouterContractError(
            "schema_error",
            "an operator may appear in only one operator_decision_audit rationale group",
        )
    return {
        "selected_operator_rationales": selected,
        "not_selected_operator_rationales": not_selected,
        "uncertain_operator_rationales": uncertain,
        "operator_improvement_notes": [
            _validate_text(
                note,
                label=f"operator_decision_audit.operator_improvement_notes[{index}]",
                maximum=MAX_EXPLANATION_CHARS,
            )
            for index, note in enumerate(value["operator_improvement_notes"])
        ],
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
    candidates.  A syntactically valid empty candidate list is an intentional
    no-branch decision; deterministic fallback is reserved for a non-empty
    list whose entries all fail validation or for a top-level contract failure.
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
    eligible = set(eligible_operator_ids)
    audit = _validate_operator_decision_audit(
        payload["operator_decision_audit"],
        eligible_operator_ids=eligible,
    )
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

    valid_candidates.sort(key=lambda candidate: (candidate["rank"], candidate["operator_id"]))
    selected_audits = {
        entry["operator_id"]: entry
        for entry in audit["selected_operator_rationales"]
    }
    audited_candidates: List[Dict[str, Any]] = []
    for candidate in valid_candidates:
        if candidate["operator_id"] not in selected_audits:
            rejected_candidates.append(
                {
                    "operator_id": candidate["operator_id"],
                    "reason": "missing_selected_operator_rationale",
                }
            )
            continue
        audited_candidates.append(candidate)

    # A well-formed empty list is an intentional no-branch decision.  A
    # non-empty list whose entries all violate the contract (including missing
    # audit bases) remains an error and follows deterministic fallback.
    if payload["operator_candidates"] and not audited_candidates:
        raise RouterContractError("no_valid_candidates", "router response contains no selectable valid candidates")
    if not payload["operator_candidates"] and selected_audits:
        raise RouterContractError(
            "schema_error",
            "selected_operator_rationales must be empty when operator_candidates is empty",
        )

    valid_candidates = audited_candidates
    selected_ids = {candidate["operator_id"] for candidate in valid_candidates}
    audit["selected_operator_rationales"] = [
        selected_audits[candidate["operator_id"]]
        for candidate in valid_candidates
    ]
    normalized_not_selected: List[Dict[str, Any]] = []
    for rationale in audit["not_selected_operator_rationales"]:
        nearer = rationale["nearer_selected_operator_id"]
        # A malformed sibling must not cause a valid selected candidate to be
        # discarded.  Preserve the exclusion reason but remove an adjacency
        # reference whose intended selected sibling was rejected by contract.
        normalized_not_selected.append(
            {
                **rationale,
                "nearer_selected_operator_id": nearer if nearer in selected_ids else None,
            }
        )
    audit["not_selected_operator_rationales"] = normalized_not_selected
    return ParsedRouterResponse(
        routing_schema_version=ROUTING_SCHEMA_VERSION,
        reasoning_objects=reasoning_objects,
        valid_candidates=valid_candidates,
        rejected_candidates=rejected_candidates,
        operator_decision_audit=audit,
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
operator_decision_audit 的字段必须且只能是 {sorted(OPERATOR_DECISION_AUDIT_FIELDS)}。其中三个 rationale 数组每个最多 {MAX_AUDIT_RATIONALES_PER_GROUP} 项；operator_improvement_notes 最多 {MAX_AUDIT_IMPROVEMENT_NOTES} 项。
selected_operator_rationales 的每项字段必须且只能是 {sorted(SELECTED_OPERATOR_RATIONALE_FIELDS)}，且 no_fabricated_facts 必须为 true；not_selected_operator_rationales 的每项字段必须且只能是 {sorted(NOT_SELECTED_OPERATOR_RATIONALE_FIELDS)}；uncertain_operator_rationales 的每项字段必须且只能是 {sorted(UNCERTAIN_OPERATOR_RATIONALE_FIELDS)}。
rank 必须为正整数；applicability 只能为 {sorted(APPLICABILITY_VALUES)}；confidence 必须在 0 到 1 之间。
每个 evidence_spans 必须有 {MIN_EVIDENCE_SPANS} 至 {MAX_EVIDENCE_SPANS} 条，每条逐字复制自“样本输入”，且最多 {MAX_EVIDENCE_SPAN_CHARS} 个字符；不得从算子卡片取证。
reasoning_object 最多 {MAX_REASONING_OBJECT_CHARS} 个字符。why_fit、why_not_adjacent 中的说明、router_comment 和 not_selected_reasons 的每项最多 {MAX_EXPLANATION_CHARS} 个字符。
why_not_adjacent 必须是恰好包含一个相邻算子 ID 到说明文字的对象；只能使用卡片声明的相邻算子。
每个通过候选契约校验的 operator_candidate 都必须有同 operator_id 的 selected_operator_rationale；在最终解析结果中二者按 rank 顺序对应。operator_candidates 可以为空，表示没有算子可在不补造事实的前提下执行。uncertain 或 not_selected 理由绝不能放入 operator_candidates。
not_selected_reasons 默认 []，最多 {MAX_NOT_SELECTED_REASONS} 项；本轮详细审计只写入 operator_decision_audit。不要输出额外字段、Markdown 或解释文字。
""".strip()
