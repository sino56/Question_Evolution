"""Shared JSONL artifact, scheduling, request-pool, and trace primitives.

The module deliberately contains no question-evolution business rules.  It is
used by stages to make local I/O, recovery, and concurrency predictable while
leaving prompts, retries, trial numbering, and aggregation semantics unchanged.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import sys
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple, TypeVar


ARTIFACT_FORMAT_VERSION = 1
DEFAULT_FLUSH_RECORDS = 32
DEFAULT_FLUSH_BYTES = 1024 * 1024
DEFAULT_FLUSH_SECONDS = 2.0

T = TypeVar("T")
R = TypeVar("R")


class StageJsonError(ValueError):
    """A JSON input error that identifies the stage, path, and line."""


class ArtifactConflictError(RuntimeError):
    """Raised when existing output/recovery files do not belong to this run."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def digest_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _first_non_whitespace(path: str) -> str:
    with open(path, "r", encoding="utf-8-sig") as source:
        while True:
            chunk = source.read(4096)
            if not chunk:
                return ""
            stripped = chunk.lstrip()
            if stripped:
                return stripped[0]


def iter_json_records(path: str, *, stage: str = "unknown") -> Iterator[Dict[str, Any]]:
    """Stream JSONL records and retain compatibility with historical arrays."""

    if not os.path.exists(path):
        raise FileNotFoundError(f"[{stage}] input file does not exist: {path}")
    first = _first_non_whitespace(path)
    if not first:
        return
    if first == "[":
        try:
            with open(path, "r", encoding="utf-8-sig") as source:
                payload = json.load(source)
        except json.JSONDecodeError as exc:
            raise StageJsonError(
                f"[{stage}] invalid JSON array in {path}:{exc.lineno}: {exc.msg}"
            ) from exc
        if not isinstance(payload, list):
            raise StageJsonError(f"[{stage}] JSON input must be an array: {path}")
        for index, record in enumerate(payload, start=1):
            if not isinstance(record, dict):
                raise StageJsonError(f"[{stage}] record must be an object: {path}:array[{index}]")
            yield record
        return

    with open(path, "r", encoding="utf-8-sig") as source:
        for line_number, line in enumerate(source, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise StageJsonError(
                    f"[{stage}] invalid JSONL record in {path}:{line_number}: {exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise StageJsonError(f"[{stage}] record must be an object: {path}:{line_number}")
            yield record


def load_json_records(path: str, *, stage: str = "unknown") -> List[Dict[str, Any]]:
    return list(iter_json_records(path, stage=stage))


def stable_record_key(record: Dict[str, Any]) -> str:
    """Return a stable per-sample key without collapsing identical prompts."""

    candidate_id = record.get("candidate_id")
    if candidate_id is not None and str(candidate_id).strip():
        return f"candidate:{str(candidate_id).strip()}"
    identity = record.get("sample_id")
    if identity is None or not str(identity).strip():
        identity = record.get("index")
    if identity is None or not str(identity).strip():
        identity = record.get("candidate_group_id")
    prompt = str(record.get("prompt") or "").strip()
    if identity is None or not str(identity).strip():
        return f"prompt:{hashlib.sha256(prompt.encode('utf-8')).hexdigest()}"
    return f"{identity}|||{prompt}"


def candidate_group_key(record: Dict[str, Any]) -> str:
    for field_name in ("candidate_group_id", "sample_id", "index"):
        value = record.get(field_name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return stable_record_key(record)


def passthrough_reuse_errors(
    record: Dict[str, Any],
    *,
    required: Sequence[str] = (
        "meta_info.references",
        "rubric",
        "score_prompt",
        "scoring_result",
        "score_rate",
    ),
) -> List[str]:
    """Return missing/invalid reusable artifacts for a non-evolved sample."""

    if record.get("question_evolved") is not False:
        return []
    errors: List[str] = []
    meta_info = record.get("meta_info")
    references = meta_info.get("references") if isinstance(meta_info, dict) else None
    if "meta_info.references" in required and not (
        isinstance(references, list)
        and references
        and all(isinstance(answer, str) and answer.strip() for answer in references)
    ):
        errors.append("meta_info.references")
    rubric = record.get("rubric")
    if "rubric" in required and (not isinstance(rubric, list) or not rubric):
        errors.append("rubric")
    if "score_prompt" in required and (
        not isinstance(record.get("score_prompt"), str) or not record.get("score_prompt", "").strip()
    ):
        errors.append("score_prompt")
    scoring_result = record.get("scoring_result")
    if "scoring_result" in required and (not isinstance(scoring_result, dict) or not scoring_result):
        errors.append("scoring_result")
    score_rate = record.get("score_rate")
    if "score_rate" in required and (
        not isinstance(score_rate, (int, float)) or not 0 <= float(score_rate) <= 1
    ):
        errors.append("score_rate")
    return errors


def ensure_passthrough_reusable(
    record: Dict[str, Any],
    *,
    stage: str,
    required: Sequence[str] = (
        "meta_info.references",
        "rubric",
        "score_prompt",
        "scoring_result",
        "score_rate",
    ),
) -> None:
    errors = passthrough_reuse_errors(record, required=required)
    if errors:
        raise ValueError(
            f"[{stage}] question_evolved=False but reusable artifacts are incomplete: "
            + ", ".join(errors)
        )


def process_rss_bytes() -> int:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            class ProcessMemoryCounters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = ProcessMemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            psapi = ctypes.WinDLL("psapi", use_last_error=True)
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.c_void_p,
                wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            process = kernel32.GetCurrentProcess()
            ok = psapi.GetProcessMemoryInfo(
                process,
                ctypes.byref(counters),
                counters.cb,
            )
            return int(counters.PeakWorkingSetSize) if ok else 0
        except Exception:
            return 0
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(usage if sys.platform == "darwin" else usage * 1024)
    except Exception:
        return 0


@dataclass
class StageMetrics:
    stage: str
    started_at: float = field(default_factory=time.monotonic)
    input_records: int = 0
    input_bytes: int = 0
    output_records: int = 0
    output_bytes: int = 0
    parse_seconds: float = 0.0
    compute_seconds: float = 0.0
    serialize_seconds: float = 0.0
    recovery_seconds: float = 0.0
    flush_seconds: float = 0.0
    flush_count: int = 0
    checkpoint_hits: int = 0
    input_queue_peak: int = 0
    output_queue_peak: int = 0
    request_pool_peaks: Dict[str, int] = field(default_factory=dict)

    def event(self, *, status: str = "completed") -> Dict[str, Any]:
        return {
            "event_type": "stage_performance",
            "stage": self.stage,
            "status": status,
            "created_at": utc_now(),
            "elapsed_seconds": round(time.monotonic() - self.started_at, 6),
            "input_records": self.input_records + self.checkpoint_hits,
            "processed_input_records": self.input_records,
            "input_bytes": self.input_bytes,
            "output_records": self.output_records,
            "output_bytes": self.output_bytes,
            "parse_seconds": round(self.parse_seconds, 6),
            "compute_seconds": round(self.compute_seconds, 6),
            "serialize_seconds": round(self.serialize_seconds, 6),
            "recovery_seconds": round(self.recovery_seconds, 6),
            "flush_seconds": round(self.flush_seconds, 6),
            "flush_count": self.flush_count,
            "checkpoint_hits": self.checkpoint_hits,
            "input_queue_peak": self.input_queue_peak,
            "output_queue_peak": self.output_queue_peak,
            "request_pool_peaks": dict(self.request_pool_peaks),
            "rss_peak_bytes": process_rss_bytes(),
        }


def append_performance_event(path: Optional[str], event: Dict[str, Any]) -> None:
    if not path:
        return
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a", encoding="utf-8") as target:
        target.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


@dataclass(frozen=True)
class ArtifactPaths:
    output: str

    @property
    def partial(self) -> str:
        return self.output + ".partial"

    @property
    def checkpoint(self) -> str:
        return self.output + ".checkpoint.jsonl"

    @property
    def manifest(self) -> str:
        return self.output + ".manifest.json"


def _file_metadata(path: str) -> Dict[str, Any]:
    return {
        "path": os.path.abspath(path),
        "bytes": os.path.getsize(path),
        "sha256": sha256_file(path),
    }


def read_manifest(output_path: str) -> Optional[Dict[str, Any]]:
    manifest_path = ArtifactPaths(output_path).manifest
    pending_manifest_path = manifest_path + ".tmp"
    if not os.path.exists(manifest_path) and os.path.exists(output_path) and os.path.exists(pending_manifest_path):
        try:
            with open(pending_manifest_path, "r", encoding="utf-8") as source:
                pending_manifest = json.load(source)
            artifact = pending_manifest.get("artifact") if isinstance(pending_manifest, dict) else None
            if (
                isinstance(artifact, dict)
                and artifact.get("bytes") == os.path.getsize(output_path)
                and artifact.get("sha256") == sha256_file(output_path)
            ):
                os.replace(pending_manifest_path, manifest_path)
        except (OSError, json.JSONDecodeError):
            pass
    if not os.path.exists(manifest_path):
        return None
    try:
        with open(manifest_path, "r", encoding="utf-8") as source:
            manifest = json.load(source)
    except (OSError, json.JSONDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def validate_published_artifact(
    output_path: str,
    *,
    stage: Optional[str] = None,
    input_path: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Validate formal output and its manifest before a stage is skipped."""

    if not os.path.isfile(output_path):
        return False, "output_missing"
    manifest = read_manifest(output_path)
    if not manifest:
        return False, "manifest_missing_or_invalid"
    if manifest.get("format_version") != ARTIFACT_FORMAT_VERSION:
        return False, "manifest_version_mismatch"
    if stage and manifest.get("stage") != stage:
        return False, "stage_mismatch"
    if manifest.get("config_sha256") != digest_json(manifest.get("config") or {}):
        return False, "manifest_config_digest_mismatch"
    artifact = manifest.get("artifact")
    if not isinstance(artifact, dict):
        return False, "artifact_metadata_missing"
    if artifact.get("bytes") != os.path.getsize(output_path):
        return False, "artifact_size_mismatch"
    if artifact.get("sha256") != sha256_file(output_path):
        return False, "artifact_digest_mismatch"
    try:
        actual_count = sum(1 for _ in iter_json_records(output_path, stage=stage or "manifest"))
    except (OSError, StageJsonError):
        return False, "artifact_parse_failed"
    if artifact.get("record_count") != actual_count:
        return False, "artifact_record_count_mismatch"
    if input_path:
        input_meta = manifest.get("input")
        if not isinstance(input_meta, dict) or not os.path.exists(input_path):
            return False, "input_metadata_missing"
        if input_meta.get("sha256") != sha256_file(input_path):
            return False, "input_digest_mismatch"
    if config is not None and manifest.get("config_sha256") != digest_json(config):
        return False, "config_digest_mismatch"
    for sidecar in manifest.get("sidecars") or []:
        if not isinstance(sidecar, dict):
            return False, "sidecar_metadata_invalid"
        sidecar_path = sidecar.get("path")
        if not isinstance(sidecar_path, str):
            return False, "sidecar_path_missing"
        if not os.path.isabs(sidecar_path):
            sidecar_path = os.path.join(os.path.dirname(os.path.abspath(output_path)), sidecar_path)
        if not os.path.isfile(sidecar_path):
            return False, "sidecar_missing"
        if sidecar.get("bytes") != os.path.getsize(sidecar_path):
            return False, "sidecar_size_mismatch"
        if sidecar.get("sha256") != sha256_file(sidecar_path):
            return False, "sidecar_digest_mismatch"
    return True, "ok"


class AtomicJsonlStageWriter:
    """Batch JSONL writes to a recoverable partial and atomically publish it."""

    def __init__(
        self,
        output_path: str,
        *,
        stage: str,
        input_path: str,
        config: Optional[Dict[str, Any]] = None,
        code_paths: Sequence[str] = (),
        metrics: Optional[StageMetrics] = None,
        flush_records: int = DEFAULT_FLUSH_RECORDS,
        flush_bytes: int = DEFAULT_FLUSH_BYTES,
        flush_seconds: float = DEFAULT_FLUSH_SECONDS,
    ):
        self.paths = ArtifactPaths(output_path)
        self.stage = stage
        self.input_path = input_path
        self.config = config or {}
        self.config_sha256 = digest_json(self.config)
        self.input_meta = _file_metadata(input_path)
        self.code_meta = [_file_metadata(path) for path in code_paths if os.path.isfile(path)]
        self.code_sha256 = digest_json(self.code_meta)
        self.metrics = metrics or StageMetrics(stage)
        self.flush_records = max(1, int(flush_records))
        self.flush_bytes = max(1, int(flush_bytes))
        self.flush_seconds = max(0.0, float(flush_seconds))
        self._pending: List[Tuple[str, List[bytes]]] = []
        self._pending_keys: set[str] = set()
        self._pending_records = 0
        self._pending_bytes = 0
        self._processed_keys: set[str] = set()
        self._record_count = 0
        self._sidecars: List[Dict[str, Any]] = []
        self._last_flush = time.monotonic()
        self._closed = False

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        recovery_started = time.monotonic()
        self._recover()
        self.metrics.recovery_seconds += time.monotonic() - recovery_started
        self._partial_file = open(self.paths.partial, "ab")
        self._checkpoint_file = open(self.paths.checkpoint, "a", encoding="utf-8")

    @property
    def processed_keys(self) -> set[str]:
        return set(self._processed_keys)

    def _run_identity(self) -> Dict[str, Any]:
        return {
            "format_version": ARTIFACT_FORMAT_VERSION,
            "stage": self.stage,
            "input_sha256": self.input_meta["sha256"],
            "config_sha256": self.config_sha256,
            "code_sha256": self.code_sha256,
        }

    def _recover(self) -> None:
        if os.path.exists(self.paths.output):
            valid, reason = validate_published_artifact(
                self.paths.output,
                stage=self.stage,
                input_path=self.input_path,
                config=self.config,
            )
            if valid:
                raise ArtifactConflictError(f"published artifact already complete: {self.paths.output}")
            raise ArtifactConflictError(
                f"refusing to overwrite unverified artifact {self.paths.output}: {reason}"
            )

        checkpoint_exists = os.path.exists(self.paths.checkpoint)
        partial_exists = os.path.exists(self.paths.partial)
        if checkpoint_exists != partial_exists:
            raise ArtifactConflictError(
                f"partial/checkpoint pair is incomplete for {self.paths.output}; inspect before retry"
            )
        if not checkpoint_exists:
            Path(self.paths.partial).touch()
            with open(self.paths.checkpoint, "w", encoding="utf-8") as checkpoint:
                checkpoint.write(json.dumps({"type": "header", **self._run_identity()}, sort_keys=True) + "\n")
            return

        last_offset = 0
        last_count = 0
        with open(self.paths.checkpoint, "r", encoding="utf-8") as checkpoint:
            header_line = checkpoint.readline()
            try:
                header = json.loads(header_line)
            except json.JSONDecodeError as exc:
                raise ArtifactConflictError(f"invalid checkpoint header: {self.paths.checkpoint}") from exc
            expected = {"type": "header", **self._run_identity()}
            if header != expected:
                raise ArtifactConflictError(
                    f"checkpoint belongs to different input/config/code: {self.paths.checkpoint}"
                )
            for line_number, line in enumerate(checkpoint, start=2):
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ArtifactConflictError(
                        f"invalid checkpoint entry {self.paths.checkpoint}:{line_number}"
                    ) from exc
                key = str(entry.get("key") or "")
                offset = entry.get("end_offset")
                count = entry.get("record_count")
                if not key or not isinstance(offset, int) or not isinstance(count, int):
                    raise ArtifactConflictError(
                        f"invalid checkpoint entry {self.paths.checkpoint}:{line_number}"
                    )
                if offset < last_offset or count < last_count:
                    raise ArtifactConflictError(f"non-monotonic checkpoint: {self.paths.checkpoint}:{line_number}")
                self._processed_keys.add(key)
                last_offset = offset
                last_count = count
        partial_size = os.path.getsize(self.paths.partial)
        if partial_size < last_offset:
            raise ArtifactConflictError("partial is shorter than its last confirmed checkpoint")
        if partial_size > last_offset:
            with open(self.paths.partial, "r+b") as partial:
                partial.truncate(last_offset)
        self._record_count = last_count
        self.metrics.output_records = last_count
        self.metrics.output_bytes = last_offset
        self.metrics.checkpoint_hits = len(self._processed_keys)

    def add_group(self, key: str, records: Sequence[Dict[str, Any]]) -> bool:
        if self._closed:
            raise RuntimeError("writer is closed")
        normalized_key = str(key or "").strip()
        if not normalized_key:
            raise ValueError("checkpoint key must not be empty")
        if normalized_key in self._processed_keys or normalized_key in self._pending_keys:
            return False
        started = time.monotonic()
        encoded = [
            (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            for record in records
        ]
        self.metrics.serialize_seconds += time.monotonic() - started
        self._pending.append((normalized_key, encoded))
        self._pending_keys.add(normalized_key)
        self._pending_records += len(encoded)
        self._pending_bytes += sum(len(line) for line in encoded)
        if (
            self._pending_records >= self.flush_records
            or self._pending_bytes >= self.flush_bytes
            or time.monotonic() - self._last_flush >= self.flush_seconds
        ):
            self.flush()
        return True

    def flush(self) -> None:
        if not self._pending:
            return
        started = time.monotonic()
        entries: List[Dict[str, Any]] = []
        for key, lines in self._pending:
            for line in lines:
                self._partial_file.write(line)
                self._record_count += 1
                self.metrics.output_records += 1
                self.metrics.output_bytes += len(line)
            entries.append(
                {
                    "type": "checkpoint",
                    "key": key,
                    "end_offset": self._partial_file.tell(),
                    "record_count": self._record_count,
                    "created_at": utc_now(),
                }
            )
        self._partial_file.flush()
        os.fsync(self._partial_file.fileno())
        for entry in entries:
            self._checkpoint_file.write(json.dumps(entry, sort_keys=True) + "\n")
            self._processed_keys.add(entry["key"])
        self._checkpoint_file.flush()
        os.fsync(self._checkpoint_file.fileno())
        self._pending.clear()
        self._pending_keys.clear()
        self._pending_records = 0
        self._pending_bytes = 0
        self._last_flush = time.monotonic()
        self.metrics.flush_count += 1
        self.metrics.flush_seconds += time.monotonic() - started

    def register_sidecar(self, path: str, *, kind: str, record_count: Optional[int] = None) -> None:
        metadata = _file_metadata(path)
        output_dir = os.path.dirname(os.path.abspath(self.paths.output))
        absolute_path = os.path.abspath(path)
        try:
            metadata["path"] = os.path.relpath(absolute_path, output_dir)
        except ValueError:
            metadata["path"] = absolute_path
        metadata["kind"] = kind
        if record_count is not None:
            metadata["record_count"] = int(record_count)
        self._sidecars.append(metadata)

    def publish(self, *, extra_manifest: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        self.flush()
        self._partial_file.close()
        self._checkpoint_file.close()
        self._closed = True
        artifact_meta = _file_metadata(self.paths.partial)
        artifact_meta["path"] = os.path.basename(self.paths.output)
        artifact_meta["record_count"] = self._record_count
        manifest: Dict[str, Any] = {
            "format_version": ARTIFACT_FORMAT_VERSION,
            "stage": self.stage,
            "created_at": utc_now(),
            "artifact": artifact_meta,
            "input": self.input_meta,
            "config": self.config,
            "config_sha256": self.config_sha256,
            "code": self.code_meta,
            "code_sha256": self.code_sha256,
            "sidecars": list(self._sidecars),
        }
        if extra_manifest:
            manifest.update(extra_manifest)
        temporary = self.paths.manifest + ".tmp"
        with open(temporary, "w", encoding="utf-8") as target:
            json.dump(manifest, target, ensure_ascii=False, indent=2, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(self.paths.partial, self.paths.output)
        os.replace(temporary, self.paths.manifest)
        os.remove(self.paths.checkpoint)
        return manifest

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        self._partial_file.close()
        self._checkpoint_file.close()
        self._closed = True


class TraceStore:
    """Deduplicated compressed raw-response sidecar."""

    def __init__(self, stage: str, *, recovery_path: Optional[str] = None):
        self.stage = stage
        self._entries: Dict[str, Dict[str, Any]] = {}
        self.recovery_path = recovery_path
        if recovery_path and os.path.exists(recovery_path):
            with open(recovery_path, "r", encoding="utf-8") as source:
                for line_number, line in enumerate(source, start=1):
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as exc:
                        raise ArtifactConflictError(
                            f"invalid trace recovery entry {recovery_path}:{line_number}"
                        ) from exc
                    trace_id = entry.get("trace_id")
                    if isinstance(trace_id, str) and trace_id:
                        self._entries[trace_id] = entry

    def add(self, *, record_key: str, raw_text: str, trace_kind: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        text = str(raw_text or "")
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        trace_source = f"{self.stage}|{record_key}|{trace_kind}|{digest}"
        trace_id = hashlib.sha256(trace_source.encode("utf-8")).hexdigest()
        entry = {
            "trace_id": trace_id,
            "record_key": record_key,
            "stage": self.stage,
            "trace_kind": trace_kind,
            "content_sha256": digest,
            "encoding": "utf-8",
            "raw_text": text,
        }
        if metadata:
            entry["metadata"] = metadata
        if trace_id not in self._entries:
            self._entries[trace_id] = entry
            if self.recovery_path:
                os.makedirs(os.path.dirname(os.path.abspath(self.recovery_path)), exist_ok=True)
                with open(self.recovery_path, "a", encoding="utf-8") as target:
                    target.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                    target.flush()
        return trace_id

    def write(self, path: str) -> Tuple[str, int]:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        temporary = path + ".tmp"
        with gzip.open(temporary, "wt", encoding="utf-8") as target:
            for trace_id in sorted(self._entries):
                target.write(json.dumps(self._entries[trace_id], ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(temporary, path)
        return path, len(self._entries)

    def finalize_recovery(self) -> None:
        if self.recovery_path and os.path.exists(self.recovery_path):
            os.remove(self.recovery_path)


class FairRequestPool:
    """Bound calls and grant one waiter per sample in round-robin order.

    Dispatch is deferred by one event-loop turn when new waiters arrive.  This
    lets sibling trial/repeat tasks enqueue together before free capacity is
    assigned, preventing the first sample from filling every service slot.
    """

    def __init__(self, limit: int, name: str):
        if limit < 1:
            raise ValueError(f"{name} request pool limit must be >= 1")
        self.limit = int(limit)
        self.name = name
        self.active = 0
        self.peak_active = 0
        self.peak_waiters = 0
        self._waiters: Dict[str, deque[asyncio.Future[None]]] = {}
        self._sample_order: deque[str] = deque()
        self._lock = asyncio.Lock()
        self._dispatch_scheduled = False

    def _waiting_count(self) -> int:
        return sum(len(waiters) for waiters in self._waiters.values())

    def _remove_sample_from_order(self, sample_key: str) -> None:
        self._sample_order = deque(key for key in self._sample_order if key != sample_key)

    def _dispatch_locked(self) -> None:
        while self.active < self.limit and self._sample_order:
            sample_key = self._sample_order.popleft()
            waiters = self._waiters.get(sample_key)
            if not waiters:
                self._waiters.pop(sample_key, None)
                continue
            future = waiters.popleft()
            while future.cancelled() and waiters:
                future = waiters.popleft()
            if waiters:
                self._sample_order.append(sample_key)
            else:
                self._waiters.pop(sample_key, None)
            if future.cancelled():
                continue
            self.active += 1
            self.peak_active = max(self.peak_active, self.active)
            future.set_result(None)

    def _schedule_dispatch_locked(self) -> None:
        if self._dispatch_scheduled:
            return
        self._dispatch_scheduled = True
        asyncio.get_running_loop().call_soon(
            lambda: asyncio.create_task(self._dispatch_deferred())
        )

    async def _dispatch_deferred(self) -> None:
        async with self._lock:
            self._dispatch_scheduled = False
            self._dispatch_locked()

    async def acquire(self, sample_key: str) -> None:
        future = asyncio.get_running_loop().create_future()
        normalized_key = str(sample_key or "unknown")
        async with self._lock:
            waiters = self._waiters.get(normalized_key)
            if waiters is None:
                waiters = deque()
                self._waiters[normalized_key] = waiters
                self._sample_order.append(normalized_key)
            waiters.append(future)
            self.peak_waiters = max(self.peak_waiters, self._waiting_count())
            self._schedule_dispatch_locked()
        try:
            await future
        except asyncio.CancelledError:
            async with self._lock:
                waiters = self._waiters.get(normalized_key)
                waiting = waiters is not None and future in waiters
                if waiting and waiters is not None:
                    waiters.remove(future)
                    if not waiters:
                        self._waiters.pop(normalized_key, None)
                        self._remove_sample_from_order(normalized_key)
                else:
                    self.active = max(0, self.active - 1)
                self._dispatch_locked()
            raise

    async def release(self) -> None:
        async with self._lock:
            self.active = max(0, self.active - 1)
            self._dispatch_locked()

    @asynccontextmanager
    async def request(self, sample_key: str) -> AsyncIterator[None]:
        await self.acquire(sample_key)
        try:
            yield
        finally:
            await self.release()


async def bounded_async_map(
    records: Iterable[T],
    worker: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
    on_result: Callable[[int, T, R], Awaitable[None]],
    metrics: Optional[StageMetrics] = None,
    queue_size: Optional[int] = None,
    ordered_results: bool = False,
) -> None:
    """Run a fixed worker set over a bounded reader queue.

    The callback is executed by a single writer task, so stage code does not
    need locks around output/checkpoint state.
    """

    worker_count = max(1, int(concurrency))
    capacity = max(worker_count, int(queue_size or worker_count * 2))
    input_queue: asyncio.Queue[Optional[Tuple[int, T]]] = asyncio.Queue(maxsize=capacity)
    output_queue: asyncio.Queue[Optional[Tuple[int, T, R, float]]] = asyncio.Queue(maxsize=capacity)
    # Bound every record submitted but not yet committed by the single writer.
    # This also prevents an ordered-result buffer from growing behind one slow item.
    inflight_slots = asyncio.Semaphore(capacity)
    active_workers = worker_count

    async def reader_task() -> None:
        iterator = iter(records)
        sequence = 0
        while True:
            parse_started = time.monotonic()
            try:
                record = next(iterator)
            except StopIteration:
                if metrics:
                    metrics.parse_seconds += time.monotonic() - parse_started
                break
            if metrics:
                metrics.parse_seconds += time.monotonic() - parse_started
            await inflight_slots.acquire()
            try:
                await input_queue.put((sequence, record))
            except BaseException:
                inflight_slots.release()
                raise
            if metrics:
                metrics.input_records += 1
                metrics.input_queue_peak = max(metrics.input_queue_peak, input_queue.qsize())
            sequence += 1
        for _ in range(worker_count):
            await input_queue.put(None)

    async def worker_task() -> None:
        nonlocal active_workers
        while True:
            task = await input_queue.get()
            try:
                if task is None:
                    break
                sequence, record = task
                started = time.monotonic()
                result = await worker(record)
                await output_queue.put((sequence, record, result, time.monotonic() - started))
                if metrics:
                    metrics.output_queue_peak = max(metrics.output_queue_peak, output_queue.qsize())
            finally:
                input_queue.task_done()
        active_workers -= 1
        if active_workers == 0:
            await output_queue.put(None)

    async def writer_task() -> None:
        next_sequence = 0
        buffered: Dict[int, Tuple[T, R, float]] = {}
        while True:
            result = await output_queue.get()
            try:
                if result is None:
                    if ordered_results:
                        while next_sequence in buffered:
                            record, value, compute_seconds = buffered.pop(next_sequence)
                            if metrics:
                                metrics.compute_seconds += compute_seconds
                            await on_result(next_sequence, record, value)
                            inflight_slots.release()
                            next_sequence += 1
                    break
                sequence, record, value, compute_seconds = result
                if ordered_results:
                    buffered[sequence] = (record, value, compute_seconds)
                    while next_sequence in buffered:
                        ordered_record, ordered_value, ordered_seconds = buffered.pop(next_sequence)
                        if metrics:
                            metrics.compute_seconds += ordered_seconds
                        await on_result(next_sequence, ordered_record, ordered_value)
                        inflight_slots.release()
                        next_sequence += 1
                else:
                    if metrics:
                        metrics.compute_seconds += compute_seconds
                    await on_result(sequence, record, value)
                    inflight_slots.release()
            finally:
                output_queue.task_done()

    await asyncio.gather(
        reader_task(),
        *(worker_task() for _ in range(worker_count)),
        writer_task(),
    )


def publish_records(
    records: Iterable[Dict[str, Any]],
    output_path: str,
    *,
    stage: str,
    input_path: str,
    config: Optional[Dict[str, Any]] = None,
    key_fn: Callable[[Dict[str, Any]], str] = stable_record_key,
    performance_path: Optional[str] = None,
    code_paths: Sequence[str] = (),
    metrics: Optional[StageMetrics] = None,
    sidecars: Sequence[Tuple[str, str, Optional[int]]] = (),
) -> Dict[str, Any]:
    """Publish a local synchronous stage through the common artifact contract."""

    resolved_config = config or {}
    valid, _ = validate_published_artifact(
        output_path,
        stage=stage,
        input_path=input_path,
        config=resolved_config,
    )
    if valid:
        manifest = read_manifest(output_path)
        return manifest or {}

    metrics = metrics or StageMetrics(stage)
    metrics.input_bytes = os.path.getsize(input_path)
    writer = AtomicJsonlStageWriter(
        output_path,
        stage=stage,
        input_path=input_path,
        config=resolved_config,
        code_paths=code_paths,
        metrics=metrics,
    )
    try:
        iterator = iter(records)
        while True:
            parse_started = time.monotonic()
            try:
                record = next(iterator)
            except StopIteration:
                metrics.parse_seconds += time.monotonic() - parse_started
                break
            metrics.parse_seconds += time.monotonic() - parse_started
            if writer.add_group(key_fn(record), [record]):
                metrics.input_records += 1
        for sidecar_path, sidecar_kind, sidecar_count in sidecars:
            writer.register_sidecar(
                sidecar_path,
                kind=sidecar_kind,
                record_count=sidecar_count,
            )
        manifest = writer.publish()
    except Exception:
        writer.close()
        append_performance_event(performance_path, metrics.event(status="failed"))
        raise
    append_performance_event(performance_path, metrics.event())
    return manifest
