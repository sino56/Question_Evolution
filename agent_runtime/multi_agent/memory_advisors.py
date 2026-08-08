"""Safe helpers for offline memory-compilation advisor outputs."""

from __future__ import annotations

from typing import Any, Mapping

MEMORY_DRAFT_STATUSES = {"proposed", "shadow", "needs_human_review", "rejected_insufficient_evidence"}


def build_strategy_card_draft(*, strategy_id: str, evidence_refs: list[Mapping[str, Any]], status: str = "proposed", fields: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build a non-active strategy draft; active publication is impossible here."""

    if status not in MEMORY_DRAFT_STATUSES:
        raise ValueError("memory advisor cannot create an active or unknown strategy state")
    if not evidence_refs:
        status = "needs_human_review"
    return {"strategy_id": strategy_id, "status": status, "evidence_refs": [dict(item) for item in evidence_refs], "applicability": dict(fields or {}), "source": "multi_agent_memory_compilation", "publication": "draft_only"}


def memory_advice(advisor_id: str, context: Mapping) -> dict:
    refs = list(context["evidence_pack_slice"].get("evidence_refs") or [])[:10]
    action = "proposed" if refs else "needs_human_review"
    if advisor_id == "publication_precheck":
        action = "needs_human_review" if refs else "rejected_insufficient_evidence"
    claim = "Any resulting strategy card remains non-active pending independent evidence and approval."
    return {"summary": f"{advisor_id} produced an offline draft only.", "findings": [{"type": "memory_compilation", "severity": "low", "claim": claim, "evidence_refs": refs, "recommended_action": action}] if refs else [], "forbidden_actions_requested": []}
