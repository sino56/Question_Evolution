"""Deterministic trigger rules for read-only advisor work."""

from __future__ import annotations

from typing import Any, Mapping

from .advisor_registry import AdvisorSpec, list_advisors


def select_advisors(stage: str, observation: Mapping[str, Any] | None = None) -> list[AdvisorSpec]:
    """Return a deterministic, registered task set; no prompt chooses execution."""

    observed = observation or {}
    selected = list_advisors(stage=stage)
    if stage == "post_experiment_review":
        # A normal completed experiment gets the five independent review lenses.
        # A blocked experiment retains only diagnostics that can use published
        # evidence; this prevents absent artifacts becoming invented findings.
        if observed.get("status") == "blocked":
            selected = [spec for spec in selected if spec.advisor_id in {"validation_diagnosis", "scoring_stability", "search_cost"}]
    elif stage == "human_review_precheck":
        # The synthesizer depends on the four independent prechecks.
        selected = [spec for spec in selected if spec.advisor_id != "review_synthesis"]
    return selected


def dependent_advisors(stage: str) -> list[AdvisorSpec]:
    if stage != "human_review_precheck":
        return []
    return [spec for spec in list_advisors(stage=stage) if spec.advisor_id == "review_synthesis"]


def missing_inputs(spec: AdvisorSpec, evidence_pack: Mapping[str, Any]) -> list[str]:
    missing: list[str] = []
    for dotted in spec.allowed_inputs:
        current: Any = evidence_pack
        for part in dotted.removeprefix("evidence_pack.").split("."):
            if not isinstance(current, Mapping) or part not in current:
                missing.append(dotted)
                break
            current = current[part]
    return missing
