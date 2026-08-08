from dataclasses import replace

import pytest

from agent_runtime.multi_agent.advisor_registry import AdvisorSpec, get_advisor, validate_registry
from agent_runtime.multi_agent.model_router import select_model
from agent_runtime.multi_agent.advisor_model_client import request_model_advice


def test_registry_requires_model_tier_and_whitelisted_tools():
    validate_registry()
    spec = get_advisor("router_diagnosis")
    assert spec.model_tier == "reasoning_high"
    with pytest.raises(ValueError):
        from agent_runtime.multi_agent.advisor_registry import validate_spec

        validate_spec(replace(spec, model_tier=""))
    with pytest.raises(ValueError):
        from agent_runtime.multi_agent.advisor_registry import validate_spec

        validate_spec(replace(spec, allowed_tools=("run_full_loop",)))


def test_model_router_selects_tier_or_declared_fallback_without_spec_model_name():
    direct = select_model("reasoning_high", "reasoning_medium", models={"reasoning_high": "strong-model"})
    fallback = select_model("reasoning_high", "reasoning_medium", models={"reasoning_medium": "medium-model"})
    assert (direct.selected_model, direct.fallback_used) == ("strong-model", False)
    assert (fallback.selected_model, fallback.fallback_used) == ("medium-model", True)


def test_model_client_fails_open_to_local_adapter_without_transport_credentials():
    result = request_model_advice(get_advisor("router_diagnosis"), {}, select_model("reasoning_high", "reasoning_medium", models={"reasoning_high": "remote-model"}), environ={})
    assert result is None
