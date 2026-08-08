import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.budgeting.budget_state import BudgetLedger, BudgetLedgerError, UNALLOCATED_TARGET
from schema_validation import load_schema, validate_instance


def test_ledger_tracks_hard_limit_consumption_and_remaining_allocation():
    ledger = BudgetLedger.create({"generation": 8, "scoring": 4})
    ledger.allocations["generation"] = {"operator:O16": 3, "operator:O18": 5}
    ledger.consume("generation", "operator:O16", 2, evidence_ref={"branch_id": "b16"})

    assert ledger.consumed_for("generation", "operator:O16") == 2
    assert ledger.remaining_for("generation", "operator:O16") == 1
    assert ledger.remaining_by_type()["generation"] == 6
    ledger.validate()


def test_ledger_refuses_overconsumption_or_historical_inconsistency():
    ledger = BudgetLedger.create({"generation": 2})
    with pytest.raises(BudgetLedgerError, match="budget exhausted"):
        ledger.consume("generation", UNALLOCATED_TARGET, 3)

    corrupt = ledger.as_dict()
    corrupt["allocations"]["generation"][UNALLOCATED_TARGET] = 3
    with pytest.raises(BudgetLedgerError, match="does not reconcile"):
        BudgetLedger.from_dict(corrupt)


def test_budget_state_serializes_to_its_contract():
    ledger = BudgetLedger.create({"search_steps": 5})
    schema_path = ROOT / "schemas" / "budget_state.schema.json"
    validate_instance(ledger.as_dict(), load_schema(schema_path), schema_dir=schema_path.parent)
