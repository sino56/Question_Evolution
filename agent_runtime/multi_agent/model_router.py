"""Map model capability tiers to configured models; specs never name a provider."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping

from .advisor_registry import MODEL_TIERS

MODEL_ROUTER_VERSION = "advisor-model-router-v1"


@dataclass(frozen=True)
class ModelSelection:
    model_tier: str
    selected_model: str
    fallback_used: bool
    router_version: str = MODEL_ROUTER_VERSION

    def as_dict(self) -> dict:
        return {"model_tier": self.model_tier, "selected_model": self.selected_model, "fallback_used": self.fallback_used, "router_version": self.router_version}


def configured_models(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    env = environ or os.environ
    configured: dict[str, str] = {}
    raw = env.get("ADVISOR_MODELS_JSON", "").strip()
    if raw:
        try:
            candidate = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("ADVISOR_MODELS_JSON must be JSON") from exc
        if not isinstance(candidate, dict):
            raise ValueError("ADVISOR_MODELS_JSON must be a JSON object")
        configured.update({str(key): str(value) for key, value in candidate.items() if isinstance(value, str) and value.strip()})
    for tier in MODEL_TIERS:
        value = env.get("ADVISOR_MODEL_" + tier.upper(), "").strip()
        if value:
            configured[tier] = value
    return configured


def select_model(model_tier: str, fallback_model_tier: str, *, models: Mapping[str, str] | None = None) -> ModelSelection:
    if model_tier not in MODEL_TIERS or fallback_model_tier not in MODEL_TIERS:
        raise ValueError("unknown model tier")
    available = dict(models) if models is not None else configured_models()
    if available.get(model_tier):
        return ModelSelection(model_tier, str(available[model_tier]), False)
    if available.get(fallback_model_tier):
        return ModelSelection(fallback_model_tier, str(available[fallback_model_tier]), True)
    # A deterministic local adapter preserves review-only fail-open semantics
    # when no external advisor provider is configured.
    return ModelSelection(model_tier, "local-deterministic-advisor", False)
