"""Evidence-only implementation of 22B and 25B/22C-4.

The primary question-evolution chain remains deliberately untouched.  This
module consumes published sidecars and produces new sidecars only:

* ``induce`` groups evidence-bound 22A observations into proposed mechanisms;
* ``validate`` evaluates mechanism/operator claims on a frozen held-out set;
* ``route-audit`` and ``route-replay`` compare suggestions with frozen routes.

No command rewrites scored records, routing candidates, state, or local memory.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import statistics
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from agent_runtime.global_memory import GlobalMemoryStore, SnapshotUnavailable
from operator_registry import OPERATOR_SPECS
from question_behavior_analysis import validate_observer_result, validate_shadow_record


MECHANISM_VERSION = "question-mechanism-v1"
VALIDATION_VERSION = "mechanism-effect-validation-v1"
ROUTE_AUDIT_VERSION = "mechanism-route-audit-v1"
REQUIRED_FREEZE_FIELDS = {
    "question_pool", "answer_model", "answer_parameters", "qwen_judge_config",
    "gpt_recheck_config", "rubric_version", "memory_snapshot_id", "thresholds",
    "manual_review_rules", "split", "experiment_kind",
}
RISK_LABELS = {"judge_unstable", "cross_judge_disputed", "rubric_or_question_risk"}
RISK_WORDS = ("risk", "rubric", "question", "judge", "ambigu", "unanswer", "format")


class MechanismGovernanceError(ValueError):
    """Raised when a sidecar lacks the required auditable evidence."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _atomic_write(path: str | Path, records: Iterable[Mapping[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=target.name + ".", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(_json(record) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _read_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if not source.is_file():
        raise MechanismGovernanceError(f"sidecar does not exist: {source}")
    if source.suffix == ".json":
        raw = json.loads(source.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            return [_mapping(row) for row in raw]
        return [_mapping(raw)]
    rows: list[dict[str, Any]] = []
    for line, raw in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MechanismGovernanceError(f"invalid JSONL at {source}:{line}: {exc.msg}") from exc
        if not isinstance(value, Mapping):
            raise MechanismGovernanceError(f"JSON object required at {source}:{line}")
        rows.append(dict(value))
    return rows


def _write_json(path: str | Path, value: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, target)


def _root_id(record: Mapping[str, Any]) -> str:
    for key in ("root_sample_id", "sample_id", "index", "node_id"):
        value = _text(record.get(key))
        if value:
            return value
    return ""


def _review_index(reviews: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for row in reviews:
        for key in ("mechanism_id", "analysis_id", "review_target_id"):
            identity = _text(row.get(key))
            if identity:
                index[identity] = dict(row)
    return index


def _is_approved(review: Mapping[str, Any] | None) -> bool:
    review = review or {}
    return bool(review.get("approved") is True or _text(review.get("status")).lower() in {"approved", "accepted", "passed"})


def _taxonomy(record: Mapping[str, Any], mechanism: str = "") -> dict[str, str]:
    source = _mapping(record.get("taxonomy"))
    signature = _mapping(record.get("sample_signature"))
    metadata = _mapping(_mapping(record.get("meta_info")).get("question_evolution_metadata"))
    profile = _mapping(record.get("sample_profile"))
    diagnosis = _mapping(record.get("overscore_diagnosis"))
    def first(*values: Any) -> str:
        return next((text for text in (_text(value) for value in values) if text), "unknown")
    return {
        "scene_family": first(source.get("scene_family"), signature.get("scene_family"), record.get("scene_family"), profile.get("scene_family"), profile.get("domain")),
        "question_form": first(source.get("question_form"), signature.get("question_form"), record.get("surface_form_family"), profile.get("problem_shape")),
        "reasoning_mechanism": first(mechanism, source.get("reasoning_mechanism"), signature.get("reasoning_mechanism"), metadata.get("expected_qwen_failure")),
        "overscore_pattern": first(source.get("overscore_pattern"), signature.get("overscore_pattern"), diagnosis.get("candidate_overscore_cause"), record.get("failure_type")),
        "version_compatibility_group": first(source.get("version_compatibility_group"), record.get("evaluation_config_fingerprint"), metadata.get("operator_registry_version")),
    }


def _mechanism_labels(observer: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    for item in observer.get("candidate_mechanisms") or []:
        if isinstance(item, Mapping):
            item = item.get("mechanism_id") or item.get("mechanism") or item.get("label") or item.get("summary")
        label = " ".join(_text(item).lower().split())
        if label and label not in {"unknown", "none", "n/a"} and label not in values:
            values.append(label)
    return values


def _canonical_operator(value: Any) -> str:
    operator = _text(value)
    if operator in OPERATOR_SPECS:
        return operator
    matches = [registered for registered in OPERATOR_SPECS if registered == operator or registered.startswith(operator + "_")]
    return matches[0] if len(matches) == 1 else operator


def _operator_ids(record: Mapping[str, Any]) -> list[str]:
    result: list[str] = []
    route = _mapping(record.get("operator_route"))
    for value in (record.get("candidate_operator"), record.get("operator_used"), record.get("operator_id"), route.get("selected_operator")):
        operator = _canonical_operator(value)
        if operator in OPERATOR_SPECS and operator not in result:
            result.append(operator)
    return result


def _evidence_ref(record: Mapping[str, Any], path: str | Path) -> dict[str, Any]:
    stats = _mapping(record.get("group_statistics"))
    return {
        "artifact_ref": str(Path(path).resolve()), "analysis_id": _text(record.get("analysis_id")),
        "root_sample_id": _root_id(record), "node_id": _text(record.get("node_id")),
        "trial_ids": list(record.get("source_trial_ids") or []),
        "rubric_titles": sorted({_text(row.get("title")) for row in stats.get("item_differences", []) if isinstance(row, Mapping) and _text(row.get("title"))}),
    }


def _risk_kind(record: Mapping[str, Any]) -> str | None:
    labels = {_text(value) for value in record.get("behavior_labels") or []}
    if labels & RISK_LABELS:
        return "rule_diagnosed_risk"
    observer = _mapping(record.get("observer_result"))
    risk = observer.get("question_or_rubric_risk")
    if risk not in (None, "", False, [], {}):
        return "observer_reported_risk"
    return None


def induce_candidates(
    analyses: Sequence[Mapping[str, Any]], *, analysis_path: str | Path,
    source_records: Sequence[Mapping[str, Any]] = (), reviews: Sequence[Mapping[str, Any]] = (),
    min_independent_roots: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Build proposed mechanism cards and non-capability risk candidates.

    A proposal is intentionally conservative: it needs two independent roots,
    evidence-bound observer output, and at least one explicit counterexample.
    Other observations remain rejection facts so the missing evidence is auditable.
    """

    source_by_root = {_root_id(row): row for row in source_records if _root_id(row)}
    review_by_id = _review_index(reviews)
    mechanism_rows: dict[tuple[str, str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    risks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    all_by_scope: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    rejected: list[dict[str, Any]] = []
    for raw in analyses:
        record = copy.deepcopy(dict(raw))
        valid, reason = validate_shadow_record(record)
        if not valid:
            rejected.append({"record_type": "mechanism_induction_rejection", "reason": reason, "analysis_id": _text(record.get("analysis_id"))})
            continue
        source = source_by_root.get(_root_id(record), {})
        merged = {**source, **record}
        risk = _risk_kind(record)
        observer = _mapping(record.get("observer_result"))
        if risk:
            risks[risk].append({"record": record, "source": source, "review": review_by_id.get(_text(record.get("analysis_id")))})
            continue
        if record.get("observer_status") != "completed":
            rejected.append({"record_type": "mechanism_induction_rejection", "reason": "observer_not_completed", "analysis_id": _text(record.get("analysis_id"))})
            continue
        valid_observer, observer_reason = validate_observer_result(observer, record)
        if not valid_observer:
            rejected.append({"record_type": "mechanism_induction_rejection", "reason": observer_reason, "analysis_id": _text(record.get("analysis_id"))})
            continue
        labels = _mechanism_labels(observer)
        if not labels:
            rejected.append({"record_type": "mechanism_induction_rejection", "reason": "observer_has_no_candidate_mechanism", "analysis_id": _text(record.get("analysis_id"))})
            continue
        for label in labels:
            taxonomy = _taxonomy(merged, label)
            scope = (taxonomy["scene_family"], taxonomy["question_form"], taxonomy["version_compatibility_group"])
            row = {"record": record, "source": source, "review": review_by_id.get(_text(record.get("analysis_id"))), "taxonomy": taxonomy, "label": label}
            mechanism_rows[(label, *(taxonomy[key] for key in ("scene_family", "question_form", "reasoning_mechanism", "overscore_pattern", "version_compatibility_group")))].append(row)
            all_by_scope[scope].append(row)

    outputs: list[dict[str, Any]] = []
    for mechanism_key, rows in sorted(mechanism_rows.items()):
        label = mechanism_key[0]
        roots = sorted({_root_id(row["record"]) for row in rows if _root_id(row["record"])})
        taxonomy = rows[0]["taxonomy"]
        scope = (taxonomy["scene_family"], taxonomy["question_form"], taxonomy["version_compatibility_group"])
        counterexamples = [
            _evidence_ref(row["record"], analysis_path)
            for row in all_by_scope[scope] if row["label"] != label
        ]
        mechanism_id = "MECH-" + _hash({"label": label, "taxonomy": taxonomy})[:16]
        if len(roots) < min_independent_roots or not counterexamples:
            rejected.append({"record_type": "mechanism_induction_rejection", "mechanism_id": mechanism_id, "reason": "independent_evidence_or_counterexample_insufficient", "root_sample_ids": roots, "counterexample_count": len(counterexamples)})
            continue
        evidence = [_evidence_ref(row["record"], analysis_path) for row in rows]
        operators = sorted({operator for row in rows for operator in _operator_ids(row["source"])})
        applicability = [f"Observed across {len(roots)} independent root samples with the stated taxonomy."]
        exclusions = ["Do not use when question/rubric risk or Judge instability is present.", "Do not use outside the cited version-compatibility group."]
        outputs.append({
            "record_type": "mechanism_candidate", "mechanism_candidate_version": MECHANISM_VERSION,
            "mechanism_id": mechanism_id, "status": "proposed", "card_type": "capability_mechanism",
            "mechanism_summary": label, "taxonomy": taxonomy, "linked_operator_ids": operators,
            "root_sample_ids": roots, "evidence_refs": evidence, "counterexamples": counterexamples,
            "applicability_conditions": applicability, "exclusion_conditions": exclusions,
            "manual_review": {"status": "approved" if any(_is_approved(row["review"]) for row in rows) else "not_reviewed"},
            "created_at": _now(), "action_limit": "Proposed evidence only; it must not alter routing, operator candidates, scores, state, or local memory.",
        })
    for risk, rows in sorted(risks.items()):
        roots = sorted({_root_id(row["record"]) for row in rows if _root_id(row["record"])})
        taxonomy = _taxonomy(rows[0]["source"] or rows[0]["record"], risk)
        outputs.append({
            "record_type": "mechanism_candidate", "mechanism_candidate_version": MECHANISM_VERSION,
            "mechanism_id": "RISK-" + _hash({"risk": risk, "taxonomy": taxonomy})[:16], "status": "proposed",
            "card_type": "risk_pattern", "mechanism_summary": risk, "taxonomy": taxonomy,
            "linked_operator_ids": [], "root_sample_ids": roots,
            "evidence_refs": [_evidence_ref(row["record"], analysis_path) for row in rows],
            "counterexamples": [], "applicability_conditions": ["Use only as a review or governance signal."],
            "exclusion_conditions": ["Never publish this risk as a capability mechanism."],
            "manual_review": {"status": "not_reviewed"}, "created_at": _now(),
            "action_limit": "Risk signal only; it must not recommend, remove, or reorder operators.",
        })
    return outputs, rejected


def publish_facts(candidates: Sequence[Mapping[str, Any]], *, qualification: Mapping[str, Mapping[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Create the compact 25A/25B hand-off; this does not publish a card."""

    qualification = qualification or {}
    facts: list[dict[str, Any]] = []
    for candidate in candidates:
        if _text(candidate.get("record_type")) != "mechanism_candidate":
            continue
        mechanism_id = _text(candidate.get("mechanism_id"))
        validation = _mapping(qualification.get(mechanism_id))
        approved = _is_approved(_mapping(validation.get("manual_review")))
        lifecycle = "qualified" if validation.get("qualification_status") == "qualified" and approved else "proposed"
        facts.append({
            "record_type": "mechanism_publish_candidate", "mechanism_publish_version": MECHANISM_VERSION,
            "mechanism_id": mechanism_id, "target_card_type": "risk_pattern" if candidate.get("card_type") == "risk_pattern" else ("positive_strategy" if candidate.get("linked_operator_ids") else "system_diagnosis"),
            "requested_status": lifecycle, "validation_status": _text(validation.get("validation_status")) or "not_validated",
            "manual_review": _mapping(validation.get("manual_review")) or _mapping(candidate.get("manual_review")),
            "taxonomy": _mapping(candidate.get("taxonomy")), "operator_ids": list(candidate.get("linked_operator_ids") or []),
            "mechanism_summary": _text(candidate.get("mechanism_summary")), "applicability_conditions": list(candidate.get("applicability_conditions") or []),
            "exclusion_conditions": list(candidate.get("exclusion_conditions") or []), "evidence_refs": list(candidate.get("evidence_refs") or []),
            "counterexamples": list(candidate.get("counterexamples") or []), "action_limit": "Publication candidate only; Global Memory governance decides any card lifecycle transition.",
        })
    return facts


def _freeze(config: Mapping[str, Any]) -> dict[str, Any]:
    missing = sorted(name for name in REQUIRED_FREEZE_FIELDS if name not in config or config[name] in (None, "", [], {}))
    if missing:
        raise MechanismGovernanceError("frozen evaluation configuration missing: " + ", ".join(missing))
    if _text(config.get("experiment_kind")) not in {"retrospective", "forward"}:
        raise MechanismGovernanceError("experiment_kind must be retrospective or forward")
    return dict(config)


def _effect_fields(row: Mapping[str, Any]) -> dict[str, Any]:
    effect = _mapping(row.get("effect_analysis"))
    validation = _mapping(row.get("validation_result"))
    validation.update(_mapping(row.get("difficulty_gain_validation")))
    before = _number(row.get("previous_score_rate"))
    if before is None:
        before = _number(effect.get("previous_score_rate") or effect.get("before_score"))
    after = _number(row.get("score_rate"))
    if after is None:
        after = _number(effect.get("score_rate") or effect.get("after_score"))
    answer_volatility = _number(row.get("answer_volatility")) or _number(effect.get("answer_volatility")) or 0.0
    judge_volatility = _number(row.get("judge_volatility")) or _number(effect.get("judge_volatility")) or 0.0
    return {
        "before": before, "after": after, "score_drop": before - after if before is not None and after is not None else None,
        "volatility": max(answer_volatility, judge_volatility), "label": _text(effect.get("effect_label") or effect.get("label") or row.get("effect_label")),
        "target_hit": bool(row.get("target_mechanism_hit") is True or effect.get("target_mechanism_hit") is True),
        "high_answer_passes": bool(row.get("high_answer_passes") is True or effect.get("high_answer_passes") is True),
        "invalid": bool(validation.get("passed") is False or row.get("question_or_rubric_risk") or validation.get("hard_risk")),
        "operator_id": _canonical_operator(row.get("operator_used") or row.get("candidate_operator") or row.get("operator_id") or _mapping(row.get("operator_route")).get("selected_operator")),
    }


def validate_effects(
    candidates: Sequence[Mapping[str, Any]], effects: Sequence[Mapping[str, Any]], *, config: Mapping[str, Any],
    reviews: Sequence[Mapping[str, Any]] = (), min_holdout_samples: int = 2,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    frozen = _freeze(config)
    reviews_by_id = _review_index(reviews)
    threshold = _number(_mapping(frozen.get("thresholds")).get("min_score_drop")) or 0.0
    validations: list[dict[str, Any]] = []
    matrix_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        if candidate.get("card_type") != "capability_mechanism":
            continue
        source_roots = set(str(value) for value in candidate.get("root_sample_ids") or [])
        matched: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
        for effect in effects:
            root = _root_id(effect)
            if not root or root in source_roots:
                continue
            values = _effect_fields(effect)
            if values["operator_id"] not in set(candidate.get("linked_operator_ids") or []):
                continue
            matched.append((effect, values))
        stable = [values for _, values in matched if values["score_drop"] is not None and values["score_drop"] > max(values["volatility"], threshold)]
        hits = [values for values in stable if values["target_hit"] and values["high_answer_passes"] and not values["invalid"]]
        increased = [values for _, values in matched if values["score_drop"] is not None and values["score_drop"] < 0]
        invalid = [values for _, values in matched if values["invalid"]]
        holdout_roots = sorted({_root_id(row) for row, _ in matched if _root_id(row)})
        independently_reproduced = len(holdout_roots) >= min_holdout_samples and len(hits) >= min_holdout_samples
        review = reviews_by_id.get(_text(candidate.get("mechanism_id")))
        qualified = independently_reproduced and not increased and not invalid and _is_approved(review)
        taxonomy = _mapping(candidate.get("taxonomy"))
        validation = {
            "record_type": "mechanism_effect_validation", "mechanism_effect_validation_version": VALIDATION_VERSION,
            "mechanism_id": candidate.get("mechanism_id"), "validation_status": "validated" if independently_reproduced and not increased and not invalid else "insufficient_or_confounded_evidence",
            "qualification_status": "qualified" if qualified else "proposed", "manual_review": dict(review or {"status": "not_reviewed"}),
            "frozen_evaluation_config": frozen, "source_root_sample_ids": sorted(source_roots), "holdout_root_sample_ids": holdout_roots,
            "matched_effect_count": len(matched), "stable_drop_count": len(stable), "target_mechanism_hit_count": len(hits),
            "score_increased_count": len(increased), "invalid_or_risk_count": len(invalid), "taxonomy": taxonomy,
            "created_at": _now(), "action_limit": "Validation evidence only; qualification never mutates a route, score, state, or local Memory.",
        }
        validations.append(validation)
        buckets: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for _, values in matched:
            key = (str(candidate.get("mechanism_id")), values["operator_id"] or "unknown", taxonomy.get("question_form", "unknown"), taxonomy.get("scene_family", "unknown"), taxonomy.get("version_compatibility_group", "unknown"))
            buckets[key].append(values)
        for key, values in buckets.items():
            score_changes = [-(value["score_drop"]) for value in values if value["score_drop"] is not None]
            matrix_rows.append({
                "record_type": "mechanism_effect_matrix", "mechanism_effect_validation_version": VALIDATION_VERSION,
                "mechanism_id": key[0], "operator_id": key[1], "question_form": key[2], "scene_family": key[3], "version_compatibility_group": key[4],
                "sample_count": len(values), "mean_score_change": statistics.fmean(score_changes) if score_changes else None,
                "stable_drop_rate": sum(value in stable for value in values) / len(values),
                "invalid_generation_rate": sum(value["invalid"] for value in values) / len(values),
                "score_increased_rate": sum(value["score_drop"] is not None and value["score_drop"] < 0 for value in values) / len(values),
                "target_mechanism_hit_rate": sum(value in hits for value in values) / len(values),
            })
    report = {"mechanism_effect_validation_version": VALIDATION_VERSION, "frozen_config_hash": _hash(frozen), "candidate_count": len(validations), "qualified_count": sum(row["qualification_status"] == "qualified" for row in validations), "matrix_rows": len(matrix_rows)}
    return validations, matrix_rows, report


def _match_taxonomy(candidate: Mapping[str, Any], route: Mapping[str, Any]) -> bool:
    wanted, actual = _mapping(candidate.get("taxonomy")), _taxonomy(route)
    for key in ("scene_family", "question_form", "version_compatibility_group"):
        if wanted.get(key) not in (None, "", "unknown") and actual.get(key) not in (None, "", "unknown") and wanted[key] != actual[key]:
            return False
    return True


def route_audit(
    routes: Sequence[Mapping[str, Any]], candidates: Sequence[Mapping[str, Any]], validations: Sequence[Mapping[str, Any]], *,
    project_root: str | Path, snapshot_id: str, mode: str = "audit", approval: Mapping[str, Any] | None = None,
    rollback: bool = False,
) -> list[dict[str, Any]]:
    if mode not in {"audit", "limited"}:
        raise MechanismGovernanceError("route mode must be audit or limited")
    try:
        store = GlobalMemoryStore(project_root)
        snapshot = store.load_snapshot(snapshot_id)
    except SnapshotUnavailable as exc:
        raise MechanismGovernanceError(str(exc)) from exc
    if snapshot.get("mode") != "global_memory" or not _mapping(snapshot.get("card_versions")):
        raise MechanismGovernanceError("limited routing audit requires a stable non-empty Global Memory snapshot")
    frozen_card_ids = set(_mapping(snapshot.get("card_versions")).keys())
    qualified_card_mechanisms = {
        _text(card.get("mechanism_id"))
        for card in store._cards()
        if card.get("card_id") in frozen_card_ids and card.get("status") == "qualified"
    }
    validation_by_id = {_text(row.get("mechanism_id")): row for row in validations}
    outputs: list[dict[str, Any]] = []
    for route_record in routes:
        route = _mapping(route_record.get("operator_route"))
        selected = _text(route.get("selected_operator") or route.get("primary_operator"))
        canonical_selected = _canonical_operator(selected)
        suggestions: list[dict[str, Any]] = []
        for candidate in candidates:
            validation = validation_by_id.get(_text(candidate.get("mechanism_id")), {})
            if candidate.get("card_type") != "capability_mechanism" or not _match_taxonomy(candidate, route_record):
                continue
            suggestion = {
                "mechanism_id": candidate.get("mechanism_id"), "suggested_operator_ids": list(candidate.get("linked_operator_ids") or []),
                "qualification_status": validation.get("qualification_status", "proposed"), "validation_status": validation.get("validation_status", "not_validated"),
                "source_root_sample_ids": list(candidate.get("root_sample_ids") or []),
                "reason": "taxonomy-matched mechanism evidence", "action_limit": "audit_only",
            }
            suggestions.append(suggestion)
        qualified = [row for row in suggestions if row["qualification_status"] == "qualified" and row["validation_status"] == "validated"]
        can_limited = bool(
            qualified
            and any(_text(row["mechanism_id"]) in qualified_card_mechanisms for row in qualified)
            and _is_approved(approval)
        )
        if rollback:
            disposition = "rollback_to_audit"
        elif mode == "limited" and not can_limited:
            disposition = "limited_integration_blocked"
        elif mode == "limited":
            disposition = "limited_integration_eligible"
        else:
            disposition = "audit_only"
        outputs.append({
            "record_type": "mechanism_route_audit", "mechanism_route_audit_version": ROUTE_AUDIT_VERSION,
            "root_sample_id": _root_id(route_record), "node_id": _text(route_record.get("node_id")), "memory_snapshot_id": snapshot_id,
            "route_selected_operator": selected, "mechanism_suggestions": suggestions,
            "suggestion_matches_existing_route": any(canonical_selected in row["suggested_operator_ids"] for row in suggestions),
            "disposition": disposition, "approval": dict(approval or {"status": "not_reviewed"}), "rollback_requested": rollback,
            "action_limit": "Sidecar only: never append, remove, reorder, or bypass operator candidates, plans, or validation gates; rollback restores audit-only behavior.",
            "created_at": _now(),
        })
    return outputs


def route_replay(audits: Sequence[Mapping[str, Any]], outcomes: Sequence[Mapping[str, Any]], *, frozen_config: Mapping[str, Any]) -> dict[str, Any]:
    frozen = _freeze(frozen_config)
    outcome_by_root = {_root_id(row): row for row in outcomes if _root_id(row)}
    rows = []
    for audit in audits:
        outcome = outcome_by_root.get(_root_id(audit))
        if not outcome:
            continue
        source_roots = {
            str(root)
            for suggestion in audit.get("mechanism_suggestions") or [] if isinstance(suggestion, Mapping)
            for root in suggestion.get("source_root_sample_ids") or []
        }
        if _root_id(audit) in source_roots:
            continue
        values = _effect_fields(outcome)
        selected = _text(audit.get("route_selected_operator"))
        matched = bool(audit.get("suggestion_matches_existing_route"))
        rows.append({"matched": matched, "effective": values["target_hit"] and values["score_drop"] is not None and values["score_drop"] > values["volatility"] and not values["invalid"], "invalid": values["invalid"], "score_increased": values["score_drop"] is not None and values["score_drop"] < 0, "root_sample_id": _root_id(audit), "selected_operator": selected})
    total = len(rows)
    return {
        "record_type": "mechanism_route_replay_report", "mechanism_route_audit_version": ROUTE_AUDIT_VERSION,
        "frozen_evaluation_config": frozen, "evaluated_holdout_roots": sorted({row["root_sample_id"] for row in rows}), "sample_count": total,
        "route_agreement_rate": sum(row["matched"] for row in rows) / total if total else 0.0,
        "matched_effective_rate": sum(row["matched"] and row["effective"] for row in rows) / sum(row["matched"] for row in rows) if any(row["matched"] for row in rows) else 0.0,
        "invalid_generation_rate": sum(row["invalid"] for row in rows) / total if total else 0.0,
        "score_increased_rate": sum(row["score_increased"] for row in rows) / total if total else 0.0,
        "action_limit": "Replay report only; its metrics do not alter the frozen router output.", "created_at": _now(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="22B and 25B/22C-4 mechanism governance sidecars")
    sub = parser.add_subparsers(dest="command", required=True)
    induce = sub.add_parser("induce")
    induce.add_argument("--input", required=True)
    induce.add_argument("--output", required=True)
    induce.add_argument("--publish-facts-output", required=True)
    induce.add_argument("--rejections-output", required=True)
    induce.add_argument("--source-input")
    induce.add_argument("--manual-reviews")
    induce.add_argument("--min-independent-roots", type=int, default=2)
    validate = sub.add_parser("validate")
    validate.add_argument("--candidates", required=True)
    validate.add_argument("--effects", required=True)
    validate.add_argument("--frozen-config", required=True)
    validate.add_argument("--output", required=True)
    validate.add_argument("--matrix-output", required=True)
    validate.add_argument("--publish-facts-output", required=True)
    validate.add_argument("--report-output", required=True)
    validate.add_argument("--manual-reviews")
    validate.add_argument("--min-holdout-samples", type=int, default=2)
    audit = sub.add_parser("route-audit")
    audit.add_argument("--routes", required=True)
    audit.add_argument("--candidates", required=True)
    audit.add_argument("--validations", required=True)
    audit.add_argument("--project-root", default=".")
    audit.add_argument("--memory-snapshot-id", required=True)
    audit.add_argument("--output", required=True)
    audit.add_argument("--mode", choices=["audit", "limited"], default="audit")
    audit.add_argument("--approval")
    audit.add_argument("--rollback", action="store_true", help="Record an explicit rollback to audit-only behavior; never changes the frozen route.")
    replay = sub.add_parser("route-replay")
    replay.add_argument("--audits", required=True)
    replay.add_argument("--outcomes", required=True)
    replay.add_argument("--frozen-config", required=True)
    replay.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "induce":
        analyses = _read_records(args.input)
        sources = _read_records(args.source_input) if args.source_input else []
        reviews = _read_records(args.manual_reviews) if args.manual_reviews else []
        candidates, rejected = induce_candidates(analyses, analysis_path=args.input, source_records=sources, reviews=reviews, min_independent_roots=max(2, args.min_independent_roots))
        _atomic_write(args.output, candidates)
        _atomic_write(args.publish_facts_output, publish_facts(candidates))
        _atomic_write(args.rejections_output, rejected)
    elif args.command == "validate":
        candidates, effects = _read_records(args.candidates), _read_records(args.effects)
        config = _read_records(args.frozen_config)[0]
        reviews = _read_records(args.manual_reviews) if args.manual_reviews else []
        validations, matrix, report = validate_effects(candidates, effects, config=config, reviews=reviews, min_holdout_samples=max(1, args.min_holdout_samples))
        _atomic_write(args.output, validations)
        _atomic_write(args.matrix_output, matrix)
        _atomic_write(args.publish_facts_output, publish_facts(candidates, qualification={_text(row.get("mechanism_id")): row for row in validations}))
        _write_json(args.report_output, report)
    elif args.command == "route-audit":
        approval = _read_records(args.approval)[0] if args.approval else None
        output = route_audit(_read_records(args.routes), _read_records(args.candidates), _read_records(args.validations), project_root=args.project_root, snapshot_id=args.memory_snapshot_id, mode=args.mode, approval=approval, rollback=args.rollback)
        _atomic_write(args.output, output)
    else:
        report = route_replay(_read_records(args.audits), _read_records(args.outcomes), frozen_config=_read_records(args.frozen_config)[0])
        _write_json(args.output, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
