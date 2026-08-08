"""Human-review prechecks that never certify an effective boundary."""

from __future__ import annotations

from typing import Mapping


def human_review_advice(advisor_id: str, context: Mapping) -> dict:
    refs = list(context["evidence_pack_slice"].get("evidence_refs") or [])[:5]
    return {"summary": f"{advisor_id} prepared a review aid; it is not a confirmation.", "findings": [{"type": "human_review_precheck", "severity": "medium", "claim": "Human review is required before treating any candidate as a confirmed effective boundary.", "evidence_refs": refs, "recommended_action": "needs_human_review"}] if refs else [], "forbidden_actions_requested": []}


def synthesize_prechecks(prechecks: list[Mapping], context: Mapping) -> dict:
    refs = list(context["evidence_pack_slice"].get("evidence_refs") or [])[:10]
    failed = [item.get("advisor_id") for item in prechecks if item.get("status") != "completed"]
    return {"summary": "Human-review prechecks were combined into a prioritised review aid; no candidate is confirmed.", "findings": [{"type": "review_priority", "severity": "high" if failed else "medium", "claim": "Review the combined evidence before a boundary or policy decision." + (f" Missing prechecks: {', '.join(str(item) for item in failed)}." if failed else ""), "evidence_refs": refs, "recommended_action": "needs_human_review"}] if refs else [], "forbidden_actions_requested": []}
