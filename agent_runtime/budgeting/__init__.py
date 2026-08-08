"""Auditable, remaining-only budget controls for Agent replanning.

This package owns the Agent control-plane ledger only.  It never mutates a
pipeline ``search_state`` / ``operator_plan`` and every accepted adjustment is
represented by a new plan revision.
"""

from .budget_state import BudgetLedger, BudgetLedgerError
from .budget_reallocator import build_reallocation_proposal
from .budget_observer import build_budget_observation
from .budget_validator import BudgetValidator, validate_reallocation
from .budget_replan import BudgetReplanError, build_budget_replan
from .budget_report import budget_report_markdown, write_budget_artifacts
from .runtime import assess_budget_reallocation, hard_limits_from_task, load_or_create_ledger, save_ledger

__all__ = [
    "BudgetLedger",
    "BudgetLedgerError",
    "build_budget_observation",
    "build_reallocation_proposal",
    "BudgetValidator",
    "validate_reallocation",
    "BudgetReplanError",
    "build_budget_replan",
    "budget_report_markdown",
    "write_budget_artifacts",
    "assess_budget_reallocation",
    "hard_limits_from_task",
    "load_or_create_ledger",
    "save_ledger",
]
