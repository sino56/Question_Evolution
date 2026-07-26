"""Append-only full branch artifacts with version and idempotency guards."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Tuple


BRANCH_ARTIFACT_FORMAT_VERSION = 1


def _clean(value: Any) -> str:
    return str(value or "").strip()


def artifact_identity(record: Mapping[str, Any], artifact_type: str) -> str:
    branch_id = _clean(record.get("branch_id") or record.get("candidate_id"))
    if not branch_id:
        raise ValueError("branch artifact requires branch_id/candidate_id")
    artifact_type = _clean(artifact_type)
    if not artifact_type:
        raise ValueError("branch artifact requires artifact_type")
    return f"{branch_id}::{artifact_type}"


class BranchArtifactStore:
    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        format_version: int = BRANCH_ARTIFACT_FORMAT_VERSION,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.version_path = self.path.with_suffix(self.path.suffix + ".version.json")
        self.format_version = int(format_version)
        self._ensure_version()
        self._seen = self._load_seen()

    def _ensure_version(self) -> None:
        if self.version_path.exists():
            payload = json.loads(self.version_path.read_text(encoding="utf-8"))
            existing = int(payload.get("format_version") or 0)
            if existing != self.format_version:
                raise ValueError(
                    "branch artifact format version mismatch; refuse to mix "
                    f"version {existing} and {self.format_version} in {self.path}"
                )
            return
        temporary = self.version_path.with_suffix(self.version_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"format_version": self.format_version},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.version_path)

    def _load_seen(self) -> set[str]:
        seen: set[str] = set()
        if not self.path.exists():
            return seen
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid branch artifact {self.path}:{line_number}: {exc.msg}"
                    ) from exc
                identity = _clean(row.get("artifact_id"))
                if not identity:
                    raise ValueError(
                        f"branch artifact missing artifact_id: {self.path}:{line_number}"
                    )
                seen.add(identity)
        return seen

    def append(self, record: Mapping[str, Any], artifact_type: str) -> bool:
        identity = artifact_identity(record, artifact_type)
        if identity in self._seen:
            return False
        row = {
            "format_version": self.format_version,
            "artifact_id": identity,
            "artifact_type": artifact_type,
            "branch_id": _clean(record.get("branch_id") or record.get("candidate_id")),
            "parent_node_id": _clean(record.get("parent_node_id")),
            "record": deepcopy(dict(record)),
        }
        with self.path.open("a", encoding="utf-8") as target:
            target.write(json.dumps(row, ensure_ascii=False) + "\n")
            target.flush()
            os.fsync(target.fileno())
        self._seen.add(identity)
        return True

    def iter_rows(self) -> Iterator[Dict[str, Any]]:
        if not self.path.exists():
            return
        with self.path.open("r", encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    yield json.loads(line)


def split_legacy_search_state(
    raw_state: Mapping[str, Any],
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Extract embedded full branch records from an old state representation."""

    state = deepcopy(dict(raw_state))
    artifacts: List[Dict[str, Any]] = []
    for field in ("branches", "branch_results", "full_branch_results"):
        rows = state.pop(field, None)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, Mapping):
                artifacts.append(deepcopy(dict(row)))
    return state, artifacts
