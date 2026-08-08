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
import sqlite3
import tempfile
import uuid
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


TAXONOMY_VERSION = "global-memory-taxonomy-v1"
RETRIEVAL_CONFIG_VERSION = "global-memory-retrieval-v1"
CARD_TYPES = {"positive_strategy", "negative_strategy", "risk_pattern", "system_diagnosis", "optimization_signal"}
STAGE4_STATUSES = {"proposed", "shadow", "qualified", "needs_human_review", "rejected_insufficient_evidence", "downgraded", "retired"}
LOCAL_SOURCES = {
    "operator_memory_bank.jsonl": "positive_strategy",
    "failure_memory_bank.jsonl": "negative_strategy",
    "invalid_generation_cases.jsonl": "risk_pattern",
    "operator_performance.jsonl": "optimization_signal",
    "agent_observation.json": "system_diagnosis",
    "mechanism_publish_candidates.jsonl": "mechanism_publish_candidate",
}


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

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / "memory_global"
        self.root.mkdir(parents=True, exist_ok=True)
        self.db_path = self.root / "global_memory_state.sqlite"
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
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
                """
            )

    def _log(self, con: sqlite3.Connection, source_ref: str, decision: str, reason: str, candidate_id: str | None = None) -> None:
        con.execute(
            "INSERT INTO admission_log(source_ref, decision, reason, candidate_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (source_ref, decision, reason, candidate_id, _now()),
        )

    def acquire_lease(self, *, job_type: str, source_exp_dir: str, owner: str | None = None, seconds: int = 300) -> str:
        """Lease Phase 1 per experiment, or the single global Phase 2 compiler."""

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
        source = str(Path(source_exp_dir).resolve()) if source_exp_dir else "__global__"
        with self._connect() as con:
            con.execute("UPDATE jobs SET status = ?, updated_at = ? WHERE job_key = ?", ("completed" if success else "failed", _now(), f"{job_type}:{source}"))

    def _watermark(self, con: sqlite3.Connection, source: Path) -> sqlite3.Row | None:
        return con.execute("SELECT * FROM watermarks WHERE source_file = ?", (str(source.resolve()),)).fetchone()

    @staticmethod
    def _read_jsonl(path: Path, start_line: int) -> list[tuple[int, dict[str, Any]]]:
        records: list[tuple[int, dict[str, Any]]] = []
        with path.open("r", encoding="utf-8") as handle:
            for number, raw in enumerate(handle, 1):
                if number <= start_line or not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise GlobalMemoryError(f"invalid JSONL in {path} line {number}: {exc.msg}") from exc
                if not isinstance(value, Mapping):
                    raise GlobalMemoryError(f"invalid JSONL object in {path} line {number}")
                records.append((number, dict(value)))
        return records

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
        if not experiment.is_dir():
            raise GlobalMemoryError(f"experiment directory does not exist: {experiment}")
        job = self.acquire_lease(job_type="phase1_extract", source_exp_dir=str(experiment))
        included = excluded = rewritten = 0
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
                        records = self._read_jsonl(source, previous_lines)
                    else:
                        try:
                            raw = json.loads(source.read_text(encoding="utf-8"))
                        except json.JSONDecodeError as exc:
                            raise GlobalMemoryError(f"invalid JSON in {source}: {exc.msg}") from exc
                        records = [] if previous_lines else [(1, _as_mapping(raw))]
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
        return {"job_id": job, "included": included, "excluded": excluded, "source_rewritten": rewritten}

    def _next_card_id(self, con: sqlite3.Connection) -> str:
        row = con.execute("SELECT card_id FROM cards ORDER BY card_id DESC LIMIT 1").fetchone()
        number = int(row["card_id"].split("-")[-1]) + 1 if row else 1
        return f"GMEM-{number:06d}"

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
                    increased = sum(1 for fact in facts if "score_increased" in fact["conclusion"].lower() or "score_increased" in str(fact["payload"]))
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
                    body = {
                        "card_type": card_type, "scene_family": scene, "question_form": form, "reasoning_mechanism": mechanism,
                        "overscore_pattern": hints.get("overscore_pattern", ""),
                        "recommended_operators": [operator] if card_type == "positive_strategy" and operator else [],
                        "backup_operators": [], "avoid_operators": [operator] if card_type == "negative_strategy" and operator else [],
                        "applicability_conditions": applicability, "exclusion_conditions": exclusions,
                        "evidence_summary": {
                            "supporting_experiments": len({fact["source_experiment"] for fact in facts}),
                            "supporting_samples": len({fact["sample_id"] for fact in facts if fact["sample_id"]} | {str(ref.get("root_sample_id")) for fact in mechanism_facts for ref in fact["evidence_refs"] if _text(ref.get("root_sample_id"))}),
                            "score_increased_rate": increased / len(facts), "effective_rate": 0.0, "invalid_generation_rate": 0.0,
                        },
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
        return [{"card_id": row["card_id"], "status": row["status"], "version": row["version"], "created_at": row["created_at"], "updated_at": row["updated_at"], **json.loads(row["body"]), "evidence_refs": json.loads(row["evidence_refs"])} for row in rows]

    def publish_projections(self, *, report: Mapping[str, Any] | None = None) -> None:
        cards = self._cards()
        runtime = [card for card in cards if card["status"] != "retired"]
        _atomic_write(self.root / "global_memory_cards.jsonl", "".join(_json(card) + "\n" for card in cards))
        index = {"taxonomy_version": TAXONOMY_VERSION, "retrieval_config_version": RETRIEVAL_CONFIG_VERSION, "cards": [{"card_id": card["card_id"], "status": card["status"], "version": card["version"], "card_type": card["card_type"], "scene_family": card.get("scene_family", ""), "question_form": card.get("question_form", ""), "reasoning_mechanism": card.get("reasoning_mechanism", ""), "evidence_refs": card["evidence_refs"]} for card in runtime]}
        _atomic_write(self.root / "global_memory_index.json", json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        with self._connect() as con:
            admissions = [dict(row) for row in con.execute("SELECT source_ref,decision,reason,candidate_id,created_at FROM admission_log ORDER BY id").fetchall()]
            watermarks = [dict(row) for row in con.execute("SELECT * FROM watermarks ORDER BY source_file").fetchall()]
        _atomic_write(self.root / "global_memory_admission_log.jsonl", "".join(_json(entry) + "\n" for entry in admissions))
        _atomic_write(self.root / "global_memory_watermarks.jsonl", "".join(_json(entry) + "\n" for entry in watermarks))
        lines = ["# Global Memory Publish Report", "", f"Generated: {_now()}", "", "## Card states", ""]
        for status, count in sorted(Counter(card["status"] for card in cards).items()):
            lines.append(f"- {status}: {count}")
        if report:
            lines.extend(["", "## Compilation result", ""] + [f"- {name}: {value}" for name, value in sorted(report.items())])
        _atomic_write(self.root / "global_memory_publish_report.md", "\n".join(lines) + "\n")
        self.write_health_report(cards)

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
        health = {"total_cards": len(cards), "status_counts": dict(counts), "stale_unverified_cards": stale, "cards_without_evidence": no_evidence, "conflicting_card_groups": conflicts, "read_without_benefit": 0, "memory_hit_score_increased_rate": 0.0, "judge_instability_downgraded": 0}
        lines = ["# Global Memory Health Report", ""] + [f"- {key}: {value}" for key, value in health.items()]
        _atomic_write(self.root / "global_memory_health_report.md", "\n".join(lines) + "\n")
        return health

    def rebuild_projections(self) -> None:
        self.publish_projections()

    def create_snapshot(self, *, local_memory_dir: str | Path | None = None) -> dict[str, Any]:
        local = Path(local_memory_dir).resolve() if local_memory_dir else self.project_root / "memory"
        local_hashes = {path.name: _file_hash(path) for path in sorted(local.glob("*.jsonl"))} if local.is_dir() else {}
        index_path = self.root / "global_memory_index.json"
        if not index_path.exists():
            self.publish_projections()
        index_hash = _file_hash(index_path)
        cards = self._cards()
        versions = {card["card_id"]: card["version"] for card in cards if card["status"] != "retired"}
        payload = {"local_memory_hashes": local_hashes, "global_index_hash": index_hash, "taxonomy_version": TAXONOMY_VERSION, "card_versions": versions}
        snapshot_id = _hash(payload).split(":", 1)[1]
        snapshot = {"memory_snapshot_id": "MSNAP-" + snapshot_id[:20], **payload, "created_at": _now(), "mode": "no_global_memory" if not versions else "global_memory"}
        _atomic_write(self.root / "snapshots" / (snapshot["memory_snapshot_id"] + ".json"), json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return snapshot

    def load_snapshot(self, snapshot_id: str, *, allow_no_global_memory: bool = False) -> dict[str, Any]:
        path = self.root / "snapshots" / f"{snapshot_id}.json"
        if not path.is_file():
            if allow_no_global_memory:
                return {"memory_snapshot_id": snapshot_id, "mode": "no_global_memory", "degraded": True}
            raise SnapshotUnavailable(f"memory snapshot is unavailable: {snapshot_id}")
        return _as_mapping(json.loads(path.read_text(encoding="utf-8")))

    def retrieve(self, *, snapshot_id: str, query: str, top_k: int = 3) -> dict[str, Any]:
        if top_k < 1:
            raise ValueError("top_k must be positive")
        snapshot = self.load_snapshot(snapshot_id)
        permitted = set(_as_mapping(snapshot.get("card_versions")).keys())
        tokens = {token for token in query.lower().split() if token}
        scored: list[tuple[int, str, dict[str, Any]]] = []
        for card in self._cards():
            if card["card_id"] not in permitted or card["status"] == "retired":
                continue
            searchable = " ".join(str(card.get(field, "")) for field in ("scene_family", "question_form", "reasoning_mechanism", "overscore_pattern", "card_type")).lower()
            score = sum(token in searchable for token in tokens)
            scored.append((-score, card["card_id"], card))
        selected = [card for _, _, card in sorted(scored)[:top_k]]
        summaries = [{"card_id": card["card_id"], "version": card["version"], "status": card["status"], "summary": f"{card['card_type']}: {card.get('reasoning_mechanism') or card.get('question_form') or 'strategy evidence'}", "applicability": card.get("applicability_conditions", []), "exclusions": card.get("exclusion_conditions", []), "evidence_refs": card["evidence_refs"], "action_limit": "Audit-only reference; it must not alter the operator plan, routing, execution order, or scoring."} for card in selected]
        normalized_query = " ".join(query.lower().split())
        context_key = _hash({"memory_snapshot_id": snapshot_id, "normalized_query": normalized_query, "retrieval_config_version": RETRIEVAL_CONFIG_VERSION, "top_k": top_k})
        return {"memory_snapshot_id": snapshot_id, "memory_context_key": context_key, "retrieval_config_version": RETRIEVAL_CONFIG_VERSION, "top_k": top_k, "cards": summaries}

    def import_trace(self, experiment_dir: str | Path) -> dict[str, Any]:
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
    args = parser.parse_args(argv)
    store = GlobalMemoryStore(args.project_root)
    if args.command == "extract":
        result = store.extract(args.exp_dir)
    elif args.command == "import-trace":
        result = store.import_trace(args.exp_dir)
    elif args.command == "integrate":
        result = store.integrate()
    elif args.command == "rebuild-projections":
        store.rebuild_projections()
        result = {"rebuilt": True}
    else:
        result = store.create_snapshot(local_memory_dir=args.local_memory_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
