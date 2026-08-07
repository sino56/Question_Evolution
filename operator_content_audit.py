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


def detect_surface_risks(prompt: str) -> List[str]:
    """Return observable wording risks; no candidate disposition is produced."""
    text = str(prompt or "")
    return [
        label
        for label, patterns in RISK_PATTERNS.items()
        if any(re.search(pattern, text) for pattern in patterns)
    ]


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
        for record in operator_records:
            risks.update(detect_surface_risks(str(record.get("prompt", ""))))
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
