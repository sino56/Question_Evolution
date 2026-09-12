"""Single source of truth for the cross-layer environment contract.

``plan.env_overrides`` (Agent) and ``run_loop.sh``'s own defaults are two
different mechanisms writing into the *same* process environment, and nothing
declared which one wins (report X-3).  This module declares that order once, and
both sides reference it:

* :data:`AGENT_INJECTABLE_ENV` is exactly what ``policy.ENV_ALLOWLIST`` accepts,
  so a plan can never inject an undeclared variable;
* :data:`ENV_PRECEDENCE` documents the resolution order;
* :data:`INNER_LOOP_MARKER` prevents a nested Agent invocation -- the Harness
  drives ``run_loop.sh`` through a registered tool, so the script must refuse to
  start a second Harness from inside that call.
"""

from __future__ import annotations


# Resolution order, highest precedence first.  Anything not listed below still
# follows "explicit environment beats the script default".
ENV_PRECEDENCE = (
    "1. explicit process environment (including Agent plan.env_overrides)",
    "2. local_api_config / config.py",
    "3. run_loop.sh built-in default",
)

# Variables the Agent may inject.  ``policy.ENV_ALLOWLIST`` is derived from this
# set, and ``run_loop.sh`` declares the same list in its header so a test can
# assert the two never drift.
AGENT_INJECTABLE_ENV = frozenset({
    "INPUT_FILE",
    "EXP_ROOT",
    "SEARCH_MODE",
    "SEARCH_BOUNDARY_TARGET",
    "BOUNDARY_TARGET",
    "MAX_SEARCH_STEPS",
    "EXECUTION_SCOPE",
    "SEARCH_MAX_DEPTH",
    "SEARCH_BRANCH_WINDOW",
    "SEARCH_MAX_REQUEST_ATTEMPTS_PER_SAMPLE",
    "SEARCH_MAX_EVALUATIONS_PER_SAMPLE",
    "SEARCH_SAMPLE_TIMEOUT_SECONDS",
    "ROUTER_CONCURRENCY",
    "SCORING_CONCURRENCY",
    # Metadata only: the router folds it into its cache identity and route
    # artifact; it never injects global strategy cards into the router.
    "MEMORY_SNAPSHOT_ID",
})

# Set by ``ToolRegistry`` whenever the Agent invokes a registered entry point.
# ``run_loop.sh``/``run_loop.ps1`` refuse ``--agent`` while it is present, so the
# outer control plane cannot recurse into itself.
INNER_LOOP_MARKER = "QE_AGENT_INNER"

# Marker line used by both loop scripts to declare the injectable set above.
# ``tests/test_agent_loop_integration.py`` compares the parsed value with
# ``AGENT_INJECTABLE_ENV``.
SCRIPT_DECLARATION_MARKER = "AGENT_INJECTABLE_ENV="


def declaration_line() -> str:
    """Return the exact declaration a loop script must contain."""

    return SCRIPT_DECLARATION_MARKER + " " + " ".join(sorted(AGENT_INJECTABLE_ENV))


def parse_script_declaration(text: str) -> set[str]:
    """Parse the declared injectable set out of a loop script body."""

    for line in text.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped.startswith(SCRIPT_DECLARATION_MARKER):
            return set(stripped[len(SCRIPT_DECLARATION_MARKER):].split())
    return set()


def resolved_environment(environ: "object", *, names: "object" = None) -> dict[str, str]:
    """Return the resolved values of the injectable variables for auditing.

    The design requires the *resolved* cross-layer values to be inspectable; an
    implicit environment hand-off otherwise leaves no record of what a run
    actually used.
    """

    mapping = environ if isinstance(environ, dict) else {}
    selected = names if names is not None else AGENT_INJECTABLE_ENV
    return {name: str(mapping[name]) for name in sorted(selected) if mapping.get(name) not in (None, "")}
