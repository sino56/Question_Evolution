"""Bounded failure-recovery recipes for the Agent control loop.

The design (§11.1, §16.4) calls for an explicit *failure handling recipe
library* instead of recovery logic scattered across ``if`` branches.  This
module is that single data source: every recipe maps a
``(failure_category × observation_type)`` signature to exactly one bounded
recovery action with a declared attempt budget.

Two vocabularies are deliberately kept separate:

``RECOVERY_ACTIONS``
    What the *control loop* may do next (retry, roll back, re-observe, stop).
    These never become ``agent_decision.action`` values, so ``policy.DECISIONS``
    stays the closed set of formal decisions.

decision actions
    ``run_pipeline`` / ``resume_pipeline`` / ``run_review`` / ``replan`` /
    ``suspend`` / ``blocked`` / ``stop_and_report`` -- owned by ``decisions.py``
    and ``policy.py``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable, Mapping, Sequence


# --- recovery action vocabulary -------------------------------------------
ACTION_RETRY_TOOL = "retry_tool"
ACTION_ROLLBACK_AND_RETRY = "rollback_and_retry"
ACTION_RESUME_FROM_CHECKPOINT = "resume_from_checkpoint"
ACTION_SUSPEND_WITH_BACKUP_ENDPOINT = "suspend_with_backup_endpoint"
ACTION_REOBSERVE_THEN_FAIL_FAST = "reobserve_then_fail_fast"
ACTION_REDUCE_OPERATOR_BUDGET = "reduce_operator_budget"
ACTION_STOP_AND_REPORT = "stop_and_report"

RECOVERY_ACTIONS = {
    ACTION_RETRY_TOOL,
    ACTION_ROLLBACK_AND_RETRY,
    ACTION_RESUME_FROM_CHECKPOINT,
    ACTION_SUSPEND_WITH_BACKUP_ENDPOINT,
    ACTION_REOBSERVE_THEN_FAIL_FAST,
    ACTION_REDUCE_OPERATOR_BUDGET,
    ACTION_STOP_AND_REPORT,
}
# Only these actions may spend model budget again inside one Session.
RE_ENTRY_ACTIONS = {ACTION_RETRY_TOOL, ACTION_ROLLBACK_AND_RETRY, ACTION_RESUME_FROM_CHECKPOINT}


@dataclass(frozen=True)
class RecoveryRecipe:
    """One bounded recovery rule.

    ``failure_category`` and ``observation_types`` are alternative triggers; a
    recipe matches when either one matches.  ``max_attempts`` is the maximum
    number of *re-entries* this recipe may trigger inside one Session.
    """

    recipe_id: str
    recovery_action: str
    max_attempts: int
    reason: str
    failure_category: str = ""
    observation_types: tuple[str, ...] = ()


# Order is precedence: the first matching recipe wins.  The order mirrors
# ``decisions.py`` so the recipe table can never contradict the decision chain.
RECIPES: tuple[RecoveryRecipe, ...] = (
    RecoveryRecipe(
        recipe_id="manifest_corrupted",
        recovery_action=ACTION_STOP_AND_REPORT,
        max_attempts=0,
        reason="a corrupted published manifest cannot be repaired by the Agent",
        observation_types=("manifest_corrupted",),
    ),
    RecoveryRecipe(
        recipe_id="fatal_system_error",
        recovery_action=ACTION_STOP_AND_REPORT,
        max_attempts=0,
        reason="a non-recoverable tool failure must reach a human",
        failure_category="fatal_system_error",
    ),
    RecoveryRecipe(
        recipe_id="configuration_error",
        recovery_action=ACTION_STOP_AND_REPORT,
        max_attempts=0,
        reason="a configuration defect must surface immediately, never be retried",
        failure_category="configuration_error",
    ),
    RecoveryRecipe(
        recipe_id="retryable_system_error",
        recovery_action=ACTION_SUSPEND_WITH_BACKUP_ENDPOINT,
        max_attempts=1,
        reason="a retryable system failure suspends the Session while a backup endpoint is selected",
        failure_category="retryable_system_error",
    ),
    RecoveryRecipe(
        recipe_id="judge_instability",
        recovery_action=ACTION_SUSPEND_WITH_BACKUP_ENDPOINT,
        max_attempts=0,
        reason="unstable judging invalidates attribution until the affected samples are re-evaluated",
        observation_types=("judge_instability_detected",),
    ),
    RecoveryRecipe(
        recipe_id="artifact_missing",
        recovery_action=ACTION_REOBSERVE_THEN_FAIL_FAST,
        max_attempts=0,
        reason="a missing formal artifact is re-observed once, then fails fast",
        observation_types=("artifact_missing",),
    ),
    RecoveryRecipe(
        recipe_id="score_increased",
        recovery_action=ACTION_STOP_AND_REPORT,
        max_attempts=0,
        reason=(
            "a score increase is negative gain: stop, keep the failure memory, and let the "
            "next session change the operator strategy.  A v1 plan has no strategy variation "
            "axis, so an automatic rollback would re-run the identical call instead of retrying "
            "differently."
        ),
        observation_types=("score_increased",),
    ),
    RecoveryRecipe(
        recipe_id="invalid_generation",
        recovery_action=ACTION_REDUCE_OPERATOR_BUDGET,
        max_attempts=1,
        reason="repeated invalid generation narrows the operator budget instead of penalising the whole family",
        observation_types=("candidate_invalid",),
    ),
    RecoveryRecipe(
        recipe_id="effective_boundary",
        recovery_action=ACTION_STOP_AND_REPORT,
        max_attempts=0,
        reason="an effective boundary is terminal evidence and must be stored",
        observation_types=("effective_boundary_found",),
    ),
    RecoveryRecipe(
        recipe_id="no_recipe",
        recovery_action=ACTION_STOP_AND_REPORT,
        max_attempts=0,
        reason="no recovery recipe matched; the Session stops and reports",
    ),
)

RECIPE_INDEX: dict[str, RecoveryRecipe] = {recipe.recipe_id: recipe for recipe in RECIPES}
# Terminal fallback: always the last entry, so lookup can never fail.
FALLBACK_RECIPE_ID = RECIPES[-1].recipe_id


def select_recipe(*, failure_category: str = "", observation_types: Iterable[str] = ()) -> RecoveryRecipe:
    """Return the first recipe matching the signature, never ``None``."""

    types = {str(item) for item in observation_types}
    for recipe in RECIPES:
        if recipe.failure_category and recipe.failure_category == failure_category:
            return recipe
        if recipe.observation_types and types & set(recipe.observation_types):
            return recipe
    return RECIPE_INDEX[FALLBACK_RECIPE_ID]


def recipe_fields(recipe: RecoveryRecipe) -> dict[str, Any]:
    """Return the audit fields a decision embeds for a recipe."""

    return {
        "recovery_recipe_id": recipe.recipe_id,
        "recovery_action": recipe.recovery_action,
        "recovery_max_attempts": recipe.max_attempts,
        "recovery_reason": recipe.reason,
    }


def describe_recipes() -> list[dict[str, Any]]:
    return [asdict(recipe) for recipe in RECIPES]


def recipe_allows_reentry(recipe: Mapping[str, Any], *, attempts_used: int) -> bool:
    """Return True only when a recipe may spend model budget again."""

    action = str(recipe.get("recovery_action") or "")
    if action not in RE_ENTRY_ACTIONS:
        return False
    try:
        maximum = int(recipe.get("recovery_max_attempts") or 0)
    except (TypeError, ValueError):
        return False
    return attempts_used < maximum


def system_failure_categories() -> Sequence[str]:
    """Every failure category the recipe table knows about."""

    return tuple(sorted({recipe.failure_category for recipe in RECIPES if recipe.failure_category}))
