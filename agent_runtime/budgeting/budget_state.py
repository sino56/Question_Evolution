"""Append-only compatible ledger for hard limits, remaining allocations, and use."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, MutableMapping, Sequence


BUDGET_TYPES = {
    "generation",
    "candidate",
    "branch",
    "search_steps",
    "scoring",
    "repeat_scoring",
    "vertical_depth",
    "model_calls",
    "time_seconds",
}
UNALLOCATED_TARGET = "pool:unallocated"


class BudgetLedgerError(ValueError):
    """A ledger update would alter history or exceed a hard limit."""


def _number(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise BudgetLedgerError(f"{field_name} must be a non-negative number")
    return float(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class BudgetLedger:
    """A serializable ledger whose allocations always represent *remaining* use.

    ``hard_limits`` are immutable for a session.  Consumption only decreases
    allocations; reallocation only moves remaining units between targets.  The
    append-only ``events`` list makes an old snapshot reconstructable without
    letting a proposal rewrite past consumption.
    """

    hard_limits: Dict[str, float]
    allocations: Dict[str, Dict[str, float]]
    consumed: Dict[str, Dict[str, float]] = field(default_factory=dict)
    events: list[Dict[str, Any]] = field(default_factory=list)
    version: str = "budget-ledger-v1"

    @classmethod
    def create(cls, limits: Mapping[str, Any]) -> "BudgetLedger":
        normalized: Dict[str, float] = {}
        allocations: Dict[str, Dict[str, float]] = {}
        for budget_type, raw_limit in limits.items():
            name = str(budget_type)
            if name not in BUDGET_TYPES:
                raise BudgetLedgerError(f"unsupported budget type: {name}")
            limit = _number(raw_limit, field_name=f"hard limit for {name}")
            normalized[name] = limit
            allocations[name] = {UNALLOCATED_TARGET: limit}
        ledger = cls(hard_limits=normalized, allocations=allocations)
        ledger.events.append({"event_type": "ledger_initialized", "created_at": _now(), "hard_limits": deepcopy(normalized)})
        return ledger

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "BudgetLedger":
        limits = value.get("hard_limits")
        allocations = value.get("allocations")
        consumed = value.get("consumed", {})
        events = value.get("events", [])
        if not isinstance(limits, Mapping) or not isinstance(allocations, Mapping):
            raise BudgetLedgerError("ledger must contain hard_limits and allocations")
        ledger = cls(
            hard_limits={str(key): _number(item, field_name=f"hard limit for {key}") for key, item in limits.items()},
            allocations={str(kind): {str(target): _number(amount, field_name=f"allocation for {kind}") for target, amount in dict(targets).items()} for kind, targets in allocations.items() if isinstance(targets, Mapping)},
            consumed={str(kind): {str(target): _number(amount, field_name=f"consumption for {kind}") for target, amount in dict(targets).items()} for kind, targets in dict(consumed).items() if isinstance(targets, Mapping)},
            events=[dict(event) for event in events if isinstance(event, Mapping)],
            version=str(value.get("version") or "budget-ledger-v1"),
        )
        ledger.validate()
        return ledger

    def as_dict(self) -> Dict[str, Any]:
        return {
            "version": self.version,
            "hard_limits": deepcopy(self.hard_limits),
            "allocations": deepcopy(self.allocations),
            "consumed": deepcopy(self.consumed),
            "remaining": self.remaining_by_type(),
            "events": deepcopy(self.events),
        }

    def remaining_by_type(self) -> Dict[str, float]:
        return {kind: sum(targets.values()) for kind, targets in self.allocations.items()}

    def remaining_for(self, budget_type: str, target: str) -> float:
        return self.allocations.get(budget_type, {}).get(target, 0.0)

    def consumed_for(self, budget_type: str, target: str | None = None) -> float:
        values = self.consumed.get(budget_type, {})
        return sum(values.values()) if target is None else values.get(target, 0.0)

    def validate(self) -> None:
        if set(self.hard_limits) != set(self.allocations):
            raise BudgetLedgerError("ledger allocations must cover exactly the hard-limit budget types")
        for kind, hard_limit in self.hard_limits.items():
            if kind not in BUDGET_TYPES:
                raise BudgetLedgerError(f"unsupported budget type: {kind}")
            total_remaining = sum(self.allocations[kind].values())
            total_consumed = sum(self.consumed.get(kind, {}).values())
            if total_remaining < -1e-9 or total_consumed < -1e-9:
                raise BudgetLedgerError("ledger values cannot be negative")
            if abs(total_remaining + total_consumed - hard_limit) > 1e-9:
                raise BudgetLedgerError(f"ledger does not reconcile for {kind}")

    def consume(self, budget_type: str, target: str, amount: float, *, evidence_ref: Mapping[str, Any] | None = None) -> None:
        amount = _number(amount, field_name="consumption amount")
        available = self.remaining_for(budget_type, target)
        if budget_type not in self.hard_limits:
            raise BudgetLedgerError(f"budget type is not configured: {budget_type}")
        if amount > available + 1e-9:
            raise BudgetLedgerError(f"budget exhausted for {budget_type} at {target}")
        self.allocations[budget_type][target] = available - amount
        self.consumed.setdefault(budget_type, {})[target] = self.consumed.get(budget_type, {}).get(target, 0.0) + amount
        self.events.append({"event_type": "budget_consumed", "created_at": _now(), "budget_type": budget_type, "target": target, "amount": amount, "evidence_ref": dict(evidence_ref or {})})
        self.validate()

    def record_tool_call(self, tool: str, *, tool_call_id: str, duration_seconds: float, ok: bool) -> None:
        """Record every registered call even when no numeric call budget exists."""

        self.events.append({
            "event_type": "tool_call_observed",
            "created_at": _now(),
            "tool": str(tool),
            "tool_call_id": str(tool_call_id),
            "duration_seconds": max(0.0, float(duration_seconds)),
            "ok": bool(ok),
        })

    def apply_changes(self, changes: Sequence[Mapping[str, Any]], *, proposal_id: str) -> None:
        """Atomically move remaining allocation after external validation."""

        before = self.as_dict()
        try:
            for change in changes:
                kind = str(change["budget_type"])
                target = str(change["target"])
                before_amount = _number(change["from"], field_name="change.from")
                after_amount = _number(change["to"], field_name="change.to")
                if kind not in self.hard_limits:
                    raise BudgetLedgerError(f"budget type is not configured: {kind}")
                if abs(self.remaining_for(kind, target) - before_amount) > 1e-9:
                    raise BudgetLedgerError(f"stale allocation for {kind} at {target}")
                self.allocations[kind][target] = after_amount
            self.validate()
        except Exception:
            restored = BudgetLedger.from_dict(before)
            self.hard_limits, self.allocations, self.consumed, self.events, self.version = restored.hard_limits, restored.allocations, restored.consumed, restored.events, restored.version
            raise
        self.events.append({"event_type": "budget_reallocated", "created_at": _now(), "proposal_id": proposal_id, "changes": [dict(change) for change in changes]})
