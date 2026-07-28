"""Runtime eligibility policy for registered question-generation operators.

Prompt specifications intentionally stay content-only.  This module is the
single owner of execution policy consumed by the Router and Search Coordinator.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

from prompts.operators import OPERATOR_SPECS


DEFAULT_RUNTIME_POLICY = {
    "generation_enabled": True,
    "validation_only": False,
    "qualification_status": "active",
}

OPERATOR_RUNTIME_POLICY: Dict[str, Dict[str, Any]] = {
    operator_id: dict(DEFAULT_RUNTIME_POLICY)
    for operator_id in OPERATOR_SPECS
}


def runtime_policy(operator_id: str) -> Mapping[str, Any]:
    """Return a normalized policy without letting callers mutate defaults."""

    policy = dict(DEFAULT_RUNTIME_POLICY)
    configured = OPERATOR_RUNTIME_POLICY.get(operator_id)
    if isinstance(configured, Mapping):
        policy.update(configured)
    return policy
