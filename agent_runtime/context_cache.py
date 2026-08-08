"""Canonical serialization and cache metadata for Agent contexts.

This module intentionally defines cache *identity* only.  It does not create
an external cache or alter runtime decisions, so every context remains fully
auditable in the Agent run directory.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


CONTEXT_SCHEMA_VERSION = "context-pack-v2"
PROMPT_TEMPLATE_VERSION = "context-prompt-v2"


def canonical_json(payload: Any) -> str:
    """Return compact, deterministic JSON suitable for hashes and prompts."""

    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def sha256_digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def context_cache_key(
    *,
    context_schema_version: str,
    prompt_template_version: str,
    skill_registry_version: str,
    tool_registry_version: str,
    policy_snapshot_id: str | None,
    prompt_snapshot_id: str | None,
    operator_snapshot_id: str | None,
    memory_snapshot_id: str | None,
    selected_search_mode: str,
    selected_execution_scope: str,
) -> str:
    """Hash only the versioned, decision-relevant context identity.

    Run directories, timestamps, observations, and other dynamic state are
    deliberately absent to preserve prompt-prefix reuse across executions.
    """

    return sha256_digest(
        {
            "context_schema_version": context_schema_version,
            "prompt_template_version": prompt_template_version,
            "skill_registry_version": skill_registry_version,
            "tool_registry_version": tool_registry_version,
            "policy_snapshot_id": policy_snapshot_id or "",
            "prompt_snapshot_id": prompt_snapshot_id or "",
            "operator_snapshot_id": operator_snapshot_id or "",
            "memory_snapshot_id": memory_snapshot_id or "",
            "selected_search_mode": selected_search_mode,
            "selected_execution_scope": selected_execution_scope,
        }
    )


def memory_context_key(
    *,
    memory_snapshot_id: str | None,
    normalized_query: str,
    retrieval_config_version: str,
    top_k: int,
) -> str:
    """Return the reproducible cache identity for a Top-K Memory retrieval."""

    return sha256_digest(
        {
            "memory_snapshot_id": memory_snapshot_id or "",
            "normalized_query": " ".join(normalized_query.lower().split()),
            "retrieval_config_version": retrieval_config_version,
            "top_k": int(top_k),
        }
    )


def cache_metadata(
    *,
    stable_prefix: Mapping[str, Any],
    snapshot_prefix: Mapping[str, Any],
    task_context: Mapping[str, Any],
    memory_context: Mapping[str, Any],
    dynamic_tail: Mapping[str, Any],
    prompt_template_version: str = PROMPT_TEMPLATE_VERSION,
) -> dict[str, str]:
    """Create auditable component hashes without persisting any cache data."""

    return {
        "context_cache_key": context_cache_key(
            context_schema_version=str(snapshot_prefix.get("context_schema_version") or CONTEXT_SCHEMA_VERSION),
            prompt_template_version=prompt_template_version,
            skill_registry_version=str(snapshot_prefix.get("skill_registry_version") or ""),
            tool_registry_version=str(snapshot_prefix.get("tool_registry_version") or ""),
            policy_snapshot_id=_as_text(snapshot_prefix.get("policy_snapshot_id")),
            prompt_snapshot_id=_as_text(snapshot_prefix.get("prompt_snapshot_id")),
            operator_snapshot_id=_as_text(snapshot_prefix.get("operator_snapshot_id")),
            memory_snapshot_id=_as_text(snapshot_prefix.get("memory_snapshot_id")),
            selected_search_mode=str(task_context.get("selected_search_mode") or ""),
            selected_execution_scope=str(task_context.get("selected_execution_scope") or ""),
        ),
        "stable_prefix_hash": sha256_digest(stable_prefix),
        "snapshot_prefix_hash": sha256_digest(snapshot_prefix),
        "task_context_hash": sha256_digest(task_context),
        "memory_context_hash": sha256_digest(memory_context),
        "dynamic_tail_hash": sha256_digest(dynamic_tail),
    }


def _as_text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""
