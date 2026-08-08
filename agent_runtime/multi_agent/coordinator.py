"""The coordinator only distributes, collects, and merges advisory work."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .advice_merge import merge_advice
from .advisor_dispatcher import dependent_advisors, select_advisors
from .advisor_executor import AdvisorExecutor
from .evidence_pack import build_evidence_pack
from .human_review_advisors import synthesize_prechecks
from ..skills import load_stage_skills


def run_post_experiment_review(
    run_dir: str | Path,
    *,
    task: Mapping[str, Any],
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail open: advisor failures never change primary experiment artifacts."""

    skill_load = load_stage_skills(
        "post_experiment_review",
        requested_context_layers=(
            "task_context",
            "memory_context_summary",
            "dynamic_tail.observation_summary",
            "dynamic_tail.event_refs",
            "artifact_refs",
        ),
        available_inputs=("experiment_summary", "branch_results", "effect_analysis", "memory_summary", "operator_id", "candidate_question", "parent_question", "validation_result", "score_change"),
        event_path=Path(run_dir) / "agent_events.jsonl",
    )
    evidence_pack = build_evidence_pack(run_dir, task=task, state=state, observation=observation, plan=plan)
    specs = select_advisors("post_experiment_review", observation)
    executor = AdvisorExecutor(run_dir, parent_run_id=str(state.get("agent_run_id") or Path(run_dir).name))
    records, advice_items = executor.execute(specs, evidence_pack)
    merged = merge_advice(run_dir, advice_items=advice_items, evidence_pack=evidence_pack)
    return {"evidence_pack_hash": evidence_pack["evidence_pack_hash"], "advisor_records": records, "merge": merged, "skill_load": skill_load.as_dict()}


def run_advisor_stage(
    run_dir: str | Path,
    *,
    stage: str,
    task: Mapping[str, Any],
    state: Mapping[str, Any],
    plan: Mapping[str, Any],
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Run optional advisory stages with the plan's required dependency order."""

    skill_load = None
    if stage == "memory_compilation":
        skill_load = load_stage_skills(
            "memory_compilation",
            requested_context_layers=("memory_context_summary", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"),
            available_inputs=("local_memory", "failure_memory", "invalid_generation_cases", "effect_analysis", "branch_results"),
            event_path=Path(run_dir) / "agent_events.jsonl",
        )
    elif stage == "human_review_precheck":
        skill_load = load_stage_skills(
            "human_review_precheck",
            requested_context_layers=("task_context", "dynamic_tail.observation_summary", "dynamic_tail.event_refs", "artifact_refs"),
            available_inputs=("candidate_question", "parent_question", "score_result", "validation_result", "mechanism_analysis"),
            event_path=Path(run_dir) / "agent_events.jsonl",
        )
    evidence_pack = build_evidence_pack(run_dir, task=task, state=state, observation=observation, plan=plan)
    executor = AdvisorExecutor(run_dir, parent_run_id=str(state.get("agent_run_id") or Path(run_dir).name), max_concurrency=2 if stage == "memory_compilation" else 4)
    records: list[dict[str, Any]] = []
    advice_items: list[dict[str, Any]] = []
    specs = select_advisors(stage, observation)
    if stage == "memory_compilation":
        # Facts/classification precede induction; conflict review precedes the
        # publication precheck.  Each call keeps independent advisor context.
        ordered = ("fact_extraction", "classification_mapping", "strategy_induction", "conflict_review", "publication_precheck")
        by_id = {spec.advisor_id: spec for spec in specs}
        groups = [[by_id[item]] for item in ordered if item in by_id]
    else:
        groups = [specs]
    upstream_summary = ""
    for group in groups:
        batch_records, batch_advice = executor.execute(group, evidence_pack, dynamic_instruction=upstream_summary)
        records.extend(batch_records)
        advice_items.extend(batch_advice)
        upstream_summary = "Previous advisory summaries: " + "; ".join(str(item.get("summary") or "")[:500] for item in batch_advice)
    for dependent in dependent_advisors(stage):
        synthesis_executor = AdvisorExecutor(
            run_dir,
            parent_run_id=str(state.get("agent_run_id") or Path(run_dir).name),
            handler=lambda spec, context, selection: synthesize_prechecks(advice_items, context),
        )
        batch_records, batch_advice = synthesis_executor.execute([dependent], evidence_pack, dynamic_instruction=upstream_summary)
        records.extend(batch_records)
        advice_items.extend(batch_advice)
    merged = merge_advice(run_dir, advice_items=advice_items, evidence_pack=evidence_pack)
    result = {"evidence_pack_hash": evidence_pack["evidence_pack_hash"], "advisor_records": records, "merge": merged}
    if skill_load is not None:
        result["skill_load"] = skill_load.as_dict()
    return result
