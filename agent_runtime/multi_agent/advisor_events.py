"""Append-only audit events for advisor collaboration."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

EVENT_TYPES = {"advisor_started", "advisor_completed", "advisor_failed", "advisor_timeout", "advisor_policy_rejected", "advice_merged"}


def append_advisor_event(run_dir: str | Path, event_type: str, payload: Mapping[str, Any]) -> Path:
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unsupported advisor event: {event_type}")
    root = Path(run_dir) / "multi_agent"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "advisor_events.jsonl"
    item = {"event_type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), **dict(payload)}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    return path
