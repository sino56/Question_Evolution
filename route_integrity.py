"""Stable, secret-free identity for a frozen live Router decision.

The Router response and the search coordinator deliberately have different
responsibilities.  This module is the small shared boundary between them: it
turns the execution-relevant route metadata and the frozen operator list into
a canonical fingerprint, and rejects attempts to resume a ``live`` search
with an incompatible route.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Mapping

from router_contract import ROUTE_REVISION, ROUTING_SCHEMA_VERSION


ROUTE_INTEGRITY_VERSION = "live-route-integrity-v1"


class RouteIntegrityError(ValueError):
    """A route is incomplete or does not match its declared frozen identity."""


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _positive_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise RouteIntegrityError(f"{field} must be a positive number")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RouteIntegrityError(f"{field} must be a positive number") from exc
    if parsed <= 0:
        raise RouteIntegrityError(f"{field} must be a positive number")
    return parsed


def _nonnegative_integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool):
        raise RouteIntegrityError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise RouteIntegrityError(f"{field} must be a non-negative integer") from exc
    if parsed < 0 or parsed != value:
        raise RouteIntegrityError(f"{field} must be a non-negative integer")
    return parsed


def _unique_string_list(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list):
        raise RouteIntegrityError(f"{field} must be an ordered string list")
    normalized = [_clean(item) for item in value]
    if any(not item for item in normalized) or len(set(normalized)) != len(normalized):
        raise RouteIntegrityError(f"{field} must contain unique non-empty strings")
    return normalized


def is_live_route(route: Mapping[str, Any] | None) -> bool:
    return bool(
        isinstance(route, Mapping)
        and _clean(route.get("routing_mode")) == "hybrid"
        and _clean(route.get("assignment_mode")) == "live"
    )


def live_route_identity(route: Mapping[str, Any]) -> Dict[str, Any]:
    """Return canonical metadata that determines safe live-route reuse.

    The identity intentionally contains provider *identifiers*, never a base
    URL or credential.  It includes the eligible space as well as the selected
    subset, so a policy/registry change cannot silently reuse a frozen plan.
    """

    if not isinstance(route, Mapping):
        raise RouteIntegrityError("live assignment requires operator_route")
    if _clean(route.get("routing_mode")) != "hybrid":
        raise RouteIntegrityError("live assignment requires routing_mode=hybrid")
    if _clean(route.get("assignment_mode")) != "live":
        raise RouteIntegrityError("live assignment requires assignment_mode=live")
    if _clean(route.get("route_revision")) != ROUTE_REVISION:
        raise RouteIntegrityError("live route_revision is missing or unsupported")
    if _clean(route.get("routing_schema_version")) != ROUTING_SCHEMA_VERSION:
        raise RouteIntegrityError("live routing_schema_version is missing or unsupported")

    required_text_fields = (
        "router_prompt_version",
        "router_transport_policy_version",
        "router_registry_policy_version",
        "router_registry_revision",
        "router_model",
        "router_provider_id",
        "route_source",
        "router_status",
    )
    values: Dict[str, Any] = {
        "route_integrity_version": ROUTE_INTEGRITY_VERSION,
        "routing_mode": "hybrid",
        "assignment_mode": "live",
        "route_revision": ROUTE_REVISION,
        "routing_schema_version": ROUTING_SCHEMA_VERSION,
    }
    for field in required_text_fields:
        text = _clean(route.get(field))
        if not text:
            raise RouteIntegrityError(f"live route is missing {field}")
        values[field] = text

    values["router_timeout_seconds"] = _positive_number(
        route.get("router_timeout_seconds"), field="router_timeout_seconds"
    )
    values["router_retries"] = _nonnegative_integer(
        route.get("router_retries"), field="router_retries"
    )
    if values["router_retries"] != 0:
        raise RouteIntegrityError("live router_retries must be 0")
    values["router_concurrency"] = _nonnegative_integer(
        route.get("router_concurrency"), field="router_concurrency"
    )
    if values["router_concurrency"] < 1:
        raise RouteIntegrityError("router_concurrency must be at least 1")
    values["selected_operator_ids"] = _unique_string_list(
        route.get("selected_operator_ids"), field="selected_operator_ids"
    )
    values["eligible_operator_ids"] = _unique_string_list(
        route.get("eligible_operator_ids"), field="eligible_operator_ids"
    )
    if not set(values["selected_operator_ids"]).issubset(values["eligible_operator_ids"]):
        raise RouteIntegrityError("selected_operator_ids must be contained in eligible_operator_ids")
    values["router_fallback_used"] = bool(route.get("router_fallback_used"))
    return values


def route_fingerprint(identity: Mapping[str, Any]) -> str:
    payload = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def attach_live_route_integrity(route: Mapping[str, Any]) -> Dict[str, Any]:
    """Attach the canonical identity only to hybrid/live route records."""

    result = dict(route)
    if not is_live_route(result):
        return result
    identity = live_route_identity(result)
    result["route_integrity_version"] = ROUTE_INTEGRITY_VERSION
    result["route_fingerprint"] = route_fingerprint(identity)
    return result


def validate_live_route_integrity(route: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate a persisted live route and return its canonical identity."""

    identity = live_route_identity(route)
    if _clean(route.get("route_integrity_version")) != ROUTE_INTEGRITY_VERSION:
        raise RouteIntegrityError("live route_integrity_version is missing or unsupported")
    expected = route_fingerprint(identity)
    if _clean(route.get("route_fingerprint")) != expected:
        raise RouteIntegrityError("live route_fingerprint does not match frozen route metadata")
    return identity
