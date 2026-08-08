"""Offline-only Global Judge governance for the question-evolution pipeline.

This module deliberately works from compact, traceable evidence.  It never
opens a model session, changes pipeline artifacts, mutates router input, or
writes prompt/operator/rubric/state files.  Publishing is a separate,
approval-gated ledger action and its strategy snapshots remain advisory until
another explicitly governed integration consumes them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


GLOBAL_JUDGE_VERSION = "global-judge-v1"
EVIDENCE_PACK_VERSION = "global-judge-evidence-pack-v1"
PROPOSAL_VERSION = "global-judge-proposal-v1"
DIAGNOSIS_VERSION = "global-judge-diagnosis-v1"
REPLAY_VERSION = "global-judge-replay-v1"
DIAGNOSIS_LEVELS = {
    "sample/data", "router", "operator selection", "operator generation",
    "validation", "rubric/judge", "memory", "search/cost",
}
DIAGNOSIS_KINDS = {
    "business_failure", "system_failure", "judge_instability",
    "strategy_conflict", "evidence_insufficient",
}
PROPOSAL_STATUSES = {
    "proposed", "needs_human_review", "shadow", "rejected_insufficient_evidence", "active",
}
FORMAL_MUTATION_NAMES = {
    "prompt", "router", "rubric", "memory", "score", "state", "operator",
}
SAFE_RECORD_FIELDS = {
    "sample_id", "index", "round", "node_id", "branch_id", "candidate_id",
    "operator_id", "operator_used", "candidate_operator", "branch_status",
    "question_evolution_status", "score_rate", "candidate_group_id",
}
SAFE_CONFIGURATION_FIELDS = {
    "search_mode", "termination_reason", "duration_seconds", "elapsed_seconds",
    "total_cost", "cost", "request_count", "evaluation_count", "model_calls",
    "started_at", "completed_at", "memory_snapshot_id",
}
FORMAL_ARTIFACT_BASENAMES = {
    "final_scored.jsonl", "branch_results.jsonl", "effect_analysis.jsonl", "state_updated.jsonl",
    "search_state_updated.jsonl", "operator_routed.jsonl", "operator_routing.jsonl",
    "difficulty_validated_candidates.jsonl", "validated_candidates.jsonl", "scored.jsonl",
}


class GlobalJudgeError(RuntimeError):
    """Base error for read-only governance operations."""


class EvidencePackRejected(GlobalJudgeError):
    pass


class ProposalRejected(GlobalJudgeError):
    pass


class PolicyGuardRejected(GlobalJudgeError):
    pass


class PublicationRejected(GlobalJudgeError):
    pass


def policy_guard(*, action: str, actor_role: str = "global_judge") -> None:
    """Enforce the Phase-5 authority split before any governed action.

    The Global Judge may only produce reports, proposals, and Shadow records.
    Formal pipeline mutations are categorically outside its authority; an
    independently authorised publisher is the sole path to an ``active``
    ledger entry.
    """

    normalized = action.strip().lower().replace("_", " ")
    if any(name in normalized for name in FORMAL_MUTATION_NAMES):
        raise PolicyGuardRejected(f"Global Judge cannot directly modify formal {action}")
    if normalized in {"publish active", "active publish"} and actor_role != "publisher":
        raise PolicyGuardRejected("active publication requires independent publisher authority")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _as_mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GlobalJudgeError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise GlobalJudgeError(f"JSON object required: {path}")
    return dict(value)


def _read_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".json":
        return [_read_json(path)]
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise GlobalJudgeError(f"cannot read {path}: {exc}") from exc
    for number, raw in enumerate(lines, 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise GlobalJudgeError(f"invalid JSONL in {path} line {number}: {exc.msg}") from exc
        if isinstance(value, Mapping):
            records.append(dict(value))
    return records


def _ref(path: Path, record: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"artifact_ref": str(path.resolve())}
    row = record or {}
    for source, target in (("sample_id", "sample_id"), ("index", "sample_id"), ("branch_id", "branch_id"), ("candidate_id", "branch_id"), ("operator_id", "operator_id"), ("operator_used", "operator_id")):
        if target not in result and _text(row.get(source)):
            result[target] = _text(row[source])
    return result


def _safe_effect(record: Mapping[str, Any]) -> dict[str, Any]:
    effect = _as_mapping(record.get("effect_analysis"))
    result: dict[str, Any] = {}
    for key in ("effect_label", "label", "status", "score_increased", "score_increased_after_evolution", "effective_boundary", "effective_boundary_probe", "failure_reason"):
        if key in effect:
            result[key] = effect[key]
        elif key in record and key not in {"failure_reason"}:
            result[key] = record[key]
    return result


def _safe_route(record: Mapping[str, Any]) -> dict[str, Any]:
    route = _as_mapping(record.get("operator_route"))
    selected = route.get("selected_operator") or route.get("operator_id") or record.get("candidate_operator") or record.get("operator_used")
    return {key: value for key, value in {
        "selected_operator": selected,
        "routing_mode": route.get("routing_mode"),
        "assignment_mode": route.get("assignment_mode"),
        "route_status": route.get("status"),
    }.items() if value not in (None, "")}


def _safe_validation(record: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in ("validation_result", "difficulty_gain_validation"):
        value = _as_mapping(record.get(name))
        if value:
            result[name] = {key: value[key] for key in ("passed", "status", "difficulty_gain", "risk_level", "hard_risk", "risk_tags") if key in value}
    return result


def _record_summary(record: Mapping[str, Any], path: Path) -> dict[str, Any]:
    summary = {key: record[key] for key in SAFE_RECORD_FIELDS if key in record and isinstance(record[key], (str, int, float, bool))}
    summary["evidence_ref"] = _ref(path, record)
    summary["artifact_kind"] = path.stem
    summary["effect"] = _safe_effect(record)
    summary["route"] = _safe_route(record)
    summary["validation"] = _safe_validation(record)
    signature = _as_mapping(record.get("sample_signature"))
    metadata = _as_mapping(record.get("meta_info")).get("question_evolution_metadata", {})
    metadata = _as_mapping(metadata)
    summary["scope"] = {
        "scene_family": _text(signature.get("scene_family")) or _text(record.get("scene_family")),
        "question_form": _text(signature.get("question_form")) or _text(record.get("surface_form_family")),
        "reasoning_mechanism": _text(signature.get("reasoning_mechanism")) or _text(metadata.get("expected_qwen_failure")),
    }
    score_trials = record.get("round0_score_trials")
    score_summary = _as_mapping(record.get("round0_score_summary"))
    effect = _as_mapping(record.get("effect_analysis"))
    summary["score_summary"] = {
        "before_score": effect.get("previous_score_rate") or effect.get("before_score") or record.get("previous_score_rate") or record.get("parent_score_rate"),
        "after_score": record.get("score_rate") or effect.get("after_score") or effect.get("score_rate"),
        "judge_repeat_count": len(score_trials) if isinstance(score_trials, list) else int(score_summary.get("trial_count") or 0),
        "judge_consistent": score_summary.get("is_stable") if "is_stable" in score_summary else score_summary.get("stable"),
    }
    return summary


def _label(row: Mapping[str, Any]) -> str:
    effect = _as_mapping(row.get("effect"))
    for value in (effect.get("effect_label"), effect.get("label"), row.get("branch_status"), row.get("question_evolution_status"), effect.get("status")):
        label = _text(value).lower()
        if label:
            return label
    if effect.get("score_increased") or effect.get("score_increased_after_evolution"):
        return "score_increased"
    validation = _as_mapping(row.get("validation"))
    if any(_as_mapping(value).get("passed") is False for value in validation.values()):
        return "validation_failed"
    return "unclassified"


def _snapshot_from_value(snapshot: str | Path | Mapping[str, Any] | None, summaries: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    if isinstance(snapshot, Mapping):
        value = dict(snapshot)
    elif snapshot:
        value = _read_json(Path(snapshot))
    for row in summaries:
        if value:
            break
        candidate = row.get("memory_snapshot_id")
        if _text(candidate):
            value = {"memory_snapshot_id": candidate, "source": "published_record"}
    snapshot_id = _text(value.get("memory_snapshot_id")) or _text(value.get("snapshot_id"))
    if not snapshot_id:
        raise EvidencePackRejected("Evidence Pack requires an immutable memory/session Snapshot")
    return {"memory_snapshot_id": snapshot_id, "snapshot_ref": _text(value.get("snapshot_ref")) or _text(value.get("source")) or "declared_snapshot", "snapshot_hash": _hash(value)}


def _artifact_files(experiment: Path) -> list[Path]:
    names = {
        "branch_results.jsonl", "effect_analysis.jsonl", "state_updated.jsonl", "search_state_updated.jsonl",
        "operator_routed.jsonl", "operator_routing.jsonl", "difficulty_validated_candidates.jsonl",
        "validated_candidates.jsonl", "scored.jsonl", "final_scored.jsonl", "experiment_statistics.json",
    }
    return sorted(path for path in experiment.rglob("*") if path.is_file() and path.name in names)


def _memory_refs(experiment: Path) -> list[dict[str, Any]]:
    names = {"operator_memory_bank.jsonl", "failure_memory_bank.jsonl", "invalid_generation_cases.jsonl", "operator_performance.jsonl"}
    refs: list[dict[str, Any]] = []
    for path in sorted(p for p in experiment.rglob("*") if p.is_file() and p.name in names):
        refs.append({"artifact_ref": str(path.resolve()), "kind": "L1_experiment_fact_memory", "record_count": len(_read_records(path))})
    return refs


def _l2_cards(project_root: Path) -> list[dict[str, Any]]:
    path = project_root / "memory_global" / "global_memory_cards.jsonl"
    if not path.is_file():
        return []
    cards: list[dict[str, Any]] = []
    for card in _read_records(path):
        cards.append({key: card.get(key) for key in ("card_id", "card_type", "status", "version", "scene_family", "question_form", "reasoning_mechanism", "evidence_refs")})
    return cards


def _metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    labels = Counter(_label(row) for row in rows)
    total = len(rows)
    def rate(*names: str) -> float:
        return sum(labels[name] for name in names) / total if total else 0.0
    judge_unstable = sum(
        1 for row in rows
        if ("judge" in _label(row) and ("unstable" in _label(row) or "disagreement" in _label(row)))
        or _as_mapping(row.get("score_summary")).get("judge_consistent") is False
    )
    repeat_count = sum(int(_as_mapping(row.get("score_summary")).get("judge_repeat_count") or 0) for row in rows)
    scored = [
        _as_mapping(row.get("score_summary")) for row in rows
        if _as_mapping(row.get("score_summary")).get("before_score") is not None
        and _as_mapping(row.get("score_summary")).get("after_score") is not None
    ]
    return {
        "records": total, "label_counts": dict(sorted(labels.items())),
        "effective_rate": rate("boundary_candidate", "exploration_candidate", "score_decreased"),
        "score_increased_rate": rate("score_increased"),
        "invalid_generation_rate": rate("invalid_generation", "validation_failed", "invalid_complexity"),
        "judge_disagreement_rate": judge_unstable / total if total else 0.0,
        "judge_repeat_count": repeat_count,
        "score_pairs_count": len(scored),
    }


def build_evidence_pack(
    experiment_dir: str | Path,
    *,
    project_root: str | Path = ".",
    snapshot: str | Path | Mapping[str, Any] | None = None,
    manual_review: str | Path | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a compact evidence-only pack without changing ``experiment_dir``."""

    experiment = Path(experiment_dir).resolve()
    root = Path(project_root).resolve()
    if not experiment.is_dir():
        raise EvidencePackRejected(f"experiment directory does not exist: {experiment}")
    files = _artifact_files(experiment)
    if not files:
        raise EvidencePackRejected("Evidence Pack requires published experiment artifacts")
    rows: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    configuration: dict[str, Any] = {}
    for path in files:
        if path.name == "experiment_statistics.json":
            raw_configuration = _read_json(path)
            configuration = {key: value for key, value in raw_configuration.items() if key in SAFE_CONFIGURATION_FIELDS and isinstance(value, (str, int, float, bool, type(None)))}
            artifacts.append({"artifact_ref": str(path.resolve()), "kind": "experiment_configuration_and_metrics"})
            continue
        records = _read_records(path)
        artifacts.append({"artifact_ref": str(path.resolve()), "kind": path.stem, "record_count": len(records)})
        rows.extend(_record_summary(record, path) for record in records)
    frozen_snapshot = _snapshot_from_value(snapshot, rows)
    reviews: list[dict[str, Any]] = []
    if manual_review:
        review_value = dict(manual_review) if isinstance(manual_review, Mapping) else _read_json(Path(manual_review))
        reviews.append({key: review_value.get(key) for key in ("review_id", "status", "reviewer", "conclusion", "evidence_refs")})
    pack_id = "EP-" + _hash({"experiment": str(experiment), "snapshot": frozen_snapshot["memory_snapshot_id"], "artifacts": artifacts})[7:23]
    return {
        "record_type": "global_judge_evidence_pack", "evidence_pack_version": EVIDENCE_PACK_VERSION,
        "evidence_pack_id": pack_id, "created_at": _now(), "experiment_dir": str(experiment),
        "experiment_configuration": configuration, "snapshot": frozen_snapshot,
        "artifacts": artifacts, "branch_records": rows, "metrics": _metrics(rows),
        "cost_and_duration": {key: configuration[key] for key in ("duration_seconds", "elapsed_seconds", "total_cost", "cost", "request_count", "evaluation_count", "model_calls") if key in configuration},
        "l1_experiment_facts": _memory_refs(experiment), "l2_strategy_card_candidates": _l2_cards(root),
        "manual_reviews": reviews,
        "data_boundary": "Formal artifact references and summaries only; no prompts, answers, rubrics, score prompts, or complete model responses.",
    }


def validate_evidence_pack(pack: Mapping[str, Any]) -> None:
    if _text(pack.get("record_type")) != "global_judge_evidence_pack":
        raise EvidencePackRejected("invalid Evidence Pack record_type")
    snapshot = _as_mapping(pack.get("snapshot"))
    if not _text(snapshot.get("memory_snapshot_id")):
        raise EvidencePackRejected("Evidence Pack requires a Snapshot")
    if not isinstance(pack.get("artifacts"), list) or not pack["artifacts"]:
        raise EvidencePackRejected("Evidence Pack requires formal artifact references")
    forbidden = {"prompt", "reference_answer", "rubric", "scoring_result", "score_prompt"}
    if forbidden.intersection(pack):
        raise EvidencePackRejected("Evidence Pack must not embed formal model material")


def _diagnose_row(row: Mapping[str, Any]) -> dict[str, Any]:
    label = _label(row)
    route = _as_mapping(row.get("route"))
    validation = _as_mapping(row.get("validation"))
    score_summary = _as_mapping(row.get("score_summary"))
    kind, level, reason, confidence = "evidence_insufficient", "sample/data", "No attributable failure signal is available in the evidence summary.", "low"
    if label == "score_increased":
        kind, level, reason, confidence = "business_failure", "operator generation", "The evolved question has negative gain (score_increased); do not treat it as a successful boundary.", "medium"
    elif label in {"not_applicable", "selected_then_not_applicable"}:
        level = "operator selection" if route.get("selected_operator") else "router"
        kind, reason, confidence = "business_failure", "A route/operator was not applicable to the observed sample.", "medium" if route else "low"
    elif label in {"validation_failed", "invalid_generation", "invalid_complexity"} or any(
        _as_mapping(value).get("passed") is False for value in validation.values()
    ):
        kind, level, reason, confidence = "business_failure", "validation", "The candidate failed a validation contract or generated invalid material.", "medium"
    elif "judge" in label and ("unstable" in label or "disagreement" in label) or score_summary.get("judge_consistent") is False:
        kind, level, reason, confidence = "judge_instability", "rubric/judge", "Repeated Judge evidence is unstable or disagrees; the conclusion requires review.", "medium"
    elif label in {"strategy_conflict", "memory_conflict"}:
        kind, level, reason, confidence = "strategy_conflict", "memory", "Conflicting strategy evidence requires reconciliation before it can be used in Shadow or release.", "medium"
    elif label in {"branch_error", "tool_error", "system_error"}:
        kind, level, reason, confidence = "system_failure", "search/cost", "A branch/system execution error prevented a reliable experiment result.", "high"
    elif label in {"budget_exhausted", "cost_excessive"}:
        kind, level, reason, confidence = "system_failure", "search/cost", "Search cost or budget exhausted before reliable evidence was collected.", "medium"
    return {
        "record_type": "global_judge_diagnosis", "diagnosis_version": DIAGNOSIS_VERSION,
        "diagnosis_id": "GJD-" + _hash({"ref": row.get("evidence_ref"), "label": label})[7:23],
        "sample_id": row.get("sample_id") or row.get("index"), "node_id": row.get("node_id"),
        "operator_id": row.get("operator_id") or row.get("operator_used") or route.get("selected_operator"),
        "effect_label": label, "diagnosis_kind": kind, "diagnosis_level": level,
        "failure_reason": reason, "confidence": confidence,
        "evidence_refs": [row["evidence_ref"]] if isinstance(row.get("evidence_ref"), Mapping) else [],
        "scope": _as_mapping(row.get("scope")),
        "recommended_action": "collect_more_evidence" if kind == "evidence_insufficient" else "shadow_validate_before_any_release",
        "human_confirmed": False,
    }


def validate_diagnosis(diagnosis: Mapping[str, Any]) -> None:
    if _text(diagnosis.get("diagnosis_level")) not in DIAGNOSIS_LEVELS:
        raise GlobalJudgeError("diagnosis must use a defined failure level")
    if _text(diagnosis.get("diagnosis_kind")) not in DIAGNOSIS_KINDS:
        raise GlobalJudgeError("diagnosis must distinguish its outcome kind")
    if not isinstance(diagnosis.get("evidence_refs"), list) or not diagnosis["evidence_refs"]:
        raise GlobalJudgeError("diagnosis requires evidence references")


def _proposal_change(level: str) -> str:
    changes = {
        "sample/data": "Add a scoped data-quality review; do not generalize from the affected sample.",
        "router": "Replay the routing evidence in Shadow and propose a narrow routing-card clarification.",
        "operator selection": "Observe a narrow operator eligibility/card adjustment in Shadow; do not hard-reject candidates.",
        "operator generation": "Shadow-test a generation constraint or operator-card clarification without changing the formal prompt.",
        "validation": "Review the validation diagnostic and evaluate whether it is a legitimate risk signal rather than a broad rejection rule.",
        "rubric/judge": "Run repeated Judge/Rubric evaluation and require reviewer confirmation before treating score changes as evidence.",
        "memory": "Create or downgrade an advisory strategy-card candidate with explicit applicability and exclusions.",
        "search/cost": "Shadow-test a bounded search/cost adjustment while monitoring opportunity loss.",
    }
    return changes[level]


def proposals_from_diagnoses(pack: Mapping[str, Any], diagnoses: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    validate_evidence_pack(pack)
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for diagnosis in diagnoses:
        validate_diagnosis(diagnosis)
        if diagnosis.get("diagnosis_kind") != "evidence_insufficient":
            grouped[_text(diagnosis.get("diagnosis_level"))].append(diagnosis)
    proposals: list[dict[str, Any]] = []
    for level, group in sorted(grouped.items()):
        refs = [ref for diagnosis in group for ref in diagnosis["evidence_refs"]]
        strength = "high" if len(group) >= 3 else "medium" if len(group) >= 2 else "low"
        risks = {str(diagnosis.get("diagnosis_kind")) for diagnosis in group}
        risk = "judge_instability_possible" if "judge_instability" in risks else ("system_failure_possible" if "system_failure" in risks else "requires_shadow_validation")
        scope = next((_as_mapping(diagnosis.get("scope")) for diagnosis in group if _as_mapping(diagnosis.get("scope"))), {})
        status = "needs_human_review" if strength == "low" or level == "rubric/judge" else "proposed"
        proposal = {
            "record_type": "optimization_proposal", "proposal_version": PROPOSAL_VERSION,
            "proposal_id": "GJ-PROP-" + _hash({"pack": pack["evidence_pack_id"], "level": level, "refs": refs})[7:23],
            "source_evidence_pack": pack["evidence_pack_id"], "diagnosis_level": level,
            "summary": f"{len(group)} traceable {level} diagnosis record(s) require offline validation.",
            "recommended_change": _proposal_change(level), "affected_scope": scope or {"scope": "only the cited evidence"},
            "evidence_strength": strength, "risk": risk, "status": status,
            "human_confirmed": False,
            "evidence_refs": refs, "verification_plan": "Run Replay/Holdout in Shadow, compare effectiveness, score_increased, invalid generation, Judge disagreement, and declared-scope fit.",
            "publish_gate": {"effective_rate_not_lower": True, "score_increased_rate_not_higher": True, "invalid_generation_rate_not_higher": True, "judge_disagreement_acceptable": True, "scope_match_required": True, "manual_review_required": True},
            "rollback_conditions": ["score_increased rate rises", "invalid generation rate rises", "Judge disagreement is unacceptable", "observed effect escapes the declared scope"],
        }
        validate_proposal(proposal, allow_active=False)
        proposals.append(proposal)
    return proposals


def validate_proposal(proposal: Mapping[str, Any], *, allow_active: bool = False) -> None:
    if _text(proposal.get("record_type")) != "optimization_proposal":
        raise ProposalRejected("invalid Optimization Proposal record_type")
    if not _text(proposal.get("proposal_id")) or not _text(proposal.get("source_evidence_pack")):
        raise ProposalRejected("proposal requires an ID and source Evidence Pack")
    if _text(proposal.get("diagnosis_level")) not in DIAGNOSIS_LEVELS:
        raise ProposalRejected("proposal diagnosis level is invalid")
    if not isinstance(proposal.get("evidence_refs"), list) or not proposal["evidence_refs"]:
        raise ProposalRejected("proposal requires traceable evidence references")
    if _text(proposal.get("evidence_strength")) not in {"low", "medium", "high"}:
        raise ProposalRejected("proposal requires evidence_strength")
    status = _text(proposal.get("status"))
    if status not in PROPOSAL_STATUSES:
        raise ProposalRejected("proposal status is invalid")
    if status == "active" and not allow_active:
        raise PolicyGuardRejected("Global Judge cannot independently publish an active proposal")
    if not _text(proposal.get("verification_plan")) or not isinstance(proposal.get("publish_gate"), Mapping):
        raise ProposalRejected("proposal requires a verification plan and publish gate")


def shadow_strategy_card(proposal: Mapping[str, Any]) -> dict[str, Any]:
    """Create an advisory card.  It is intentionally not connected to Router input."""

    validate_proposal(proposal)
    if proposal.get("evidence_strength") == "low" or proposal.get("status") == "needs_human_review":
        raise PolicyGuardRejected("insufficient or unreviewed proposal evidence cannot enter Shadow")
    return {
        "record_type": "shadow_strategy_card", "shadow_card_version": "shadow-strategy-card-v1",
        "shadow_card_id": "GJ-SHADOW-" + _hash(proposal["proposal_id"])[7:23], "proposal_id": proposal["proposal_id"],
        "status": "shadow", "affected_scope": proposal["affected_scope"], "recommended_change": proposal["recommended_change"],
        "evidence_refs": list(proposal["evidence_refs"]), "action_limit": "Offline/replay-only. It must not alter live Router input, operator plans, scoring, state, or active Memory.",
    }


def run_global_judge(pack: Mapping[str, Any]) -> dict[str, Any]:
    """Generate deterministic, evidence-bound diagnostics and proposal drafts."""

    validate_evidence_pack(pack)
    diagnoses = [_diagnose_row(row) for row in pack.get("branch_records", []) if isinstance(row, Mapping)]
    if not diagnoses:
        diagnoses = [{
            "record_type": "global_judge_diagnosis", "diagnosis_version": DIAGNOSIS_VERSION,
            "diagnosis_id": "GJD-" + _hash(pack["evidence_pack_id"])[7:23], "sample_id": None, "node_id": None,
            "operator_id": None, "effect_label": "unclassified", "diagnosis_kind": "evidence_insufficient",
            "diagnosis_level": "sample/data", "failure_reason": "No branch summaries were available for diagnosis.", "confidence": "low",
            "evidence_refs": [{"artifact_ref": entry["artifact_ref"]} for entry in pack["artifacts"]], "scope": {}, "recommended_action": "collect_more_evidence", "human_confirmed": False,
        }]
    proposals = proposals_from_diagnoses(pack, diagnoses)
    shadow_cards: list[dict[str, Any]] = []
    shadow_rejections: list[dict[str, str]] = []
    for proposal in proposals:
        try:
            shadow_cards.append(shadow_strategy_card(proposal))
        except PolicyGuardRejected as exc:
            shadow_rejections.append({"proposal_id": proposal["proposal_id"], "reason": str(exc)})
    kinds = Counter(str(diagnosis["diagnosis_kind"]) for diagnosis in diagnoses)
    return {
        "record_type": "global_judge_run_report", "global_judge_version": GLOBAL_JUDGE_VERSION,
        "run_id": "GJ-RUN-" + _hash(pack["evidence_pack_id"])[7:23], "created_at": _now(),
        "source_evidence_pack": pack["evidence_pack_id"], "diagnoses": diagnoses, "proposals": proposals,
        "shadow_strategy_cards": shadow_cards, "shadow_rejections": shadow_rejections,
        "diagnosis_summary": dict(sorted(kinds.items())),
        "conclusion": "Offline advisory output only. No formal experiment artifact or live runtime policy was modified.",
    }


def _scope_match(scope: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    declared = {key: _text(value).lower() for key, value in scope.items() if _text(value) and key in {"scene_family", "question_form", "reasoning_mechanism"}}
    if not declared:
        return True
    actual = _as_mapping(row.get("scope"))
    return all(_text(actual.get(key)).lower() == value for key, value in declared.items())


def replay_holdout(pack: Mapping[str, Any], proposal: Mapping[str, Any], *, holdout: str | Path | Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Read-only replay evaluator; it reports a counterfactual scope, never routes live work."""

    validate_evidence_pack(pack)
    validate_proposal(proposal)
    if holdout is None:
        rows = [dict(row) for row in pack.get("branch_records", []) if isinstance(row, Mapping)]
        source = "evidence_pack_branch_summaries"
    elif isinstance(holdout, (str, Path)):
        rows = [_record_summary(row, Path(holdout)) for row in _read_records(Path(holdout))]
        source = str(Path(holdout).resolve())
    else:
        rows = [dict(row) for row in holdout if isinstance(row, Mapping)]
        source = "provided_holdout_records"
    in_scope = [row for row in rows if _scope_match(_as_mapping(proposal.get("affected_scope")), row)]
    baseline, shadow = _metrics(rows), _metrics(in_scope)
    checks = {
        "effective_rate_not_lower": shadow["effective_rate"] >= baseline["effective_rate"],
        "score_increased_rate_not_higher": shadow["score_increased_rate"] <= baseline["score_increased_rate"],
        "invalid_generation_rate_not_higher": shadow["invalid_generation_rate"] <= baseline["invalid_generation_rate"],
        "judge_disagreement_acceptable": shadow["judge_disagreement_rate"] <= baseline["judge_disagreement_rate"],
        "scope_match_required": len(in_scope) > 0,
    }
    return {
        "record_type": "replay_holdout_result", "replay_version": REPLAY_VERSION,
        "replay_id": "GJ-REPLAY-" + _hash({"proposal": proposal["proposal_id"], "source": source})[7:23],
        "proposal_id": proposal["proposal_id"], "source_evidence_pack": pack["evidence_pack_id"], "holdout_source": source,
        "mode": "shadow_only", "baseline_metrics": baseline, "shadow_scope_metrics": shadow,
        "would_affect_records": len(in_scope), "checks": checks,
        "status": "needs_human_review" if all(checks.values()) else "rejected_insufficient_evidence",
        "action_limit": "No Router, operator plan, score, state, or formal artifact was changed during Replay/Holdout.",
    }


def validate_replay_holdout(result: Mapping[str, Any]) -> None:
    if _text(result.get("record_type")) != "replay_holdout_result":
        raise PublicationRejected("Replay/Holdout result is required")
    checks = _as_mapping(result.get("checks"))
    required = {"effective_rate_not_lower", "score_increased_rate_not_higher", "invalid_generation_rate_not_higher", "judge_disagreement_acceptable", "scope_match_required"}
    if not required.issubset(checks) or not all(checks[name] is True for name in required):
        raise PublicationRejected("Replay/Holdout publication gates did not pass")


def validate_approval(approval: Mapping[str, Any]) -> None:
    if not _text(approval.get("approved_by")) or not _text(approval.get("approved_at")):
        raise PublicationRejected("active publication requires an approval record")
    if _text(approval.get("decision")) not in {"approve_active", "approve_rollback", "approve_retire"}:
        raise PublicationRejected("approval record requires an explicit governed decision")
    if not _text(approval.get("risk_acknowledgement")):
        raise PublicationRejected("approval record requires risk acknowledgement")


class GlobalJudgeGovernance:
    """Append-only publication/rollback ledger, isolated from formal artifacts."""

    def __init__(self, project_root: str | Path) -> None:
        self.project_root = Path(project_root).resolve()
        self.root = self.project_root / "memory_global" / "global_judge"
        self.ledger = self.root / "publication_ledger.jsonl"

    def _append(self, record: Mapping[str, Any]) -> None:
        existing = self.ledger.read_text(encoding="utf-8") if self.ledger.exists() else ""
        _atomic_write(self.ledger, existing + _json(record) + "\n")

    def publish_active(self, proposal: Mapping[str, Any], replay: Mapping[str, Any], approval: Mapping[str, Any], *, actor_role: str) -> dict[str, Any]:
        policy_guard(action="publish active", actor_role=actor_role)
        if actor_role != "publisher":
            raise PolicyGuardRejected("Global Judge has read/experiment-write authority only; active publication requires publisher authority")
        validate_proposal(proposal)
        validate_replay_holdout(replay)
        validate_approval(approval)
        if replay.get("proposal_id") != proposal.get("proposal_id"):
            raise PublicationRejected("Replay/Holdout result does not belong to this proposal")
        publication_id = "GJ-PUB-" + uuid.uuid4().hex[:16]
        snapshot_id = "GJ-SNAPSHOT-" + _hash({"proposal": proposal["proposal_id"], "publication": publication_id})[7:23]
        record = {
            "record_type": "global_judge_publication", "publication_id": publication_id, "status": "active",
            "proposal_id": proposal["proposal_id"], "source_evidence_pack": proposal["source_evidence_pack"],
            "replay_id": replay["replay_id"], "approval": dict(approval), "active_snapshot_id": snapshot_id,
            "rollback_conditions": proposal["rollback_conditions"], "published_at": _now(),
            "authority": "publisher", "scope": proposal["affected_scope"],
            "action_limit": "Publication ledger only; formal prompt/router/rubric/operator/memory/score/state files are not mutated by Global Judge.",
        }
        self._append(record)
        _atomic_write(self.root / "active_strategy_snapshot.json", json.dumps({"snapshot_id": snapshot_id, "publication_id": publication_id, "proposal_id": proposal["proposal_id"], "scope": proposal["affected_scope"], "created_at": record["published_at"]}, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return record

    def rollback(self, publication_id: str, approval: Mapping[str, Any], *, actor_role: str, reason: str) -> dict[str, Any]:
        if actor_role != "publisher":
            raise PolicyGuardRejected("rollback requires publisher authority")
        validate_approval(approval)
        if _text(approval.get("decision")) != "approve_rollback":
            raise PublicationRejected("rollback requires approve_rollback approval")
        if not _text(reason):
            raise PublicationRejected("rollback requires a reason")
        record = {"record_type": "global_judge_publication", "publication_id": "GJ-ROLLBACK-" + uuid.uuid4().hex[:16], "status": "rolled_back", "reverts_publication_id": publication_id, "approval": dict(approval), "reason": reason, "published_at": _now(), "authority": "publisher"}
        self._append(record)
        return record

    def retire(self, publication_id: str, approval: Mapping[str, Any], *, actor_role: str, reason: str) -> dict[str, Any]:
        if actor_role != "publisher":
            raise PolicyGuardRejected("retirement requires publisher authority")
        validate_approval(approval)
        if _text(approval.get("decision")) != "approve_retire":
            raise PublicationRejected("retirement requires approve_retire approval")
        if not _text(reason):
            raise PublicationRejected("retirement requires a reason")
        record = {"record_type": "global_judge_publication", "publication_id": "GJ-RETIRE-" + uuid.uuid4().hex[:16], "status": "retired", "reverts_publication_id": publication_id, "approval": dict(approval), "reason": reason, "published_at": _now(), "authority": "publisher"}
        self._append(record)
        return record


def write_json(path: str | Path, value: Mapping[str, Any], *, project_root: str | Path = ".") -> None:
    """Write only to the isolated Global Judge workspace, never formal outputs."""

    output = Path(path).resolve()
    governance_root = (Path(project_root).resolve() / "memory_global" / "global_judge").resolve()
    try:
        output.relative_to(governance_root)
    except ValueError as exc:
        raise PolicyGuardRejected(
            f"Global Judge reports must be written below {governance_root}; refusing an ungoverned output path"
        ) from exc
    if output.name in FORMAL_ARTIFACT_BASENAMES:
        raise PolicyGuardRejected("Global Judge cannot overwrite a formal pipeline artifact")
    _atomic_write(output, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _load_proposal(path: str | Path) -> dict[str, Any]:
    value = _read_json(Path(path))
    if "proposals" in value and isinstance(value["proposals"], list):
        if len(value["proposals"]) != 1:
            raise GlobalJudgeError("provide a single proposal JSON object")
        return _as_mapping(value["proposals"][0])
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline Global Judge and governed strategy publication")
    sub = parser.add_subparsers(dest="command", required=True)
    evidence = sub.add_parser("build-evidence")
    evidence.add_argument("--exp-dir", required=True); evidence.add_argument("--snapshot", required=True); evidence.add_argument("--output", required=True); evidence.add_argument("--project-root", default="."); evidence.add_argument("--manual-review")
    judge = sub.add_parser("judge")
    judge.add_argument("--evidence-pack", required=True); judge.add_argument("--output", required=True); judge.add_argument("--project-root", default=".")
    replay = sub.add_parser("replay-holdout")
    replay.add_argument("--evidence-pack", required=True); replay.add_argument("--proposal", required=True); replay.add_argument("--output", required=True); replay.add_argument("--holdout"); replay.add_argument("--project-root", default=".")
    publish = sub.add_parser("publish-active")
    publish.add_argument("--proposal", required=True); publish.add_argument("--replay", required=True); publish.add_argument("--approval", required=True); publish.add_argument("--project-root", default="."); publish.add_argument("--actor-role", default="global_judge")
    rollback = sub.add_parser("rollback")
    rollback.add_argument("--publication-id", required=True); rollback.add_argument("--approval", required=True); rollback.add_argument("--reason", required=True); rollback.add_argument("--project-root", default="."); rollback.add_argument("--actor-role", default="global_judge")
    retire = sub.add_parser("retire")
    retire.add_argument("--publication-id", required=True); retire.add_argument("--approval", required=True); retire.add_argument("--reason", required=True); retire.add_argument("--project-root", default="."); retire.add_argument("--actor-role", default="global_judge")
    args = parser.parse_args(argv)
    if args.command == "build-evidence":
        result = build_evidence_pack(args.exp_dir, project_root=args.project_root, snapshot=args.snapshot, manual_review=args.manual_review); write_json(args.output, result, project_root=args.project_root)
    elif args.command == "judge":
        result = run_global_judge(_read_json(Path(args.evidence_pack))); write_json(args.output, result, project_root=args.project_root)
    elif args.command == "replay-holdout":
        result = replay_holdout(_read_json(Path(args.evidence_pack)), _load_proposal(args.proposal), holdout=args.holdout); write_json(args.output, result, project_root=args.project_root)
    elif args.command == "publish-active":
        result = GlobalJudgeGovernance(args.project_root).publish_active(_load_proposal(args.proposal), _read_json(Path(args.replay)), _read_json(Path(args.approval)), actor_role=args.actor_role)
    elif args.command == "rollback":
        result = GlobalJudgeGovernance(args.project_root).rollback(args.publication_id, _read_json(Path(args.approval)), actor_role=args.actor_role, reason=args.reason)
    else:
        result = GlobalJudgeGovernance(args.project_root).retire(args.publication_id, _read_json(Path(args.approval)), actor_role=args.actor_role, reason=args.reason)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
