"""Runtime schema gates for Agent contracts.

The JSON Schemas under ``schemas/`` are the formal contracts of the Agent
Harness.  This module turns them from documentation into write-time gates:
every value that crosses a stage boundary is validated against its schema
before it is persisted, so a drifting contract fails loudly instead of
silently landing on disk and flowing downstream.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from schema_validation import SchemaValidationError, load_schema, validate_instance


SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"


class ContractViolation(RuntimeError):
    """A value violated its published Agent contract."""


_SCHEMA_CACHE: Dict[str, Dict[str, Any]] = {}


def load_contract(name: str) -> Dict[str, Any]:
    """Load and cache a contract schema by file name."""

    if name not in _SCHEMA_CACHE:
        path = SCHEMA_DIR / name
        if not path.is_file():
            raise ContractViolation(f"contract schema is missing: {name}")
        _SCHEMA_CACHE[name] = load_schema(path)
    return _SCHEMA_CACHE[name]


def validate_contract(name: str, value: Any, *, path: str = "$") -> None:
    """Raise ``ContractViolation`` when ``value`` does not match ``name``."""

    try:
        validate_instance(value, load_contract(name), schema_dir=SCHEMA_DIR, path=path)
    except SchemaValidationError as exc:
        raise ContractViolation(f"{name} violation: {exc}") from exc
