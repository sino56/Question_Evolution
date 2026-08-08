"""Persist concise, reviewer-friendly Stage-10 budget sidecars."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from .budget_state import BudgetLedger


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")


def write_budget_artifacts(
    run_dir: str | Path,
    *,
    ledger: BudgetLedger,
    proposal: Mapping[str, Any],
    decision: Mapping[str, Any],
) -> Dict[str, Path]:
    root = Path(run_dir)
    root.mkdir(parents=True, exist_ok=True)
    _append_jsonl(root / "budget_reallocation_proposals.jsonl", proposal)
    _append_jsonl(root / "budget_reallocation_decisions.jsonl", decision)
    ledger_path = root / "budget_ledger.json"
    temporary = ledger_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(ledger.as_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(ledger_path)
    report_path = write_budget_report(root, proposal=proposal, decision=decision, ledger=ledger)
    return {"proposal": root / "budget_reallocation_proposals.jsonl", "decision": root / "budget_reallocation_decisions.jsonl", "ledger": ledger_path, "report": report_path}


def budget_report_markdown(proposal: Mapping[str, Any], decision: Mapping[str, Any], ledger: BudgetLedger) -> str:
    lines = [
        "## Dynamic budget reallocation",
        "",
        f"- Proposal: `{proposal.get('proposal_id', 'unknown')}` ({proposal.get('analysis_status', 'unknown')})",
        f"- Validator decision: `{decision.get('status', 'unknown')}`",
        f"- Trigger: {proposal.get('trigger', 'unknown')}",
        f"- Summary: {proposal.get('summary', '')}",
        f"- Remaining hard-budget pool: `{json.dumps(ledger.remaining_by_type(), ensure_ascii=False, sort_keys=True)}`",
        "",
        "| Budget type | Target | Before | After | Reason | Evidence refs |",
        "| --- | --- | ---: | ---: | --- | ---: |",
    ]
    changes = decision.get("approved_changes") or proposal.get("changes") or []
    if not changes:
        lines.append("| — | — | — | — | No automatic change | 0 |")
    else:
        for change in changes:
            if not isinstance(change, Mapping):
                continue
            lines.append(
                "| {kind} | {target} | {before:g} | {after:g} | {reason} | {refs} |".format(
                    kind=str(change.get("budget_type") or ""),
                    target=str(change.get("target") or ""),
                    before=float(change.get("from") or 0),
                    after=float(change.get("to") or 0),
                    reason=str(change.get("reason") or "").replace("|", "/"),
                    refs=len(change.get("evidence_refs") or []),
                )
            )
    if decision.get("rejection_reasons"):
        lines.append("- Rejected because: " + "; ".join(str(item) for item in decision["rejection_reasons"]))
    if decision.get("review_reasons"):
        lines.append("- Human review required: " + "; ".join(str(item) for item in decision["review_reasons"]))
    return "\n".join(lines) + "\n"


def write_budget_report(
    run_dir: str | Path,
    *,
    proposal: Mapping[str, Any],
    decision: Mapping[str, Any],
    ledger: BudgetLedger,
) -> Path:
    target = Path(run_dir) / "budget_reallocation_report.md"
    target.write_text(budget_report_markdown(proposal, decision, ledger), encoding="utf-8")
    return target
