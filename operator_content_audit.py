"""Offline-only content-risk statistics for operator qualification.

The audit is intentionally not imported by routing, generation, validation, or
candidate selection.  Its output helps select forced-qualification and manual
review samples; it must never become an online hard gate.
"""

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List


RISK_PATTERNS = {
    "answer_direction": (
        r"不能直接认定",
        r"最高(?:只能)?支持",
        r"为什么不成立",
        r"不足以(?:确认|认定)",
    ),
    "fact_role_disclosure": (r"关键证据", r"决定性事实", r"干扰事实", r"必要事实"),
    "rule_application_disclosure": (r"(?:已经|已)满足.{0,12}规则", r"规则不适用"),
    "competitor_elimination": (r"(?:另一|其他).{0,12}(?:无法成立|已被排除)",),
    "chain_disclosure": (r"缺少第.?跳", r"链条不闭合", r"完整链条"),
    "format_pseudo_difficulty": (r"(?:逐项说明|分别列出|固定标签|表格矩阵)",),
}

# These checks are collected only from offline fixture replay or reviewer
# annotations.  They are deliberately optional so historical JSONL records
# remain readable; an absent annotation is not an online failure.
ADVISORY_CHECK_ALIASES = {
    "slot_shortage_or_negative_case": ("slot_shortage_or_negative_case", "slot_sufficiency"),
    "illegal_synthesis": ("illegal_synthesis", "fact_source_trace", "world_consistency", "illegal_rule_check"),
    "adjacent_operator_drift": ("adjacent_operator_drift",),
    "decisive_fact_ablation": ("decisive_fact_ablation",),
    "irrelevant_fact_ablation": ("irrelevant_fact_ablation",),
    "name_or_order_swap": ("name_or_order_swap",),
    "information_balance": ("information_balance",),
}

_PASS_STATUSES = {"passed", "pass", "ok", "true"}
_FAIL_STATUSES = {"failed", "fail", "false"}
_UNRESOLVED_STATUSES = {"unresolved", "needs_review", "not_applicable"}


def detect_surface_risks(prompt: str) -> List[str]:
    """Return observable wording risks; no candidate disposition is produced."""
    text = str(prompt or "")
    return [
        label
        for label, patterns in RISK_PATTERNS.items()
        if any(re.search(pattern, text) for pattern in patterns)
    ]


def _content_check_annotations(record: Dict[str, Any]) -> Dict[str, Any]:
    """Read optional offline annotations without relying on a new top-level field."""
    metadata = record.get("meta_info", {}).get("question_evolution_metadata", {})
    annotations: Dict[str, Any] = {}
    for source in (
        metadata.get("operator_content_checks"),
        record.get("operator_content_checks"),
        record.get("content_audit"),
    ):
        if isinstance(source, dict):
            annotations.update(source)
    return annotations


def _normalise_status(value: Any) -> str:
    if value is True:
        return "passed"
    if value is False:
        return "failed"
    normalized = str(value or "").strip().lower()
    if normalized in _PASS_STATUSES:
        return "passed"
    if normalized in _FAIL_STATUSES:
        return "failed"
    if normalized in _UNRESOLVED_STATUSES:
        return "unresolved"
    return "not_reported"


def _advisory_check_statuses(record: Dict[str, Any]) -> Dict[str, str]:
    annotations = _content_check_annotations(record)
    statuses = {}
    for check_name, aliases in ADVISORY_CHECK_ALIASES.items():
        values = [annotations[alias] for alias in aliases if alias in annotations]
        # A failed sub-check remains failed even if another alias passed.  This
        # is important for O14's three source-closure sub-checks.
        normalized = [_normalise_status(value) for value in values]
        if "failed" in normalized:
            statuses[check_name] = "failed"
        elif "unresolved" in normalized:
            statuses[check_name] = "unresolved"
        elif "passed" in normalized:
            statuses[check_name] = "passed"
        else:
            statuses[check_name] = "not_reported"
    return statuses


def build_risk_report(records: Iterable[Dict[str, Any]], *, minimum_samples: int = 20) -> Dict[str, Any]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for record in records:
        operator_id = str(
            record.get("candidate_operator")
            or record.get("operator_used")
            or record.get("meta_info", {}).get("question_evolution_metadata", {}).get("operator_used")
            or "unassigned"
        )
        grouped[operator_id].append(record)

    by_operator = {}
    for operator_id, operator_records in sorted(grouped.items()):
        risks = Counter()
        check_status_counts = {
            check_name: Counter() for check_name in ADVISORY_CHECK_ALIASES
        }
        for record in operator_records:
            risks.update(detect_surface_risks(str(record.get("prompt", ""))))
            for check_name, status in _advisory_check_statuses(record).items():
                check_status_counts[check_name][status] += 1
                if status in {"failed", "unresolved"}:
                    risks[f"{check_name}_{status}"] += 1
        sample_count = len(operator_records)
        if sample_count < minimum_samples:
            recommendation = "continue_forced_qualification"
        elif risks:
            recommendation = "prioritize_manual_review_and_prompt_optimization"
        else:
            recommendation = "eligible_for_gray_release_review"
        by_operator[operator_id] = {
            "sample_count": sample_count,
            "risk_counts": dict(sorted(risks.items())),
            "risk_rate": round(sum(risks.values()) / sample_count, 4) if sample_count else 0.0,
            "check_status_counts": {
                check_name: dict(sorted(counts.items()))
                for check_name, counts in sorted(check_status_counts.items())
            },
            "gray_release_recommendation": recommendation,
        }

    return {
        "report_kind": "offline_operator_content_risk_statistics",
        "minimum_samples_guidance": minimum_samples,
        "online_disposition": "none",
        "by_operator": by_operator,
    }


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate offline operator content-risk statistics.")
    parser.add_argument("--input", required=True, help="Candidate JSONL to inspect.")
    parser.add_argument("--output", required=True, help="JSON report path.")
    parser.add_argument("--minimum-samples", type=int, default=20)
    args = parser.parse_args()
    report = build_risk_report(_read_jsonl(Path(args.input)), minimum_samples=args.minimum_samples)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
