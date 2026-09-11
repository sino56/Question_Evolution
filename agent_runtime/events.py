"""Append-only, redacted Agent event journal."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping


_SENSITIVE_KEY_NAMES = {
    "api_key",
    "apikey",
    "x_api_key",
    "authorization",
    "auth",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "client_secret",
    "password",
    "passwd",
    "credential",
    "credentials",
    "base_url",
}
# Only explicit credential-shaped suffixes are redacted.  Audit identifiers such
# as ``idempotency_key`` or ``key_findings`` must survive unchanged.
_SENSITIVE_KEY_SUFFIX = re.compile(
    r"(?:^|_)(?:api_key|apikey|access_token|refresh_token|auth_token|client_secret|authorization|password|credentials|base_url)$",
    re.I,
)
_SENSITIVE_VALUE = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|Bearer\s+\S+|https?://[^\s]+)",
    re.I,
)
_ASSIGNMENT_SECRET = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|auth[_-]?token|client[_-]?secret|authorization|password|token|secret|key)\s*[=:]\s*\S+"
)
# Evidence and artifact references must survive redaction: an audit trail that
# cannot point at its own artifacts is worthless.  These keys are the only
# places a URL is preserved.
_REFERENCE_VALUE_KEYS = {
    "artifact_ref",
    "artifact_refs",
    "artifact_url",
    "source_ref",
    "evidence_ref",
    "evidence_refs",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_sensitive_key(key: str) -> bool:
    """Return True only for credential-shaped field names."""

    normalized = str(key).strip().lower().replace("-", "_")
    if normalized in _SENSITIVE_KEY_NAMES:
        return True
    return bool(_SENSITIVE_KEY_SUFFIX.search(normalized))


def redact(value: Any, *, key: str = "") -> Any:
    if is_sensitive_key(key):
        return "[REDACTED]"
    if isinstance(value, Mapping):
        return {str(name): redact(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = _ASSIGNMENT_SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
        if str(key).strip().lower() in _REFERENCE_VALUE_KEYS:
            return value
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
