"""Shared contracts for the governed question-evolution pipeline.

This module intentionally contains deterministic, inspectable policy helpers.
It does not decide whether a candidate is *effective*: that remains the
post-scoring effect analysis.  The helpers instead keep authorization,
provenance, execution scope, and version identities consistent between stages.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


GOVERNANCE_VERSION = "flow-governance-v1"
SOURCE_FAITHFUL = "source_faithful"
CONTROLLED_HYPOTHETICAL_CASE = "controlled_hypothetical_case"
CONTROLLED_SYNTHESIS = "controlled_synthesis"
HYPOTHETICAL_ADAPTATION = "hypothetical_adaptation_from_source"
PASS_THROUGH = "pass_through"
ALL_EVOLUTION_MODES = (
    SOURCE_FAITHFUL,
    CONTROLLED_HYPOTHETICAL_CASE,
    CONTROLLED_SYNTHESIS,
    HYPOTHETICAL_ADAPTATION,
)

STRUCTURE_VALIDATION = "structure_validation"
FULL_ITERATION = "full_iteration"

_CLAIM_MARKERS = (
    "因此", "所以", "说明", "表明", "证明", "认定", "确认", "足以", "只能",
    "不能直接", "最高支持", "结论边界", "负责", "属于同一", "排除其他",
)
_RULE_MARKERS = ("本题规定", "本题设定", "规则", "阈值", "公式", "程序")
_REAL_RULE_MARKERS = ("法律", "法规", "行业规定", "标准", "规范", "办法")


def clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def metadata(record: Mapping[str, Any]) -> Dict[str, Any]:
    value = record.get("meta_info")
    return dict(value) if isinstance(value, Mapping) else {}


def evolution_metadata(record: Mapping[str, Any]) -> Dict[str, Any]:
    value = metadata(record).get("question_evolution_metadata")
    return dict(value) if isinstance(value, Mapping) else {}


def _mapping_from_record(record: Mapping[str, Any], key: str) -> Dict[str, Any]:
    for source in (record, metadata(record), evolution_metadata(record)):
        value = source.get(key) if isinstance(source, Mapping) else None
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _string_list(value: Any) -> List[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return list(dict.fromkeys(text for text in (clean_text(v) for v in value) if text))


def resolve_evolution_authorization(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a safe authorization object without treating eligibility as permission.

    Missing configuration preserves source-faithful operation and explicitly
    permits a self-contained hypothetical case.  It never authorizes synthesis
    of real-world facts, rules, professional thresholds, or case conclusions.
    """

    raw = _mapping_from_record(record, "evolution_authorization")
    configured_modes = _string_list(raw.get("allowed_evolution_modes"))
    parseable = bool(raw) and bool(configured_modes)
    if not configured_modes:
        configured_modes = [SOURCE_FAITHFUL, CONTROLLED_HYPOTHETICAL_CASE]
    configured_modes = [mode for mode in configured_modes if mode in ALL_EVOLUTION_MODES]
    if SOURCE_FAITHFUL not in configured_modes:
        configured_modes.insert(0, SOURCE_FAITHFUL)
    if CONTROLLED_HYPOTHETICAL_CASE not in configured_modes:
        configured_modes.append(CONTROLLED_HYPOTHETICAL_CASE)
    source = clean_text(raw.get("authorization_source"))
    authorization_id = clean_text(raw.get("authorization_id"))
    auditable = bool(source and authorization_id)
    return {
        "allowed_evolution_modes": configured_modes,
        "controlled_hypothetical_case_authorized": bool(raw.get("controlled_hypothetical_case_authorized", True)),
        "controlled_synthesis_authorized": bool(raw.get("controlled_synthesis_authorized", False)),
        "hypothetical_adaptation_authorized": bool(raw.get("hypothetical_adaptation_authorized", False)),
        "authorization_source": source or "safe_default",
        "authorization_id": authorization_id or "safe_default_hypothetical_only",
        "authorization_checked": True,
        "authorization_config_parseable": parseable,
        "authorization_source_auditable": auditable,
        "real_external_material_authorized": parseable and auditable,
    }


def _sentences(value: Any) -> List[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?；;])|\n+", clean_text(value)) if part.strip()]


def _source_locator(record: Mapping[str, Any], sentence_index: int) -> Dict[str, Any]:
    info = metadata(record)
    return {
        "source_file": clean_text(info.get("source_file")) or "pipeline_record",
        "source_record_id": clean_text(record.get("sample_id") or record.get("index")) or None,
        "json_pointer": "/prompt",
        "sentence_index": sentence_index,
    }


def analyze_source(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Split source text into observations, claims, rules, and answer direction.

    The original prompt is deliberately split sentence-by-sentence: it is never
    represented as one opaque observation fact.
    """

    prompt = clean_text(record.get("prompt"))
    source_world = clean_text(metadata(record).get("source_world_id")) or (
        "source_case_" + (clean_text(record.get("sample_id") or record.get("index")) or "unknown")
    )
    observations: List[Dict[str, Any]] = []
    claims: List[Dict[str, Any]] = []
    rules: List[Dict[str, Any]] = []
    answer_direction: List[Dict[str, Any]] = []
    for index, sentence in enumerate(_sentences(prompt), start=1):
        lower = sentence.lower()
        is_claim = any(marker in sentence for marker in _CLAIM_MARKERS)
        is_rule = any(marker in sentence for marker in _RULE_MARKERS)
        fact = {
            "fact_id": f"SRC_F{index:03d}",
            "world_id": source_world,
            "global_fact_key": hashlib.sha256(sentence.encode("utf-8")).hexdigest()[:16],
            "text": sentence,
            "origin_type": "source_observation" if not is_claim else "source_claim",
            "source_locator": _source_locator(record, index),
        }
        if is_claim:
            claims.append(fact)
            if any(marker in sentence for marker in ("认定", "确认", "足以", "只能", "不能直接", "最高支持", "结论边界")):
                answer_direction.append(fact)
        else:
            observations.append(fact)
        if is_rule:
            rule = dict(fact)
            rule.update({
                "rule_id": f"SRC_R{index:03d}",
                "rule_text": sentence,
                "version": clean_text(metadata(record).get("rule_version")) or None,
                "applicable_subjects": [],
                "applicable_scenarios": [],
                "validity_status": "unresolved" if any(marker in lower for marker in _REAL_RULE_MARKERS) else "task_local",
            })
            rules.append(rule)
    return {
        "schema_version": GOVERNANCE_VERSION,
        "source_world_id": source_world,
        "source_observation_ledger": observations,
        "source_claim_ledger": claims,
        "rule_ledger": rules,
        "answer_direction_ledger": answer_direction,
        "derived_summary_ledger": [],
        "sample_eligibility": {
            "source_fact_count": len(observations),
            "eligible_for_source_faithful": bool(observations),
            "eligible_for_controlled_hypothetical_case": True,
        },
    }


def resolve_evolution_mode(record: Mapping[str, Any], source_analysis: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    authorization = resolve_evolution_authorization(record)
    source_analysis = dict(source_analysis or _mapping_from_record(record, "source_analysis") or analyze_source(record))
    existing = _mapping_from_record(record, "mode_decision")
    requested = clean_text(existing.get("evolution_mode"))
    observations = source_analysis.get("source_observation_ledger")
    observation_count = len(observations) if isinstance(observations, list) else 0
    allowed = set(authorization["allowed_evolution_modes"])
    if requested in allowed:
        mode = requested
        reason = "preserved explicit mode decision"
    elif observation_count >= 2 and SOURCE_FAITHFUL in allowed:
        mode = SOURCE_FAITHFUL
        reason = "sufficient source observations support a source-faithful rewrite"
    elif CONTROLLED_HYPOTHETICAL_CASE in allowed and authorization["controlled_hypothetical_case_authorized"]:
        mode = CONTROLLED_HYPOTHETICAL_CASE
        reason = "abstract or information-sparse source uses an explicitly marked in-question hypothetical case"
    else:
        mode = PASS_THROUGH
        reason = "no authorized evolution mode is safely available"
    if mode == CONTROLLED_SYNTHESIS and not authorization["controlled_synthesis_authorized"]:
        mode, reason = CONTROLLED_HYPOTHETICAL_CASE, "controlled synthesis is not authorized; use a self-contained hypothetical case"
    if mode == HYPOTHETICAL_ADAPTATION and not authorization["hypothetical_adaptation_authorized"]:
        mode, reason = CONTROLLED_HYPOTHETICAL_CASE, "source adaptation is not authorized; use a self-contained hypothetical case"
    return {
        "evolution_mode": mode,
        "mode_reason": reason,
        "authorization_checked": authorization["authorization_checked"],
        "authorization_id": authorization["authorization_id"],
        "authorization": authorization,
        "source_analysis_version": source_analysis.get("schema_version", GOVERNANCE_VERSION),
    }


def public_fact_projection(source_analysis: Mapping[str, Any], mode_decision: Mapping[str, Any]) -> Dict[str, Any]:
    mode = clean_text(mode_decision.get("evolution_mode"))
    facts = source_analysis.get("source_observation_ledger")
    public_facts = [dict(value) for value in facts if isinstance(value, Mapping)] if isinstance(facts, list) else []
    rules = source_analysis.get("rule_ledger")
    public_rules = [
        dict(value) for value in rules if isinstance(value, Mapping)
        and clean_text(value.get("validity_status")) in {"current", "task_local"}
    ] if isinstance(rules, list) else []
    return {
        "projection_version": GOVERNANCE_VERSION,
        "world_id": clean_text(source_analysis.get("source_world_id")),
        "evolution_mode": mode,
        "public_fact_ledger": public_facts,
        "public_rule_ledger": public_rules,
        "writer_task": "Write one neutral, self-contained question using only these public facts or explicitly registered in-question hypothetical observations.",
    }


def writer_context(record: Mapping[str, Any]) -> Dict[str, Any]:
    analysis = _mapping_from_record(record, "source_analysis") or analyze_source(record)
    decision = _mapping_from_record(record, "mode_decision") or resolve_evolution_mode(record, analysis)
    projection = _mapping_from_record(record, "public_fact_projection") or public_fact_projection(analysis, decision)
    return {
        "evolution_mode": decision["evolution_mode"],
        "public_fact_projection": projection,
        "context_policy": "No reference answer, old rubric, old score, hidden plan, fact role, target error, or scoring intent is available to the writer.",
    }


def question_version(prompt: Any) -> str:
    return "qv_" + hashlib.sha256(clean_text(prompt).encode("utf-8")).hexdigest()[:16]


def mark_stale_scoring_material(record: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(record)
    info = metadata(result)
    current_version = question_version(result.get("prompt"))
    info["active_scoring_state"] = "not_evaluated"
    info["question_version"] = current_version
    info["stale_material_version"] = info.get("parent_question_version") or None
    result["meta_info"] = info
    result.pop("score_rate", None)
    return result


def resolve_execution_scope(record: Mapping[str, Any]) -> Dict[str, Any]:
    raw = _mapping_from_record(record, "execution_scope")
    if not raw:
        return {
            "max_stage": STRUCTURE_VALIDATION,
            "allow_reference_rebuild": False,
            "allow_model_answering": False,
            "allow_judge_scoring": False,
            "allow_effect_claim": False,
            "source": "safe_default",
        }
    scope = {
        "max_stage": clean_text(raw.get("max_stage")) or STRUCTURE_VALIDATION,
        "allow_reference_rebuild": bool(raw.get("allow_reference_rebuild", False)),
        "allow_model_answering": bool(raw.get("allow_model_answering", False)),
        "allow_judge_scoring": bool(raw.get("allow_judge_scoring", False)),
        "allow_effect_claim": bool(raw.get("allow_effect_claim", False)),
        "source": clean_text(raw.get("source")) or "record_config",
    }
    return scope


def scope_allows(record: Mapping[str, Any], capability: str) -> bool:
    scope = resolve_execution_scope(record)
    if capability == "reference_rebuild":
        return bool(scope["allow_reference_rebuild"])
    if capability == "model_answering":
        return bool(scope["allow_model_answering"])
    if capability == "judge_scoring":
        return bool(scope["allow_judge_scoring"])
    if capability == "effect_claim":
        return bool(scope["allow_effect_claim"])
    raise ValueError(f"unknown execution-scope capability: {capability}")


def is_full_iteration_scope(record: Mapping[str, Any]) -> bool:
    scope = resolve_execution_scope(record)
    return scope["max_stage"] == FULL_ITERATION and all(
        bool(scope[key]) for key in ("allow_reference_rebuild", "allow_model_answering", "allow_judge_scoring", "allow_effect_claim")
    )


def technical_execution_block(validation: Mapping[str, Any]) -> bool:
    return clean_text(validation.get("disposition_status")) == "technical_block" or clean_text(validation.get("invalid_type")) in {
        "empty_prompt", "schema_error", "non_sendable_question",
    }


def validation_disposition(validation: Mapping[str, Any]) -> Dict[str, Any]:
    if technical_execution_block(validation):
        status, action = "technical_block", "pass_through_candidate"
    elif validation.get("passed") is False:
        status, action = "retry_same_operator", "retry_same_operator"
    elif clean_text(validation.get("semantic_economy_risk")) == "high":
        status, action = "exploration_candidate", "exploration_candidate"
    else:
        status, action = "record_only_risk", "record_only_risk"
    return {
        "status": status,
        "suggested_action": action,
        "risk_tags": list(validation.get("risk_tags") or []),
        "evidence": list(validation.get("diagnostic_evidence") or []),
        "blocking": status == "technical_block",
    }
