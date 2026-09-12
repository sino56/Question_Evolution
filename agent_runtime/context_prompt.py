"""Fixed-order prompt assembly for cache-friendly Agent contexts."""

from __future__ import annotations

from typing import Any, Mapping

from .context_cache import canonical_json


PROMPT_LAYER_ORDER = (
    "stable_prefix",
    "snapshot_prefix",
    "task_context",
    "memory_context",
    "world_state",
    "dynamic_tail",
)
# Layers before this index are the reusable, cache-addressed prefix.  The
# world state and the dynamic tail are volatile and must stay outside it.
CACHED_LAYER_COUNT = 4


def cached_prompt_prefix(context_pack: Mapping[str, Any]) -> str:
    """Serialize the reusable layers in the documented immutable order."""

    _require_v2(context_pack)
    return "\n".join(
        f"[{name}]\n{canonical_json(context_pack.get(name) or {})}"
        for name in PROMPT_LAYER_ORDER[:CACHED_LAYER_COUNT]
    )


def assemble_context_prompt(context_pack: Mapping[str, Any], *, instruction: str = "") -> str:
    """Append volatile state and the current instruction after cached layers."""

    prefix = cached_prompt_prefix(context_pack)
    volatile = [
        f"[{name}]\n{canonical_json(context_pack.get(name) or {})}"
        for name in PROMPT_LAYER_ORDER[CACHED_LAYER_COUNT:]
    ]
    suffix = f"[user_or_system_instruction]\n{instruction}" if instruction else ""
    return "\n".join(part for part in (prefix, *volatile, suffix) if part)


def _require_v2(context_pack: Mapping[str, Any]) -> None:
    required = set(PROMPT_LAYER_ORDER)
    if not required.issubset(context_pack):
        raise ValueError("context_pack_v2 requires all context layers")
