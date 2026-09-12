"""Stage-4 global strategy memory.

SQLite is authoritative.  The JSON/Markdown files in ``memory_global`` are
atomic, human-readable projections and are never used to repair the database.
The module deliberately produces shadow/proposed evidence only: it never
changes pipeline routing, scoring, or publishes an active strategy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence

from .context_cache import memory_context_key


TAXONOMY_VERSION = "global-memory-taxonomy-v1"
# Bumped from v1: retrieval now uses weighted field precedence, hard exclusion
# filtering, freshness decay and conflict/risk penalties (report M-2).
RETRIEVAL_CONFIG_VERSION = "global-memory-retrieval-v2"
CARD_TYPES = {"positive_strategy", "negative_strategy", "risk_pattern", "system_diagnosis", "optimization_signal"}
STAGE4_STATUSES = {"proposed", "shadow", "qualified", "needs_human_review", "rejected_insufficient_evidence", "downgraded", "retired"}
# L1 memory holds *experiment facts* only (design §13.2).
LOCAL_SOURCES = {
    "operator_memory_bank.jsonl": "positive_strategy",
    "failure_memory_bank.jsonl": "negative_strategy",
    "invalid_generation_cases.jsonl": "risk_pattern",
    "operator_performance.jsonl": "optimization_signal",
    "mechanism_publish_candidates.jsonl": "mechanism_publish_candidate",
}
# Control-plane observations are explicitly *not* L1 experiment facts.  They are
# listed separately so the exclusion is auditable; ``agent_observation.json`` is
# rewritten on every Agent run, so treating it as a fact source also made it
# permanently look "rewritten" to the watermark guard (report M-4 / M-10).
CONTROL_PLANE_SOURCES = {
    "agent_observation.json": "system_diagnosis",
}

# Retrieval field precedence (design §13.6).  The declared order *is* the
# contract: reasoning mechanism > question form > exclusion conditions >
# evidence strength > overscore pattern > scene family.
RETRIEVAL_FIELD_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("reasoning_mechanism", 8.0),
    ("question_form", 6.0),
    ("exclusion_conditions", 5.0),
    ("evidence_strength", 4.0),
    ("overscore_pattern", 3.0),
    ("scene_family", 2.0),
)
RETRIEVAL_FRESHNESS_WEIGHT = 1.0
RETRIEVAL_FRESHNESS_HALFLIFE_DAYS = 30.0
RETRIEVAL_CONFLICT_PENALTY = 2.0
# Hard ceiling on the memory payload injected into the control layer.
RETRIEVAL_CONTEXT_CHAR_BUDGET = 6000
RETRIEVAL_STATUS_PENALTIES = {
    "needs_human_review": 1.0,
    "downgraded": 1.5,
    "rejected_insufficient_evidence": 2.0,
    "proposed": 0.0,
    "shadow": 0.0,
    "qualified": 0.0,
}
# A card whose own exclusion condition matches the query is dropped outright
# instead of merely being reported as a passthrough field (report M-2).
RETRIEVAL_ENFORCE_EXCLUSIONS = True

# ``needs_human_review`` is the only decision that can repeat indefinitely for
# the same source and reason; it is deduplicated so the audit log cannot grow
# without bound (report M-4).
DEDUPLICATED_DECISIONS = {"needs_human_review"}

_TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(value: Any) -> set[str]:
    """CJK-aware tokenization.

    ``query.lower().split()`` is effectively a no-op for Chinese text because
    Chinese has no inter-word spaces, so the previous retriever matched almost
    nothing on real queries (report M-2).  ASCII runs become word tokens and
    every CJK character becomes a unigram -- the standard cheap fallback when no
    segmenter is available.
    """

    return set(_TOKEN_PATTERN.findall(str(value).lower()))


def _overlap_ratio(tokens: set[str], value: Any) -> float:
    if not tokens:
        return 0.0
    return len(tokens & _tokenize(value)) / len(tokens)


def _parse_timestamp(value: Any) -> datetime:
    """Parse an ISO timestamp, falling back to "now" for unknown values."""

    text = _text(value)
    if text:
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            parsed = None
        if parsed is not None:
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc)



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _file_hash(path: Path, *, lines: int | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        if lines is None:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
        else:
            for index, line in enumerate(handle):
                if index >= lines:
                    break
                digest.update(line)
    return "sha256:" + digest.hexdigest()


def _card_fingerprint(card: Mapping[str, Any]) -> str:
    """Content address of a strategy card as stored in the authoritative DB."""

    return _hash(card)


def _matches_frozen_card(card: Mapping[str, Any], fingerprints: Mapping[str, Any], allowed: Mapping[str, Any]) -> bool:
    """Return True when a card still matches its frozen snapshot identity."""

    card_id = str(card.get("card_id"))
    entry = fingerprints.get(card_id)
    if isinstance(entry, Mapping):
        if int(card.get("version") or 0) != int(entry.get("version") or 0):
            return False
        return _card_fingerprint(card) == entry.get("body_sha256")
    # Legacy snapshots froze only the version.  They still must not serve a
    # card whose version advanced after the snapshot was taken.
    recorded = allowed.get(card_id)
    return recorded is None or int(card.get("version") or 0) == int(recorded)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _card_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "card_id": row["card_id"],
        "status": row["status"],
        "version": row["version"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        **json.loads(row["body"]),
        "evidence_refs": json.loads(row["evidence_refs"]),
    }


def _fact_outcome(fact: Mapping[str, Any]) -> dict[str, bool]:
    """Extract structured outcome signals from one candidate fact.

    ``effective_rate`` / ``invalid_generation_rate`` used to be hard-coded
    ``0.0`` placeholders and ``score_increased_rate`` was a substring search over
    free text (report M-3).  The signals below come from the structured
    ``effect_analysis`` / ``validation_result`` objects the pipeline publishes,
    falling back to the typed ``failure_type`` field only.
    """

    payload = _as_mapping(fact.get("payload"))
    effect = _as_mapping(payload.get("effect_analysis"))
    validation = _as_mapping(payload.get("validation_result"))
    failure_type = _text(payload.get("failure_type")).lower()
    direction = _text(effect.get("effect_direction")).lower()
    increased = any(
        (
            effect.get("score_increased_after_evolution") is True,
            payload.get("score_increased_after_evolution") is True,
            direction == "score_increased",
            failure_type == "score_increased",
            _text(payload.get("status")).lower() == "score_increased",
        )
    )
    decreased = any(
        (
            effect.get("score_decreased_after_evolution") is True,
            payload.get("score_decreased_after_evolution") is True,
            direction == "score_decreased",
            failure_type == "score_decreased",
        )
    )
    invalid = any(
        (
            validation.get("passed") is False,
            failure_type in {"invalid_generation", "validation_failed", "invalid_complexity"},
            _text(payload.get("generation_status")).lower() in {"invalid", "failed"},
        )
    )
    return {"increased": increased, "decreased": decreased, "invalid": invalid}


def _rate(count: int, total: int, *, measured: bool) -> float | None:
    """Return a real rate, or ``None`` when nothing was actually measured."""

    if not measured or total <= 0:
        return None
    return round(count / total, 6)


def _claim_level(*, status: str, fact_count: int, supporting_samples: int) -> str:
    if status == "qualified":
        return "human_reviewed"
    if supporting_samples >= 3:
        return "multi_sample"
    if fact_count >= 2:
        return "multi_observation"
    return "single_observation"


def _risk_labels(*, increased: bool, invalid: bool, fact_count: int, supporting_experiments: int, has_mechanism: bool) -> list[str]:
    labels: list[str] = []
    if increased:
        labels.append("score_increase_observed")
    if invalid:
        labels.append("invalid_generation_observed")
    if fact_count < 2:
        labels.append("insufficient_evidence")
    if supporting_experiments < 2:
        labels.append("single_experiment")
    if not has_mechanism:
        labels.append("unverified_mechanism")
    return labels


class GlobalMemoryError(RuntimeError):
    pass


class AdmissionRejected(GlobalMemoryError):
    pass


class LeaseUnavailable(GlobalMemoryError):
    pass


class SnapshotUnavailable(GlobalMemoryError):
    pass


def validate_stage4_card(card: Mapping[str, Any]) -> None:
    """Reject malformed cards and every attempt to activate one in Stage 4."""

    card_type = _text(card.get("card_type"))
    if card_type not in CARD_TYPES:
        raise AdmissionRejected(f"unsupported strategy-card type: {card_type}")
    status = _text(card.get("status"))
    if status == "active":
        raise AdmissionRejected("active strategy publication is reserved for Stage 5 governance")
    if status not in STAGE4_STATUSES:
        raise AdmissionRejected(f"unsupported Stage-4 strategy-card status: {status}")
    if not isinstance(card.get("applicability_conditions"), list) or not card["applicability_conditions"]:
        raise AdmissionRejected("strategy card requires applicability_conditions")
    if not isinstance(card.get("exclusion_conditions"), list) or not card["exclusion_conditions"]:
        raise AdmissionRejected("strategy card requires exclusion_conditions")
    if not isinstance(card.get("evidence_refs"), list) or not card["evidence_refs"]:
        raise AdmissionRejected("strategy card requires evidence_refs")


class GlobalMemoryStore:
    """Authoritative Stage-4 memory storage and deterministic projections."""

    def __init__(self, project_root: str | Path, *, initialize: bool = True, read_only: bool = False) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / "memory_global"
        self.db_path = self.root / "global_memory_state.sqlite"
        self.read_only = bool(read_only)
        self._schema_ready = False
        if self.read_only and not self.db_path.is_file():
            raise GlobalMemoryError(f"global memory database is unavailable: {self.db_path}")
        if initialize:
            self.initialize()

    def initialize(self) -> None:
        """Create the store directory and schema on demand.

        Construction used to ``mkdir`` and create tables unconditionally, so
        merely importing or probing the class mutated the filesystem and could
        not be used by a read-only inspector (report M-8).  Initialization is
        now explicit, idempotent, and skipped entirely in read-only mode.
        """

        if self._schema_ready:
            return
        if self.read_only:
            self._schema_ready = True
            return
        self.root.mkdir(parents=True, exist_ok=True)
        # Mark ready *before* the DDL: ``_initialize`` borrows ``_connect``, and
        # flipping the flag afterwards would make the two methods recurse.
        self._schema_ready = True
        try:
            self._initialize()
        except BaseException:
            self._schema_ready = False
            raise

    def _require_connection_ready(self) -> None:
        if self._schema_ready:
            return
        if self.db_path.is_file():
            # An existing database already carries its schema; opening it must
            # not create, migrate, or otherwise touch the filesystem.
            self._schema_ready = True
            return
        if self.read_only:
            raise GlobalMemoryError(f"global memory database is unavailable: {self.db_path}")
        raise GlobalMemoryError("global memory store is not initialized; call initialize() first")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._require_connection_ready()
        if self.read_only:
            connection = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True, timeout=20)
        else:
            connection = sqlite3.connect(self.db_path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS candidate_facts (
                  candidate_id TEXT PRIMARY KEY, content_hash TEXT NOT NULL UNIQUE,
                  source_ref TEXT NOT NULL, source_file TEXT NOT NULL, source_line INTEGER NOT NULL,
                  source_experiment TEXT NOT NULL, sample_id TEXT, round_value TEXT, branch_id TEXT,
                  operator_id TEXT, fact_type TEXT NOT NULL, conclusion TEXT NOT NULL,
                  classification_hints TEXT NOT NULL, evidence_refs TEXT NOT NULL, payload TEXT NOT NULL,
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS admission_log (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, source_ref TEXT NOT NULL, decision TEXT NOT NULL,
                  reason TEXT NOT NULL, candidate_id TEXT, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS watermarks (
                  source_file TEXT PRIMARY KEY, last_line INTEGER NOT NULL, content_hash TEXT NOT NULL,
                  prefix_hash TEXT NOT NULL, last_processed_at TEXT NOT NULL, status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cards (
                  card_id TEXT PRIMARY KEY, card_type TEXT NOT NULL, status TEXT NOT NULL, version INTEGER NOT NULL,
                  fingerprint TEXT NOT NULL UNIQUE, body TEXT NOT NULL, evidence_refs TEXT NOT NULL,
                  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS card_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, card_id TEXT NOT NULL, previous_status TEXT,
                  new_status TEXT NOT NULL, reason TEXT NOT NULL, evidence_refs TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jobs (
                  job_key TEXT PRIMARY KEY, job_id TEXT NOT NULL, job_type TEXT NOT NULL, source_exp_dir TEXT NOT NULL,
                  status TEXT NOT NULL, lease_owner TEXT NOT NULL, lease_expires_at TEXT NOT NULL,
                  retry_count INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sequences (
                  name TEXT PRIMARY KEY, value INTEGER NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);
                CREATE INDEX IF NOT EXISTS idx_admission_log_lookup ON admission_log(source_ref, decision, reason);
                CREATE INDEX IF NOT EXISTS idx_candidate_facts_source ON candidate_facts(source_file);
                """
            )

    def _log(self, con: sqlite3.Connection, source_ref: str, decision: str, reason: str, candidate_id: str | None = None) -> None:
        if decision in DEDUPLICATED_DECISIONS:
            existing = con.execute(
                "SELECT id FROM admission_log WHERE source_ref = ? AND decision = ? AND reason = ? ORDER BY id DESC LIMIT 1",
                (source_ref, decision, reason),
            ).fetchone()
            if existing:
                # Same source, same reason: refresh the single open review item
                # instead of appending an unbounded duplicate (report M-4).
                con.execute(
                    "UPDATE admission_log SET created_at = ?, candidate_id = ? WHERE id = ?",
                    (_now(), candidate_id, existing["id"]),
                )
                return
        con.execute(
            "INSERT INTO admission_log(source_ref, decision, reason, candidate_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (source_ref, decision, reason, candidate_id, _now()),
        )

    def acquire_lease(self, *, job_type: str, source_exp_dir: str, owner: str | None = None, seconds: int = 300) -> str:
        """Lease Phase 1 per experiment, or the single global Phase 2 compiler."""

        self._require_writable()
        owner = owner or "worker-" + uuid.uuid4().hex[:10]
        source = str(Path(source_exp_dir).resolve()) if source_exp_dir else "__global__"
        key = f"{job_type}:{source}"
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=seconds)
        with self._connect() as con:
            existing = con.execute("SELECT * FROM jobs WHERE job_key = ?", (key,)).fetchone()
            if existing and existing["status"] == "running" and datetime.fromisoformat(existing["lease_expires_at"]) > now:
                raise LeaseUnavailable(f"lease already held for {key}")
            job_id = "gm-job-" + uuid.uuid4().hex[:12]
            retries = int(existing["retry_count"]) + 1 if existing and existing["status"] == "failed" else 0
            con.execute(
                """INSERT INTO jobs(job_key, job_id, job_type, source_exp_dir, status, lease_owner, lease_expires_at, retry_count, updated_at)
                   VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)
                   ON CONFLICT(job_key) DO UPDATE SET job_id=excluded.job_id, status='running', lease_owner=excluded.lease_owner,
                   lease_expires_at=excluded.lease_expires_at, retry_count=excluded.retry_count, updated_at=excluded.updated_at""",
                (key, job_id, job_type, source, owner, expires.isoformat(), retries, _now()),
            )
        return job_id

    def finish_lease(self, *, job_type: str, source_exp_dir: str, success: bool) -> None:
        self._require_writable()
        source = str(Path(source_exp_dir).resolve()) if source_exp_dir else "__global__"
        with self._connect() as con:
            con.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE job_key = ?", ("completed" if success else "failed", _now(), f"{job_type}:{source}"))

    def _watermark(self, con: sqlite3.Connection, source: Path) -> sqlite3.Row | None:
        return con.execute("SELECT * FROM watermarks WHERE source_file = ?", (str(source.resolve()),)).fetchone()

    @staticmethod
    def _read_jsonl(path: Path, start_line: int) -> tuple[list[tuple[int, dict[str, Any]]], list[tuple[int, str]]]:
        """Read new records; unparseable lines are quarantined, not fatal.

        A single corrupt line (for example from a crashed writer) used to raise
        and block the whole extraction forever.  Quarantining the line keeps the
        source processable while the ``needs_human_review`` audit entry makes
        the defect explicit (report: memory bad-line isolation).
        """

        records: list[tuple[int, dict[str, Any]]] = []
        quarantined: list[tuple[int, str]] = []
        with path.open("r", encoding="utf-8") as handle:
            for number, raw in enumerate(handle, 1):
                if number <= start_line or not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    quarantined.append((number, f"invalid JSON: {exc.msg}"))
                    continue
                if not isinstance(value, Mapping):
                    quarantined.append((number, "line is not a JSON object"))
                    continue
                records.append((number, dict(value)))
        return records, quarantined

    def _candidate_from_record(self, record: Mapping[str, Any], *, source: Path, line: int, experiment: Path, fact_type: str) -> dict[str, Any]:
        if fact_type == "mechanism_publish_candidate":
            taxonomy = _as_mapping(record.get("taxonomy"))
            operators = [operator for operator in record.get("operator_ids") or [] if _text(operator)]
            target_type = _text(record.get("target_card_type"))
            if target_type not in CARD_TYPES:
                target_type = "system_diagnosis"
            return {
                "source_ref": f"{source.resolve()}#{line}", "source_file": str(source.resolve()), "source_line": line,
                "source_experiment": str(experiment.resolve()), "sample_id": "", "round": "", "branch_id": "",
                "operator_id": operators[0] if operators else "", "fact_type": target_type,
                "conclusion": _text(record.get("mechanism_summary")) or "Published mechanism candidate",
                "classification_hints": {
                    "scene_family": _text(taxonomy.get("scene_family")), "question_form": _text(taxonomy.get("question_form")),
                    "reasoning_mechanism": _text(taxonomy.get("reasoning_mechanism")) or _text(record.get("mechanism_id")),
                    "overscore_pattern": _text(taxonomy.get("overscore_pattern")),
                    "applicability_conditions": list(record.get("applicability_conditions") or []),
                    "exclusion_conditions": list(record.get("exclusion_conditions") or []),
                },
                "evidence_refs": list(record.get("evidence_refs") or []),
                "payload": {key: value for key, value in record.items() if key not in {"prompt", "reference_answer", "scoring_result", "rubric", "score_prompt"}},
            }
        signature = _as_mapping(record.get("sample_signature"))
        effect = _as_mapping(record.get("effect_analysis"))
        metadata = _as_mapping(record.get("meta_info")).get("question_evolution_metadata", {})
        metadata = _as_mapping(metadata)
        operator = _text(record.get("operator_used")) or _text(record.get("operator_id")) or _text(effect.get("operator_used"))
        conclusion = _text(record.get("failure_reason")) or _text(record.get("reuse_note")) or _text(record.get("reason"))
        if not conclusion:
            conclusion = f"Observed {fact_type.replace('_', ' ')} for {operator or 'an unspecified operator'}"
        applicability = record.get("applicability_conditions") or metadata.get("applicability_conditions") or signature.get("applicability_conditions") or []
        exclusions = record.get("exclusion_conditions") or metadata.get("exclusion_conditions") or signature.get("exclusion_conditions") or []
        if isinstance(applicability, str):
            applicability = [applicability]
        if isinstance(exclusions, str):
            exclusions = [exclusions]
        return {
            "source_ref": f"{source.resolve()}#{line}", "source_file": str(source.resolve()), "source_line": line,
            "source_experiment": str(experiment.resolve()), "sample_id": str(record.get("sample_id") or record.get("index") or ""),
            "round": record.get("round"), "branch_id": str(record.get("branch_id") or record.get("candidate_id") or ""),
            "operator_id": operator, "fact_type": fact_type, "conclusion": conclusion,
            "classification_hints": {
                "scene_family": _text(signature.get("scene_family")) or _text(record.get("scene_family")),
                "question_form": _text(signature.get("question_form")) or _text(record.get("surface_form_family")),
                "reasoning_mechanism": _text(signature.get("reasoning_mechanism")) or _text(metadata.get("expected_qwen_failure")),
                "overscore_pattern": _text(signature.get("overscore_pattern")) or _text(record.get("failure_type")),
                "applicability_conditions": [item for item in applicability if _text(item)],
                "exclusion_conditions": [item for item in exclusions if _text(item)],
            },
            "evidence_refs": [{"artifact_ref": f"{source.resolve()}#{line}", "sample_id": str(record.get("sample_id") or record.get("index") or ""), "branch_id": str(record.get("branch_id") or record.get("candidate_id") or "")}],
            "payload": {key: value for key, value in record.items() if key not in {"prompt", "reference_answer", "scoring_result", "rubric", "score_prompt"}},
        }

    def _admit(self, con: sqlite3.Connection, candidate: Mapping[str, Any]) -> bool:
        source_ref = _text(candidate.get("source_ref"))
        evidence = candidate.get("evidence_refs")
        if not source_ref or not isinstance(evidence, list) or not evidence:
            self._log(con, source_ref or "unknown", "excluded", "candidate lacks a traceable evidence reference")
            return False
        if any(key in candidate for key in ("prompt", "reference_answer", "scoring_result", "rubric", "score_prompt")):
            self._log(con, source_ref, "excluded", "complete sample content is reconstructible and not admissible")
            return False
        canonical = {key: candidate.get(key) for key in ("source_ref", "fact_type", "conclusion", "classification_hints", "evidence_refs")}
        content_hash = _hash(canonical)
        candidate_id = "mem-candidate-" + content_hash.split(":", 1)[1][:16]
        existing = con.execute("SELECT 1 FROM candidate_facts WHERE content_hash = ?", (content_hash,)).fetchone()
        if existing:
            self._log(con, source_ref, "excluded", "duplicate candidate fact", candidate_id)
            return False
        con.execute(
            """INSERT INTO candidate_facts(candidate_id, content_hash, source_ref, source_file, source_line, source_experiment, sample_id, round_value, branch_id, operator_id, fact_type, conclusion, classification_hints, evidence_refs, payload, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (candidate_id, content_hash, source_ref, candidate["source_file"], candidate["source_line"], candidate["source_experiment"], candidate.get("sample_id"), str(candidate.get("round") or ""), candidate.get("branch_id"), candidate.get("operator_id"), candidate["fact_type"], candidate["conclusion"], _json(candidate["classification_hints"]), _json(evidence), _json(candidate["payload"]), _now()),
        )
        self._log(con, source_ref, "included", "non-reconstructible, traceable strategy evidence", candidate_id)
        return True

    def extract(self, experiment_dir: str | Path) -> dict[str, Any]:
        """Incrementally extract local facts.  Watermarks advance only on success."""

        experiment = Path(experiment_dir).resolve()
        # Mode and initialization are preconditions of the operation, so they
        # are enforced before any argument is inspected.
        self._require_writable()
        self.initialize()
        if not experiment.is_dir():
            raise GlobalMemoryError(f"experiment directory does not exist: {experiment}")
        # L1 is experiment facts only; control-plane observations are excluded
        # by construction (report M-10).
        job = self.acquire_lease(job_type="phase1_extract", source_exp_dir=str(experiment))
        included = excluded = rewritten = quarantined = 0
        try:
            sources = [path for path in experiment.rglob("*") if path.is_file() and path.name in LOCAL_SOURCES]
            with self._connect() as con:
                for source in sorted(sources):
                    fact_type = LOCAL_SOURCES[source.name]
                    watermark = self._watermark(con, source)
                    lines = source.read_text(encoding="utf-8").splitlines()
                    line_count = len(lines)
                    content_hash = _file_hash(source)
                    previous_lines = int(watermark["last_line"]) if watermark else 0
                    if watermark and line_count < previous_lines:
                        self._log(con, str(source), "needs_human_review", "source_rewritten: line count moved backwards")
                        rewritten += 1
                        continue
                    if watermark and line_count >= previous_lines and _file_hash(source, lines=previous_lines) != watermark["prefix_hash"]:
                        self._log(con, str(source), "needs_human_review", "source_rewritten: previously processed prefix changed")
                        rewritten += 1
                        continue
                    if watermark and line_count == previous_lines and content_hash == watermark["content_hash"]:
                        continue
                    before = included
                    if source.suffix == ".jsonl":
                        records, bad_lines = self._read_jsonl(source, previous_lines)
                    else:
                        try:
                            raw = json.loads(source.read_text(encoding="utf-8"))
                        except json.JSONDecodeError as exc:
                            raise GlobalMemoryError(f"invalid JSON in {source}: {exc.msg}") from exc
                        records, bad_lines = ([] if previous_lines else [(1, _as_mapping(raw))]), []
                    for number, reason in bad_lines:
                        self._log(con, f"{source.resolve()}#{number}", "needs_human_review", f"unparseable line quarantined: {reason}")
                        quarantined += 1
                    for number, record in records:
                        candidate = self._candidate_from_record(record, source=source, line=number, experiment=experiment, fact_type=fact_type)
                        if self._admit(con, candidate):
                            included += 1
                        else:
                            excluded += 1
                    # Update only after all candidates for this source were safely inserted.
                    con.execute(
                        """INSERT INTO watermarks(source_file,last_line,content_hash,prefix_hash,last_processed_at,status) VALUES (?, ?, ?, ?, ?, 'ok')
                           ON CONFLICT(source_file) DO UPDATE SET last_line=excluded.last_line,content_hash=excluded.content_hash,prefix_hash=excluded.prefix_hash,last_processed_at=excluded.last_processed_at,status='ok'""",
                        (str(source), line_count, content_hash, _file_hash(source, lines=line_count), _now()),
                    )
            self.finish_lease(job_type="phase1_extract", source_exp_dir=str(experiment), success=True)
        except BaseException:
            self.finish_lease(job_type="phase1_extract", source_exp_dir=str(experiment), success=False)
            raise
        self.publish_projections()
        return {"job_id": job, "included": included, "excluded": excluded, "source_rewritten": rewritten, "quarantined_lines": quarantined}

    def _resolve_source(self, source_file: str) -> Path:
        target = Path(source_file)
        return target.resolve() if target.is_absolute() else (self.project_root / target).resolve()

    def bless_source(self, source_file: str, *, reason: str = "human_confirmed_prefix_rewrite") -> dict[str, Any]:
        """Close an open ``source_rewritten`` review and re-baseline its watermark.

        ``source_rewritten`` used to be permanent: the watermark never advanced,
        so every later ``extract`` re-flagged the same file and appended another
        review row (report M-4).  This is the missing human resolution path --
        it clears the open review items for that source, records a ``blessed``
        decision, and adopts the current file prefix as the new baseline.
        """

        self._require_writable()
        self.initialize()
        target = self._resolve_source(source_file)
        if not target.is_file():
            raise GlobalMemoryError(f"source file does not exist: {target}")
        lines = len(target.read_text(encoding="utf-8").splitlines())
        with self._connect() as con:
            watermark = self._watermark(con, target)
            if watermark is None:
                raise GlobalMemoryError(f"source has no watermark: {target}")
            con.execute(
                "DELETE FROM admission_log WHERE source_ref = ? AND decision = 'needs_human_review'",
                (str(target),),
            )
            self._log(con, str(target), "blessed", reason)
            con.execute(
                """UPDATE watermarks SET last_line = ?, content_hash = ?, prefix_hash = ?,
                   last_processed_at = ?, status = 'ok' WHERE source_file = ?""",
                (lines, _file_hash(target), _file_hash(target, lines=lines), _now(), str(target)),
            )
        self.publish_projections()
        return {"source_file": str(target), "last_line": lines, "status": "ok"}

    def reset_watermark(self, source_file: str) -> dict[str, Any]:
        """Delete a source watermark so the next ``extract`` re-reads the file."""

        self._require_writable()
        self.initialize()
        target = self._resolve_source(source_file)
        with self._connect() as con:
            cursor = con.execute("DELETE FROM watermarks WHERE source_file = ?", (str(target),))
            if not cursor.rowcount:
                raise GlobalMemoryError(f"source has no watermark: {target}")
            con.execute(
                "DELETE FROM admission_log WHERE source_ref = ? AND decision = 'needs_human_review'",
                (str(target),),
            )
            self._log(con, str(target), "watermark_reset", "watermark cleared for re-admission")
        self.publish_projections()
        return {"source_file": str(target), "status": "reset"}

    def _require_writable(self) -> None:
        if self.read_only:
            raise GlobalMemoryError("global memory store is open in read-only mode")

    @staticmethod
    def _max_legacy_card_number(con: sqlite3.Connection) -> int:
        """Highest numeric suffix among existing cards, tolerating odd ids."""

        highest = 0
        for row in con.execute("SELECT card_id FROM cards").fetchall():
            match = re.search(r"(\d+)\s*$", str(row["card_id"]))
            if match:
                highest = max(highest, int(match.group(1)))
        return highest

    def _next_card_id(self, con: sqlite3.Connection) -> str:
        """Allocate the next card id from a dedicated sequence (report M-6).

        The previous implementation took ``ORDER BY card_id DESC LIMIT 1`` and
        called ``int(card_id.split("-")[-1])``, which raises ``ValueError`` as
        soon as any non-``GMEM-####`` id exists in the table.  A monotonic
        sequence, seeded from whatever ids already exist, cannot be poisoned by
        a single malformed row.
        """

        row = con.execute("SELECT value FROM sequences WHERE name = 'card_id'").fetchone()
        next_value = (int(row["value"]) if row else self._max_legacy_card_number(con)) + 1
        con.execute(
            """INSERT INTO sequences(name, value, updated_at) VALUES ('card_id', ?, ?)
               ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
            (next_value, _now()),
        )
        return f"GMEM-{next_value:06d}"

    def _facts(self, con: sqlite3.Connection) -> list[dict[str, Any]]:
        rows = con.execute("SELECT * FROM candidate_facts ORDER BY candidate_id").fetchall()
        return [
            {
                **dict(row),
                "classification_hints": json.loads(row["classification_hints"]),
                "evidence_refs": json.loads(row["evidence_refs"]),
                "payload": json.loads(row["payload"]),
            }
            for row in rows
        ]

    def integrate(self) -> dict[str, int]:
        """Serial Phase-2 compilation and conservative card lifecycle evaluation."""

        self._require_writable()
        self.initialize()
        self.acquire_lease(job_type="phase2_integrate", source_exp_dir="")
        report: Counter[str] = Counter()
        try:
            with self._connect() as con:
                grouped: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = {}
                for fact in self._facts(con):
                    hints = fact["classification_hints"]
                    key = (fact["fact_type"], hints.get("scene_family", ""), hints.get("question_form", ""), hints.get("reasoning_mechanism", ""), fact.get("operator_id") or "")
                    grouped.setdefault(key, []).append(fact)
                active_fingerprints: set[str] = set()
                for key, facts in grouped.items():
                    card_type, scene, form, mechanism, operator = key
                    if card_type not in CARD_TYPES:
                        continue
                    hints = facts[0]["classification_hints"]
                    applicability = hints.get("applicability_conditions") or ["Use only when the scene, form, and reasoning mechanism match the cited evidence."]
                    exclusions = hints.get("exclusion_conditions") or ["Do not use when the cited evidence cannot be independently verified."]
                    refs = [ref for fact in facts for ref in fact["evidence_refs"]]
                    # Structured outcomes replace the substring search plus the
                    # two hard-coded ``0.0`` placeholders (report M-3).
                    outcomes = [_fact_outcome(fact) for fact in facts]
                    increased = sum(1 for outcome in outcomes if outcome["increased"])
                    decreased = sum(1 for outcome in outcomes if outcome["decreased"])
                    invalid = sum(1 for outcome in outcomes if outcome["invalid"])
                    measured_facts = sum(
                        1 for outcome in outcomes if outcome["increased"] or outcome["decreased"] or outcome["invalid"]
                    )
                    mechanism_facts = [fact for fact in facts if _text(_as_mapping(fact.get("payload")).get("record_type")) == "mechanism_publish_candidate"]
                    if mechanism_facts:
                        qualified = any(
                            _text(_as_mapping(fact["payload"]).get("requested_status")) == "qualified"
                            and _text(_as_mapping(fact["payload"]).get("validation_status")) == "validated"
                            and (
                                _as_mapping(_as_mapping(fact["payload"]).get("manual_review")).get("approved") is True
                                or _text(_as_mapping(_as_mapping(fact["payload"]).get("manual_review")).get("status")).lower() in {"approved", "accepted", "passed"}
                            )
                            for fact in mechanism_facts
                        )
                        status = "qualified" if qualified and not increased else "proposed"
                    else:
                        status = "shadow" if len(facts) >= 2 and not increased else ("needs_human_review" if increased else "proposed")
                    supporting_experiments = len({fact["source_experiment"] for fact in facts})
                    supporting_samples = len(
                        {fact["sample_id"] for fact in facts if fact["sample_id"]}
                        | {str(ref.get("root_sample_id")) for fact in mechanism_facts for ref in fact["evidence_refs"] if _text(ref.get("root_sample_id"))}
                    )
                    body = {
                        "card_type": card_type, "scene_family": scene, "question_form": form, "reasoning_mechanism": mechanism,
                        "overscore_pattern": hints.get("overscore_pattern", ""),
                        "recommended_operators": [operator] if card_type == "positive_strategy" and operator else [],
                        "backup_operators": [], "avoid_operators": [operator] if card_type == "negative_strategy" and operator else [],
                        "applicability_conditions": applicability, "exclusion_conditions": exclusions,
                        "evidence_summary": {
                            "supporting_experiments": supporting_experiments,
                            "supporting_samples": supporting_samples,
                            # ``None`` means "not measured" -- an explicit unknown
                            # instead of a fabricated 0.0 (report M-3).
                            "score_increased_rate": _rate(increased, len(facts), measured=measured_facts > 0),
                            "effective_rate": _rate(decreased, len(facts), measured=measured_facts > 0),
                            "invalid_generation_rate": _rate(invalid, len(facts), measured=measured_facts > 0),
                            "measured_facts": measured_facts,
                            "fact_count": len(facts),
                        },
                        "claim_level": _claim_level(status=status, fact_count=len(facts), supporting_samples=supporting_samples),
                        "risk_labels": _risk_labels(
                            increased=bool(increased), invalid=bool(invalid), fact_count=len(facts),
                            supporting_experiments=supporting_experiments, has_mechanism=bool(mechanism_facts),
                        ),
                        "taxonomy_version": TAXONOMY_VERSION,
                        "mechanism_id": _text(_as_mapping(mechanism_facts[0]["payload"]).get("mechanism_id")) if mechanism_facts else "",
                    }
                    try:
                        validate_stage4_card({**body, "status": status, "evidence_refs": refs})
                    except AdmissionRejected:
                        report["rejected_insufficient_evidence"] += 1
                        continue
                    fingerprint = _hash({"type": card_type, "scene": scene, "form": form, "mechanism": mechanism, "operator": operator})
                    active_fingerprints.add(fingerprint)
                    existing = con.execute("SELECT * FROM cards WHERE fingerprint = ?", (fingerprint,)).fetchone()
                    now = _now()
                    if existing:
                        old = existing["status"]
                        new_status = "downgraded" if old == "shadow" and status == "needs_human_review" else status
                        version = int(existing["version"]) + 1
                        con.execute("UPDATE cards SET status=?,version=?,body=?,evidence_refs=?,updated_at=? WHERE card_id=?", (new_status, version, _json(body), _json(refs), now, existing["card_id"]))
                        con.execute("INSERT INTO card_events(card_id,previous_status,new_status,reason,evidence_refs,created_at) VALUES(?,?,?,?,?,?)", (existing["card_id"], old, new_status, "re-evaluated from current evidence", _json(refs), now))
                        report["retained" if old == new_status else new_status] += 1
                    else:
                        card_id = self._next_card_id(con)
                        con.execute("INSERT INTO cards(card_id,card_type,status,version,fingerprint,body,evidence_refs,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (card_id, card_type, status, 1, fingerprint, _json(body), _json(refs), now, now))
                        con.execute("INSERT INTO card_events(card_id,previous_status,new_status,reason,evidence_refs,created_at) VALUES(?,?,?,?,?,?)", (card_id, None, status, "added from admissible candidate facts", _json(refs), now))
                        report["added"] += 1
                for old in con.execute("SELECT * FROM cards WHERE status != 'retired'").fetchall():
                    if old["fingerprint"] not in active_fingerprints:
                        con.execute("UPDATE cards SET status='retired',version=version+1,updated_at=? WHERE card_id=?", (_now(), old["card_id"]))
                        con.execute("INSERT INTO card_events(card_id,previous_status,new_status,reason,evidence_refs,created_at) VALUES(?,?,?,?,?,?)", (old["card_id"], old["status"], "retired", "no admissible supporting evidence remains", old["evidence_refs"], _now()))
                        report["retired"] += 1
            self.finish_lease(job_type="phase2_integrate", source_exp_dir="", success=True)
        except BaseException:
            self.finish_lease(job_type="phase2_integrate", source_exp_dir="", success=False)
            raise
        self.publish_projections(report=dict(report))
        return dict(report)

    def _cards(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute("SELECT * FROM cards ORDER BY card_id").fetchall()
        return [_card_from_row(row) for row in rows]

    def _cards_by_ids(self, card_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Fetch only the requested cards, straight from SQL (report M-5).

        ``retrieve`` previously called ``_cards()``, i.e. a full-table load plus
        a ``json.loads`` per row on *every* query.  Retrieval now pushes its
        id filter into the query so its cost tracks the frozen card count in the
        snapshot rather than the total number of cards ever produced.
        """

        ids = [str(card_id) for card_id in card_ids]
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        with self._connect() as con:
            rows = con.execute(
                f"SELECT * FROM cards WHERE card_id IN ({placeholders}) AND status != 'retired' ORDER BY card_id",
                ids,
            ).fetchall()
        return [_card_from_row(row) for row in rows]

    def _atomic_write_if_changed(self, path: Path, content: str) -> bool:
        """Rewrite a projection only when its content actually changed.

        ``publish_projections`` used to rewrite every projection in full on each
        call, so a no-op compile still paid O(N) I/O (report M-5).  Data
        projections are now content-guarded; the small Markdown reports below
        keep their generation timestamp and are always written.
        """

        try:
            if path.read_text(encoding="utf-8") == content:
                return False
        except OSError:
            pass
        _atomic_write(path, content)
        return True

    def publish_projections(self, *, report: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._require_writable()
        self.initialize()
        cards = self._cards()
        runtime = [card for card in cards if card["status"] != "retired"]
        rewritten: list[str] = []
        projections = {
            "global_memory_cards.jsonl": "".join(_json(card) + "\n" for card in cards),
            "global_memory_index.json": json.dumps(
                {
                    "taxonomy_version": TAXONOMY_VERSION,
                    "retrieval_config_version": RETRIEVAL_CONFIG_VERSION,
                    "cards": [
                        {
                            "card_id": card["card_id"], "status": card["status"], "version": card["version"],
                            "card_type": card["card_type"], "scene_family": card.get("scene_family", ""),
                            "question_form": card.get("question_form", ""), "reasoning_mechanism": card.get("reasoning_mechanism", ""),
                            "evidence_refs": card["evidence_refs"],
                        }
                        for card in runtime
                    ],
                },
                ensure_ascii=False, indent=2, sort_keys=True,
            ) + "\n",
        }
        with self._connect() as con:
            admissions = [dict(row) for row in con.execute("SELECT source_ref,decision,reason,candidate_id,created_at FROM admission_log ORDER BY id").fetchall()]
            watermarks = [dict(row) for row in con.execute("SELECT * FROM watermarks ORDER BY source_file").fetchall()]
        projections["global_memory_admission_log.jsonl"] = "".join(_json(entry) + "\n" for entry in admissions)
        projections["global_memory_watermarks.jsonl"] = "".join(_json(entry) + "\n" for entry in watermarks)
        for name, content in projections.items():
            if self._atomic_write_if_changed(self.root / name, content):
                rewritten.append(name)
        lines = ["# Global Memory Publish Report", "", f"Generated: {_now()}", "", "## Card states", ""]
        for status, count in sorted(Counter(card["status"] for card in cards).items()):
            lines.append(f"- {status}: {count}")
        if report:
            lines.extend(["", "## Compilation result", ""] + [f"- {name}: {value}" for name, value in sorted(report.items())])
        _atomic_write(self.root / "global_memory_publish_report.md", "\n".join(lines) + "\n")
        self.write_health_report(cards)
        return {"rewritten": rewritten, "unchanged": sorted(set(projections) - set(rewritten)), "card_count": len(cards)}

    def write_health_report(self, cards: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        cards = list(cards or self._cards())
        counts = Counter(str(card["status"]) for card in cards)
        no_evidence = sum(1 for card in cards if not card.get("evidence_refs"))
        conflicts = 0
        groups: dict[tuple[str, str], set[str]] = {}
        for card in cards:
            groups.setdefault((str(card.get("scene_family", "")), str(card.get("question_form", ""))), set()).add(str(card["card_type"]))
        conflicts = sum(1 for kinds in groups.values() if "positive_strategy" in kinds and "negative_strategy" in kinds)
        stale = sum(1 for card in cards if (datetime.now(timezone.utc) - datetime.fromisoformat(str(card.get("updated_at", _now())))).days > 30)
        # Aggregate the per-card measured rates instead of publishing a
        # hard-coded 0.0 placeholder (report M-3).
        measured = [
            card for card in cards
            if _as_mapping(card.get("evidence_summary")).get("score_increased_rate") is not None
        ]
        memory_hit_score_increased_rate = (
            round(sum(float(_as_mapping(card["evidence_summary"])["score_increased_rate"]) for card in measured) / len(measured), 6)
            if measured else None
        )
        health = {"total_cards": len(cards), "status_counts": dict(counts), "stale_unverified_cards": stale, "cards_without_evidence": no_evidence, "conflicting_card_groups": conflicts, "read_without_benefit": 0, "memory_hit_score_increased_rate": memory_hit_score_increased_rate, "measured_cards": len(measured), "judge_instability_downgraded": 0}
        lines = ["# Global Memory Health Report", ""] + [f"- {key}: {value}" for key, value in health.items()]
        _atomic_write(self.root / "global_memory_health_report.md", "\n".join(lines) + "\n")
        return health

    def rebuild_projections(self) -> dict[str, Any]:
        return self.publish_projections()

    def create_snapshot(self, *, local_memory_dir: str | Path | None = None) -> dict[str, Any]:
        self._require_writable()
        self.initialize()
        local = Path(local_memory_dir).resolve() if local_memory_dir else self.project_root / "memory"
        local_hashes = {path.name: _file_hash(path) for path in sorted(local.glob("*.jsonl"))} if local.is_dir() else {}
        index_path = self.root / "global_memory_index.json"
        if not index_path.exists():
            self.publish_projections()
        index_hash = _file_hash(index_path)
        cards = self._cards()
        versions: dict[str, Any] = {}
        fingerprints: dict[str, Any] = {}
        for card in cards:
            if card.get("status") == "retired":
                continue
            versions[card["card_id"]] = card["version"]
            # Content addressing: the frozen identity is the card body itself,
            # not only its id.  ``retrieve`` refuses to serve a card whose body
            # or version changed after the snapshot was taken.
            fingerprints[card["card_id"]] = {"version": int(card.get("version") or 0), "body_sha256": _card_fingerprint(card)}
        payload = {"local_memory_hashes": local_hashes, "global_index_hash": index_hash, "taxonomy_version": TAXONOMY_VERSION, "card_versions": versions, "card_fingerprints": fingerprints}
        snapshot_id = _hash(payload).split(":", 1)[1]
        snapshot = {"memory_snapshot_id": "MSNAP-" + snapshot_id[:20], **payload, "created_at": _now(), "mode": "no_global_memory" if not versions else "global_memory"}
        snapshot_path = self.root / "snapshots" / (snapshot["memory_snapshot_id"] + ".json")
        # M-9: the id is a content hash, so an identical identity must reuse the
        # existing file instead of rewriting it with a new timestamp -- which
        # also kept resetting the ``created_at`` reference clock that retrieval
        # freshness is measured against.
        if snapshot_path.is_file():
            existing = _as_mapping(json.loads(snapshot_path.read_text(encoding="utf-8")))
            if existing.get("card_fingerprints") == fingerprints and existing.get("card_versions") == versions:
                return {**snapshot, **existing, "reused": True}
        _atomic_write(snapshot_path, json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return {**snapshot, "reused": False}

    def prune_snapshots(self, *, keep: int = 20, protect: Sequence[str] = ()) -> dict[str, Any]:
        """Delete the oldest snapshots beyond ``keep`` (report M-9).

        ``create_snapshot`` runs on every non-resumed Session, so the snapshot
        directory grew without bound.  ``protect`` lists snapshot ids that must
        survive (normally the one the current Session manifest references).
        """

        self._require_writable()
        self.initialize()
        if keep < 1:
            raise GlobalMemoryError("keep must be a positive integer")
        directory = self.root / "snapshots"
        protected = {str(item) for item in protect if item}
        entries: list[tuple[str, str]] = []
        if directory.is_dir():
            for path in directory.glob("MSNAP-*.json"):
                try:
                    value = _as_mapping(json.loads(path.read_text(encoding="utf-8")))
                except (OSError, json.JSONDecodeError):
                    continue
                entries.append((str(value.get("created_at") or ""), path.name))
        entries.sort()
        removable = [(created, name) for created, name in entries if name[:-5] not in protected]
        removed: list[str] = []
        for _created, name in removable[: max(0, len(entries) - int(keep))]:
            try:
                (directory / name).unlink()
                removed.append(name)
            except OSError:
                continue
        return {"kept": len(entries) - len(removed), "removed": removed, "protected": sorted(protected)}

    def load_snapshot(self, snapshot_id: str, *, allow_no_global_memory: bool = False) -> dict[str, Any]:
        path = self.root / "snapshots" / f"{snapshot_id}.json"
        if not path.is_file():
            if allow_no_global_memory:
                return {"memory_snapshot_id": snapshot_id, "mode": "no_global_memory", "degraded": True}
            raise SnapshotUnavailable(f"memory snapshot is unavailable: {snapshot_id}")
        return _as_mapping(json.loads(path.read_text(encoding="utf-8")))

    def verify_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        """Report whether every frozen card still matches its fingerprint."""

        snapshot = self.load_snapshot(snapshot_id)
        allowed = _as_mapping(snapshot.get("card_versions"))
        fingerprints = _as_mapping(snapshot.get("card_fingerprints"))
        mismatches: list[dict[str, Any]] = []
        for card in self._cards():
            card_id = str(card.get("card_id"))
            if card_id not in allowed:
                continue
            if card.get("status") == "retired":
                mismatches.append({"card_id": card_id, "reason": "card_retired_after_freeze"})
            elif not _matches_frozen_card(card, fingerprints, allowed):
                mismatches.append({"card_id": card_id, "reason": "card_body_or_version_changed_after_freeze"})
        return {
            "memory_snapshot_id": snapshot_id,
            "frozen_card_count": len(allowed),
            "mismatches": mismatches,
            "verified": not mismatches,
        }

    def _retrieval_clock(self, snapshot: Mapping[str, Any]) -> datetime:
        """Reference time for freshness decay.

        Freshness is measured against the snapshot's own creation time so the
        score of a frozen snapshot is reproducible: two retrievals of the same
        snapshot must return identical rankings (design §13.6).
        """

        recorded = _text(snapshot.get("created_at"))
        if recorded:
            try:
                return datetime.fromisoformat(recorded)
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _evidence_strength(card: Mapping[str, Any]) -> float:
        summary = _as_mapping(card.get("evidence_summary"))
        samples = summary.get("supporting_samples")
        samples = samples if isinstance(samples, int) and not isinstance(samples, bool) else 0
        return min(1.0, samples / 3.0)

    def _conflicting_groups(self, cards: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
        groups: dict[tuple[str, str], set[str]] = {}
        for card in cards:
            key = (str(card.get("scene_family", "")), str(card.get("question_form", "")))
            groups.setdefault(key, set()).add(str(card.get("card_type")))
        return {
            key for key, kinds in groups.items()
            if "positive_strategy" in kinds and "negative_strategy" in kinds
        }

    def _score_card(self, card: Mapping[str, Any], tokens: set[str], *, now: datetime, conflicting: set[tuple[str, str]]) -> tuple[float, list[str]]:
        """Weighted score following the design's field precedence (report M-2).

        Field weights, freshness decay, status/conflict penalties and a hard
        exclusion filter replace the previous token-substring counter.  Returns
        ``(score, reasons)`` so the ranking is auditable.
        """

        reasons: list[str] = []
        components: dict[str, float] = {}
        for field, weight in RETRIEVAL_FIELD_WEIGHTS:
            if field == "evidence_strength":
                value = self._evidence_strength(card)
            elif field == "exclusion_conditions":
                value = _overlap_ratio(tokens, " ".join(str(item) for item in card.get("exclusion_conditions") or []))
            else:
                value = _overlap_ratio(tokens, card.get(field, ""))
            if value:
                components[field] = round(weight * value, 6)
        age_days = max(0.0, (now - _parse_timestamp(card.get("updated_at") or card.get("created_at"))).total_seconds() / 86400.0)
        freshness = 0.5 ** (age_days / RETRIEVAL_FRESHNESS_HALFLIFE_DAYS)
        components["freshness"] = round(RETRIEVAL_FRESHNESS_WEIGHT * freshness, 6)
        score = sum(components.values())
        penalty = RETRIEVAL_STATUS_PENALTIES.get(str(card.get("status")), 0.0)
        risk_labels = [str(label) for label in card.get("risk_labels") or []]
        penalty += min(len(risk_labels), 3) * 0.5
        if (str(card.get("scene_family", "")), str(card.get("question_form", ""))) in conflicting:
            penalty += RETRIEVAL_CONFLICT_PENALTY
            reasons.append("conflicting_positive_and_negative_strategies_in_group")
        if penalty:
            reasons.append(f"penalty={round(penalty, 6)}")
        score = round(score - penalty, 6)
        reasons.extend(f"{field}={components[field]}" for field in components if field in dict(RETRIEVAL_FIELD_WEIGHTS))
        if risk_labels:
            reasons.append("risk_labels=" + ",".join(risk_labels))
        return score, reasons

    def retrieve(self, *, snapshot_id: str, query: str, top_k: int = 3, strict_frozen: bool = True) -> dict[str, Any]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        snapshot = self.load_snapshot(snapshot_id)
        allowed = _as_mapping(snapshot.get("card_versions"))
        fingerprints = _as_mapping(snapshot.get("card_fingerprints"))
        permitted = set(allowed.keys())
        tokens = _tokenize(query)
        # SQL-scoped fetch: only the frozen cards are loaded (report M-5).
        candidates = self._cards_by_ids(sorted(permitted))
        conflict_scope = self._conflicting_groups(candidates)
        now = self._retrieval_clock(snapshot)
        scored: list[tuple[float, str, dict[str, Any], list[str]]] = []
        stale: list[str] = []
        excluded: list[dict[str, Any]] = []
        for card in candidates:
            card_id = str(card.get("card_id"))
            if not _matches_frozen_card(card, fingerprints, allowed):
                stale.append(card_id)
                continue
            exclusion_tokens = set()
            for item in card.get("exclusion_conditions") or []:
                exclusion_tokens |= _tokenize(item)
            if RETRIEVAL_ENFORCE_EXCLUSIONS and tokens and tokens & exclusion_tokens:
                excluded.append({"card_id": card_id, "reason": "exclusion_condition_matched"})
                continue
            score, reasons = self._score_card(card, tokens, now=now, conflicting=conflict_scope)
            scored.append((score, card_id, card, reasons))
        if stale and strict_frozen:
            raise SnapshotUnavailable(
                "frozen memory snapshot no longer matches its cards: " + ", ".join(sorted(stale))
            )
        ordered = sorted(scored, key=lambda item: (-item[0], item[1]))[:top_k]
        # Bounded deterministic context: ``top_k`` caps the injected cards, and
        # the character budget caps the injected payload (design §9.4).
        summaries: list[dict[str, Any]] = []
        remaining = RETRIEVAL_CONTEXT_CHAR_BUDGET
        dropped: list[str] = []
        for score, card_id, card, reasons in ordered:
            summary = {
                "card_id": card["card_id"], "version": card["version"], "status": card["status"],
                "retrieval_score": score, "retrieval_reasons": reasons,
                "claim_level": card.get("claim_level", "single_observation"),
                "risk_labels": list(card.get("risk_labels") or []),
                "summary": f"{card['card_type']}: {card.get('reasoning_mechanism') or card.get('question_form') or 'strategy evidence'}",
                "evidence_summary": _as_mapping(card.get("evidence_summary")),
                "applicability": card.get("applicability_conditions", []),
                "exclusions": card.get("exclusion_conditions", []),
                "evidence_refs": card["evidence_refs"],
                "action_limit": "Audit-only reference; it must not alter the operator plan, routing, execution order, or scoring.",
            }
            size = len(_json(summary))
            if size > remaining:
                dropped.append(card_id)
                continue
            remaining -= size
            summaries.append(summary)
        context_key = memory_context_key(memory_snapshot_id=snapshot_id, normalized_query=query, retrieval_config_version=RETRIEVAL_CONFIG_VERSION, top_k=top_k)
        return {
            "memory_snapshot_id": snapshot_id, "memory_context_key": context_key,
            "retrieval_config_version": RETRIEVAL_CONFIG_VERSION, "top_k": top_k, "cards": summaries,
            "mode": snapshot.get("mode", "no_global_memory"),
            "snapshot_integrity": {"status": "verified" if not stale else "mismatch", "frozen_card_count": len(permitted), "mismatched_card_ids": stale},
            "retrieval_diagnostics": {
                "token_count": len(tokens), "candidate_count": len(candidates),
                "excluded": excluded, "dropped_for_budget": dropped,
                "context_char_budget": RETRIEVAL_CONTEXT_CHAR_BUDGET,
                "context_chars_remaining": remaining,
            },
        }

    def import_trace(self, experiment_dir: str | Path) -> dict[str, Any]:
        self._require_writable()
        experiment = Path(experiment_dir).resolve()
        manifests = list(experiment.rglob("*.manifest.json"))
        if not manifests:
            raise GlobalMemoryError("historical import rejected: no published artifact manifest")
        invalid = [str(path) for path in manifests if not _as_mapping(json.loads(path.read_text(encoding="utf-8"))).get("schema_version")]
        if invalid:
            raise GlobalMemoryError("historical import rejected: incompatible manifest schema")
        result = self.extract(experiment)
        self.integrate()
        return result


def router_cache_key(*, base_key: str, memory_snapshot_id: str) -> str:
    return _hash({"base_key": base_key, "memory_snapshot_id": memory_snapshot_id})


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage-4 global memory compiler")
    parser.add_argument("--project-root", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("extract", "import-trace"):
        child = sub.add_parser(command)
        child.add_argument("--exp-dir", required=True)
    sub.add_parser("integrate")
    sub.add_parser("rebuild-projections")
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--local-memory-dir", default=None)
    prune = sub.add_parser("prune-snapshots")
    prune.add_argument("--keep", type=int, default=20)
    prune.add_argument("--protect", action="append", default=[], help="snapshot id that must survive")
    # Watermark governance: without these two commands a ``source_rewritten``
    # review could never be closed and the audit log grew without bound (M-4).
    bless = sub.add_parser("bless-source")
    bless.add_argument("--source-file", required=True)
    bless.add_argument("--reason", default="human_confirmed_prefix_rewrite")
    reset = sub.add_parser("reset-watermark")
    reset.add_argument("--source-file", required=True)
    health = sub.add_parser("health")
    health.add_argument("--read-only", action="store_true", help="inspect without creating or touching the store")
    args = parser.parse_args(argv)
    store = GlobalMemoryStore(args.project_root, initialize=args.command != "health", read_only=getattr(args, "read_only", False))
    if args.command == "extract":
        result = store.extract(args.exp_dir)
    elif args.command == "import-trace":
        result = store.import_trace(args.exp_dir)
    elif args.command == "integrate":
        result = store.integrate()
    elif args.command == "rebuild-projections":
        result = store.rebuild_projections()
    elif args.command == "bless-source":
        result = store.bless_source(args.source_file, reason=args.reason)
    elif args.command == "reset-watermark":
        result = store.reset_watermark(args.source_file)
    elif args.command == "prune-snapshots":
        result = store.prune_snapshots(keep=int(args.keep), protect=tuple(args.protect or ()))
    elif args.command == "health":
        result = store.write_health_report()
    else:
        result = store.create_snapshot(local_memory_dir=args.local_memory_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
