"""Append-only, redacted Agent event journal."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping


_SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|authorization|token|secret|base[_-]?url|key)", re.I)
_SENSITIVE_VALUE = re.compile(r"(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+|https?://[^\s]+)", re.I)
_ASSIGNMENT_SECRET = re.compile(r"(?i)\b(api[_-]?key|authorization|token|secret|key)\s*[=:]\s*\S+")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact(value: Any, *, key: str = "") -> Any:
    if _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(name): redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _ASSIGNMENT_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
        return _SENSITIVE_VALUE.sub("[REDACTED]", value)
    return value


def summarize_text(value: Any, *, limit: int = 1200) -> str:
    text = str(redact(value)).strip()
    return text[:limit] + ("…" if len(text) > limit else "")


def append_event(path: str | Path, event_type: str, payload: Mapping[str, Any]) -> Dict[str, Any]:
    event = {"event_type": event_type, "created_at": utc_now(), **redact(dict(payload))}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
    return event
