"""Merge advisor advice conservatively; advice remains non-executable."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from .advisor_events import append_advisor_event

FORBIDDEN_ACTION = re.compile(r"(?:modify|write|delete|publish.*active|run[_ ]?loop|resume|spawn|execute|change).*(?:prompt|operator|router|score|rubric|memory|artifact|advisor|pipeline)|(?:prompt|operator|router|score|rubric|memory|artifact|advisor|pipeline).*?(?:modify|write|delete|publish|run|spawn|execute|change)", re.I)
SAFE_ACTIONS = {"report_only", "needs_human_review", "proposed", "shadow", "rejected_insufficient_evidence", "investigate"}


def _action_is_forbidden(action: str) -> bool:
    return bool(FORBIDDEN_ACTION.search(action)) or action.strip().lower() not in SAFE_ACTIONS


def _bounded(advice: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(advice)
    summary = str(value.get("summary") or "")
    findings = list(value.get("findings") or [])
    truncated = len(summary) > 800 or len(findings) > 20
    value["summary"] = summary[:800]
    value["findings"] = [dict(item) for item in findings[:20] if isinstance(item, Mapping)]
    if truncated:
        value["truncated_by_merger"] = True
    return value


def merge_advice(
    run_dir: str | Path,
    *,
    advice_items: Sequence[Mapping[str, Any]],
    evidence_pack: Mapping[str, Any],
) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    expected_hash = evidence_pack.get("evidence_pack_hash")
    expected_snapshots = dict(evidence_pack.get("snapshot_ids") or {})
    seen: dict[str, tuple[str, str]] = {}
    for raw in advice_items:
        item = _bounded(raw)
        advisor_id = str(item.get("advisor_id") or "unknown")
        if item.get("input_hash") != expected_hash or dict(item.get("snapshot_ids") or {}) != expected_snapshots:
            rejected.append({"advisor_id": advisor_id, "reason": "input_hash_or_snapshot_mismatch"})
            append_advisor_event(run_dir, "advisor_policy_rejected", {"advisor_id": advisor_id, "reason": "input_hash_or_snapshot_mismatch"})
            continue
        prohibited = list(item.get("forbidden_actions_requested") or [])
        for finding in item.get("findings") or []:
            action = str(finding.get("recommended_action") or "")
            if not finding.get("evidence_refs"):
                finding["recommended_action"] = "needs_human_review"
                finding["evidence_missing"] = True
            elif _action_is_forbidden(action):
                prohibited.append(action)
        if prohibited:
            rejected.append({"advisor_id": advisor_id, "reason": "policy_rejected", "actions": prohibited})
            append_advisor_event(run_dir, "advisor_policy_rejected", {"advisor_id": advisor_id, "reason": "policy_rejected", "actions": prohibited})
            continue
        for finding in item.get("findings") or []:
            key = str(finding.get("type") or finding.get("claim") or "")
            action = str(finding.get("recommended_action") or "")
            prior = seen.get(key)
            if prior and prior[1] != action:
                conflicts.append({"finding_key": key, "advisor_ids": [prior[0], advisor_id], "actions": [prior[1], action], "resolution": "needs_human_review"})
            else:
                seen[key] = (advisor_id, action)
        accepted.append(item)
    merged = {
        "status": "completed",
        "evidence_pack_hash": expected_hash,
        "snapshot_ids": expected_snapshots,
        "accepted_advice": accepted,
        "policy_rejections": rejected,
        "conflicts": conflicts,
        "advisory_only": True,
    }
    root = Path(run_dir) / "multi_agent"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "advice_merge.json"
    path.write_text(json.dumps(merged, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    append_advisor_event(run_dir, "advice_merged", {"accepted_count": len(accepted), "rejected_count": len(rejected), "conflict_count": len(conflicts), "evidence_pack_hash": expected_hash})
    return merged
