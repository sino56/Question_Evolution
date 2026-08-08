"""Deterministic first-version post-experiment review advisors.

These adapters make bounded evidence claims locally.  Deployments may replace
them with a model adapter, but the output contract and policy checks remain.
"""

from __future__ import annotations

from typing import Any, Mapping


def review_advice(advisor_id: str, context: Mapping[str, Any]) -> dict[str, Any]:
    allowed = context["evidence_pack_slice"].get("allowed_inputs") or {}
    summary = next(iter(allowed.values()), {})
    if not isinstance(summary, Mapping):
        summary = {}
    counts = summary.get("status_counts") or summary.get("summary", {}).get("status_counts") or {}
    refs = list(context["evidence_pack_slice"].get("evidence_refs") or [])[:3]
    rules = {
        "router_diagnosis": ("router_risk", "not_applicable", "needs_human_review", "Repeated not_applicable outcomes may indicate an overly broad routing condition."),
        "operator_generation_diagnosis": ("generation_risk", "validation_failed", "needs_human_review", "Invalid candidates may indicate an operator-generation or sample-fit risk."),
        "validation_diagnosis": ("validation_risk", "validation_failed", "needs_human_review", "Validation failures require review; this advisor does not override them."),
        "scoring_stability": ("scoring_risk", "score_increased", "needs_human_review", "score_increased requires stability review and remains negative evidence."),
        "search_cost": ("search_cost_risk", "branch_error", "investigate", "Branch errors or low-yield search require a cost review."),
    }
    finding_type, metric, action, claim = rules.get(advisor_id, ("review_note", "", "report_only", "No specialized deterministic finding."))
    count = int(counts.get(metric, 0) or 0)
    findings: list[dict[str, Any]] = []
    if count:
        findings.append({"type": finding_type, "severity": "medium", "claim": f"{claim} Observed count: {count}.", "evidence_refs": refs, "recommended_action": action})
    return {"summary": f"{advisor_id} completed with {len(findings)} evidence-backed finding(s).", "findings": findings, "forbidden_actions_requested": []}
