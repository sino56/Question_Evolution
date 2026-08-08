"""Persistent per-advisor records; every transition is append-only."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

STATUSES = {"pending", "running", "completed", "failed", "skipped", "timeout", "rejected_by_policy"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_run_record(run_dir: str | Path, record: Mapping[str, Any]) -> Path:
    if record.get("status") not in STATUSES:
        raise ValueError("invalid advisor run status")
    root = Path(run_dir) / "multi_agent"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "advisor_runs.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")
    return path
