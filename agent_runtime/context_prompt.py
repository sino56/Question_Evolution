"""Fixed-order prompt assembly for cache-friendly Agent contexts."""

from __future__ import annotations

from typing import Any, Mapping

from .context_cache import canonical_json


PROMPT_LAYER_ORDER = (
    "stable_prefix",
    "snapshot_prefix",
    "task_context",
    "memory_context",
    "dynamic_tail",
)


def cached_prompt_prefix(context_pack: Mapping[str, Any]) -> str:
    """Serialize the four reusable layers in the documented immutable order."""

    _require_v2(context_pack)
    return "\n".join(
        f"[{name}]\n{canonical_json(context_pack.get(name) or {})}"
        for name in PROMPT_LAYER_ORDER[:4]
    )


def assemble_context_prompt(context_pack: Mapping[str, Any], *, instruction: str = "") -> str:
    """Append volatile state and the current instruction after cached layers."""

    prefix = cached_prompt_prefix(context_pack)
    dynamic = f"[dynamic_tail]\n{canonical_json(context_pack.get('dynamic_tail') or {})}"
    suffix = f"[user_or_system_instruction]\n{instruction}" if instruction else ""
    return "\n".join(part for part in (prefix, dynamic, suffix) if part)


def _require_v2(context_pack: Mapping[str, Any]) -> None:
    required = set(PROMPT_LAYER_ORDER)
    if not required.issubset(context_pack):
        raise ValueError("context_pack_v2 requires all context layers")
