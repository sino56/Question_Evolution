"""Strictly slice advisor inputs and produce cache-safe context blocks."""

from __future__ import annotations

from typing import Any, Mapping

from .advisor_registry import AdvisorSpec
from .evidence_pack import redact, stable_hash

STATIC_PREFIX_VERSION = "advisor-static-prefix-v1"
TOOL_WHITELIST_VERSION = "advisor-tool-whitelist-v1"


def _lookup(pack: Mapping[str, Any], dotted: str) -> Any:
    current: Any = pack
    for part in dotted.removeprefix("evidence_pack.").split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def build_evidence_slice(spec: AdvisorSpec, evidence_pack: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {item: _lookup(evidence_pack, item) for item in spec.allowed_inputs}
    result = {
        "advisor_id": spec.advisor_id,
        "snapshot_ids": dict(evidence_pack.get("snapshot_ids") or {}),
        "allowed_inputs": redact(allowed),
        "evidence_refs": sorted(redact(list(evidence_pack.get("evidence_refs") or [])), key=stable_hash),
    }
    # Preserve the most decision-relevant material when the supplied evidence
    # exceeds the registered context budget; raw artifacts are always refs.
    import json
    if len(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str)) > spec.max_input_chars:
        result["allowed_inputs"] = {"evidence_pack.summary": redact(_lookup(evidence_pack, "evidence_pack.summary"))}
        result["evidence_refs"] = result["evidence_refs"][:10]
        result["input_truncated"] = True
    result["evidence_pack_slice_hash"] = stable_hash(result)
    return result


def build_advisor_context(
    spec: AdvisorSpec,
    evidence_pack: Mapping[str, Any],
    *,
    dynamic_instruction: str = "",
    parent_advisor_task_id: str | None = None,
    mode: str = "spawn",
) -> dict[str, Any]:
    if mode not in {"spawn", "continue"}:
        raise ValueError("advisor mode must be spawn or continue")
    # Validators and other review work may not inherit a generation context.
    if spec.stage in {"human_review_precheck", "post_experiment_review"} and mode == "continue":
        raise ValueError("review and validation advisors must use a new context")
    if mode == "continue" and not parent_advisor_task_id:
        raise ValueError("continue requires parent_advisor_task_id")
    evidence_slice = build_evidence_slice(spec, evidence_pack)
    static_prefix = {
        "version": STATIC_PREFIX_VERSION,
        "role": "read-only Question Evolution advisor",
        "prohibitions": ["no formal artifact mutations", "no model self-selection", "no advisor spawning", "no direct execution conclusions"],
        "advice_schema": "advisor_advice.schema.json",
    }
    spec_context = {"advisor_spec": spec.as_dict(), "tool_whitelist_version": TOOL_WHITELIST_VERSION}
    dynamic = {"instruction": str(dynamic_instruction)[:4000], "mode": mode, "parent_advisor_task_id": parent_advisor_task_id}
    cache_payload = {
        "static_prefix_version": STATIC_PREFIX_VERSION,
        "advisor_spec_version": spec.version,
        "tools": list(spec.allowed_tools),
        "tool_whitelist_version": TOOL_WHITELIST_VERSION,
        "snapshot_ids": evidence_slice["snapshot_ids"],
        "evidence_pack_slice_hash": evidence_slice["evidence_pack_slice_hash"],
        "model_router_policy_version": "advisor-model-router-v1",
    }
    advisor_context_cache = {
        "advisor_static_prefix_hash": stable_hash(static_prefix),
        "advisor_spec_context_hash": stable_hash(spec_context),
        "evidence_pack_slice_hash": evidence_slice["evidence_pack_slice_hash"],
        "advisor_dynamic_instruction_hash": stable_hash(dynamic),
        "context_cache_key": stable_hash(cache_payload),
    }
    return {
        "advisor_static_prefix": static_prefix,
        "advisor_spec_context": spec_context,
        "evidence_pack_slice": evidence_slice,
        "advisor_dynamic_instruction": dynamic,
        "advisor_context_cache": advisor_context_cache,
        "input_hash": stable_hash({"spec": spec_context, "slice": evidence_slice, "dynamic": dynamic}),
        "context_cache_key": advisor_context_cache["context_cache_key"],
    }
