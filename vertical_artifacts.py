"""Append-only normalized artifacts for vertical search and recovery."""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterator, Mapping


VERTICAL_ARTIFACT_FORMAT_VERSION = 1

ARTIFACT_SPECS = {
    "node": ("vertical_nodes.jsonl", "node_id"),
    "attempt": ("operator_attempts.jsonl", "attempt_id"),
    "edge": ("boundary_edges.jsonl", "edge_id"),
    "path": ("boundary_paths.jsonl", "path_id"),
}


class VerticalArtifactStore:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.version_path = self.root / "vertical_artifacts.version.json"
        self._ensure_version()
        self._seen = {
            kind: self._load_seen(kind) for kind in ARTIFACT_SPECS
        }

    def _ensure_version(self) -> None:
        if self.version_path.exists():
            payload = json.loads(self.version_path.read_text(encoding="utf-8"))
            existing = int(payload.get("format_version") or 0)
            if existing != VERTICAL_ARTIFACT_FORMAT_VERSION:
                raise ValueError(
                    "vertical artifact format version mismatch; refuse to mix "
                    f"version {existing} and {VERTICAL_ARTIFACT_FORMAT_VERSION}"
                )
            return
        temporary = self.version_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(
                {"format_version": VERTICAL_ARTIFACT_FORMAT_VERSION},
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, self.version_path)

    def path(self, kind: str) -> Path:
        try:
            filename, _identity_field = ARTIFACT_SPECS[kind]
        except KeyError as exc:
            raise ValueError(f"unsupported vertical artifact kind: {kind}") from exc
        return self.root / filename

    def _load_seen(self, kind: str) -> set[str]:
        path = self.path(kind)
        _filename, identity_field = ARTIFACT_SPECS[kind]
        seen: set[str] = set()
        if not path.exists():
            return seen
        with path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid vertical artifact {path}:{line_number}: {exc.msg}"
                    ) from exc
                identity = str(record.get(identity_field) or "").strip()
                if not identity:
                    raise ValueError(
                        f"vertical artifact missing {identity_field}: {path}:{line_number}"
                    )
                seen.add(identity)
        return seen

    def append(self, kind: str, record: Mapping[str, Any]) -> bool:
        if kind not in ARTIFACT_SPECS:
            raise ValueError(f"unsupported vertical artifact kind: {kind}")
        _filename, identity_field = ARTIFACT_SPECS[kind]
        identity = str(record.get(identity_field) or "").strip()
        if not identity:
            raise ValueError(f"{kind} artifact requires {identity_field}")
        if identity in self._seen[kind]:
            return False
        row = deepcopy(dict(record))
        with self.path(kind).open("a", encoding="utf-8") as target:
            target.write(json.dumps(row, ensure_ascii=False) + "\n")
            target.flush()
            os.fsync(target.fileno())
        self._seen[kind].add(identity)
        return True

    def iter_records(self, kind: str) -> Iterator[Dict[str, Any]]:
        path = self.path(kind)
        if not path.exists():
            return
        with path.open("r", encoding="utf-8") as source:
            for line in source:
                if line.strip():
                    yield json.loads(line)

    def count(self, kind: str) -> int:
        return len(self._seen[kind])
