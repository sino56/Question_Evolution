"""Optional configured model adapter for advisor JSON; local rules remain fail-open."""

from __future__ import annotations

import json
import os
import urllib.request
from typing import Any, Mapping

from .advisor_registry import AdvisorSpec
from .model_router import ModelSelection


def request_model_advice(spec: AdvisorSpec, context: Mapping[str, Any], selection: ModelSelection, *, environ: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """Return ``None`` when no advisor provider is configured.

    The transport credentials never enter ``context`` or any advisor artifact.
    """

    env = environ or os.environ
    if selection.selected_model == "local-deterministic-advisor":
        return None
    base_url = env.get("ADVISOR_BASE_URL", "").strip().rstrip("/")
    api_key = env.get("ADVISOR_API_KEY", "").strip()
    if not base_url or not api_key:
        return None
    body = {
        "model": selection.selected_model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": "You are a read-only Question Evolution advisor. Return only advisor_advice JSON. Never request or perform formal artifact mutation, execution, model selection, or advisor spawning."},
            {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
        ],
    }
    request = urllib.request.Request(base_url + "/chat/completions", data=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key}, method="POST")
    with urllib.request.urlopen(request, timeout=spec.max_runtime_seconds) as response:  # nosec B310: explicit local deployment configuration
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    if not isinstance(content, str):
        raise ValueError("advisor model response is not text")
    candidate = json.loads(content.strip().removeprefix("```json").removesuffix("```").strip())
    if not isinstance(candidate, dict):
        raise ValueError("advisor model response is not a JSON object")
    return candidate
