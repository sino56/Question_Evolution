"""L3 procedural memory: versioned, read-only execution rules (design §13.4).

Design §13.4 asks for a *versioned, stable execution-rule library* covering tool
order, retry/fail-fast rules, budget rules, rollback conditions, publish gates,
and approval conditions.  Until now those rules were hard-coded constants with
no version, no change audit, and no way to govern their evolution (report M-7).

This module is deliberately small:

* the rules live in ``memory/procedural/*.json`` as data, with ``version`` and
  ``approved_by``;
* loading is read-only and returns a content hash, which is folded into the
  context ``snapshot_prefix`` so a Session records *which* rule revision it ran
  under;
* :func:`verify_against_runtime` asserts that the runtime constants still agree
  with the declared rules -- that assertion is what turns a config file into a
  governance link instead of decoration.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

from .events import redact


PROCEDURAL_DIR = "memory/procedural"
REQUIRED_RULE_IDS = {
    "tool_order",
    "retry_and_fail_fast",
    "budget",
    "rollback",
    "publish_gate",
    "approval",
}
REQUIRED_FIELDS = {"rule_id", "version", "approved_by", "rules"}


class ProceduralMemoryError(RuntimeError):
    """The procedural rule library is missing, malformed, or inconsistent."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


@dataclass(frozen=True)
class ProceduralMemory:
    """One immutable snapshot of the L3 rule library."""

    version: str
    content_hash: str
    rules: Dict[str, Any]
    approved_by: tuple[str, ...]
    source_files: tuple[str, ...]

    def rule(self, rule_id: str) -> Dict[str, Any]:
        value = self.rules.get(rule_id)
        if not isinstance(value, Mapping):
            raise ProceduralMemoryError(f"procedural rule is not defined: {rule_id}")
        return dict(value)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "procedural_memory_version": self.version,
            "procedural_memory_hash": self.content_hash,
            "approved_by": list(self.approved_by),
            "source_files": list(self.source_files),
        }


def procedural_dir(project_root: str | Path) -> Path:
    return Path(project_root) / PROCEDURAL_DIR


def load_procedural_memory(project_root: str | Path, *, required: bool = True) -> ProceduralMemory:
    """Load and validate the L3 rule library without ever writing to it."""

    directory = procedural_dir(project_root)
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        if required:
            raise ProceduralMemoryError(f"procedural rule library is missing: {directory}")
        return ProceduralMemory(version="procedural-unavailable", content_hash="sha256:", rules={}, approved_by=(), source_files=())
    versions: list[str] = []
    approved: list[str] = []
    rules: Dict[str, Any] = {}
    payload: Dict[str, Any] = {}
    sources: list[str] = []
    for path in files:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProceduralMemoryError(f"procedural rule file is unreadable: {path}: {exc}") from exc
        if not isinstance(value, Mapping):
            raise ProceduralMemoryError(f"procedural rule file must be a JSON object: {path}")
        missing = sorted(field for field in REQUIRED_FIELDS if not value.get(field))
        if missing:
            raise ProceduralMemoryError(f"{path.name} is missing required fields: {', '.join(missing)}")
        declared = value.get("rules")
        if not isinstance(declared, Mapping):
            raise ProceduralMemoryError(f"{path.name} must declare a 'rules' object")
        for rule_id, rule_value in declared.items():
            if not isinstance(rule_value, Mapping):
                raise ProceduralMemoryError(f"{path.name} rule '{rule_id}' must be an object")
            if rule_id in rules:
                raise ProceduralMemoryError(f"procedural rule is declared twice: {rule_id}")
            rules[str(rule_id)] = dict(rule_value)
        versions.append(str(value["version"]))
        approved.append(str(value["approved_by"]))
        sources.append(path.name)
        payload[path.name] = dict(value)
    unknown = sorted(set(rules) - REQUIRED_RULE_IDS)
    if unknown:
        raise ProceduralMemoryError("undeclared procedural rule ids: " + ", ".join(unknown))
    absent = sorted(REQUIRED_RULE_IDS - set(rules))
    if absent:
        raise ProceduralMemoryError("missing procedural rules: " + ", ".join(absent))
    version = versions[0] if len(set(versions)) == 1 else "procedural-mixed:" + "+".join(sorted(set(versions)))
    content_hash = "sha256:" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return ProceduralMemory(
        version=version,
        content_hash=content_hash,
        rules=rules,
        approved_by=tuple(dict.fromkeys(approved)),
        source_files=tuple(sources),
    )


def verify_against_runtime(memory: ProceduralMemory, *, runtime: Mapping[str, Any]) -> list[str]:
    """Return the list of rule/runtime mismatches (empty means consistent).

    ``runtime`` is supplied by the caller (normally by the contract test) as a
    mapping ``{rule_id: actual_value}``.  Comparing declared rules with the
    values the code actually uses is what prevents the rule library from
    silently drifting away from the implementation.
    """

    mismatches: list[str] = []
    for rule_id, actual in runtime.items():
        declared = memory.rules.get(rule_id)
        if not isinstance(declared, Mapping):
            mismatches.append(f"{rule_id}: rule is not declared")
            continue
        if _canonical(redact(declared)) != _canonical(redact(dict(actual))):
            mismatches.append(f"{rule_id}: declared rule differs from the runtime value")
    return mismatches
