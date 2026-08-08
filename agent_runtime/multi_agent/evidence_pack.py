"""Create a compact, redacted evidence package for read-only advisors."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from ..context_cache import sha256_digest

SENSITIVE_KEYS = ("api_key", "apikey", "authorization", "base_url", "token", "secret", "password", "environment", "env")
FULL_RESPONSE_KEYS = ("raw_response", "model_response", "response", "completion", "candidate_answer", "answer", "full_prompt", "prompt_log")


def stable_hash(value: Any) -> str:
    """Compatibility wrapper around the shared canonical context hash."""

    return sha256_digest(value)


def redact(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if any(token in lowered for token in SENSITIVE_KEYS):
        return "[REDACTED]"
    if any(token in lowered for token in FULL_RESPONSE_KEYS):
        return "[OMITTED_FULL_RESPONSE]"
    if isinstance(value, Mapping):
        return {str(item_key): redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def build_evidence_pack(
    run_dir: str | Path,
    *,
    task: Mapping[str, Any],
    state: Mapping[str, Any],
    observation: Mapping[str, Any],
    plan: Mapping[str, Any] | None = None,
    tool_events: list[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    root = Path(run_dir) / "multi_agent"
    root.mkdir(parents=True, exist_ok=True)
    snapshot_ids = {"memory": state.get("memory_snapshot_id"), "policy": "agent-policy-v1", "prompt": "frozen", "operator": "frozen"}
    evidence = list(observation.get("evidence_refs") or [])[:40]
    pack = redact({
        "session_id": state.get("session_id") or state.get("agent_run_id") or Path(run_dir).name,
        "experiment_dir": state.get("experiment_dir") or observation.get("experiment_dir") or "",
        "goal": task.get("goal", ""),
        "snapshot_ids": snapshot_ids,
        "summary": {key: observation.get(key) for key in ("status", "main_issue", "status_counts", "boundary_candidate_count", "score_increased_count", "not_applicable_count", "validation_failed_count", "branch_error_count", "termination_reason", "target_reached", "missing_artifacts")},
        "observations": list(observation.get("observations") or [])[:40],
        "tool_events": list(tool_events or [])[-100:],
        "memory_summary": observation.get("memory_summary") or {},
        "evidence_refs": evidence,
        "plan_summary": {key: (plan or {}).get(key) for key in ("plan_id", "plan_revision", "selected_search_mode", "budget")},
    })
    pack["evidence_pack_hash"] = stable_hash(pack)
    path = root / "evidence_pack.json"
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(pack, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)
    return pack
