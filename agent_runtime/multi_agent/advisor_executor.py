"""Bounded concurrent execution for advisory-only tasks."""

from __future__ import annotations

import concurrent.futures
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .advisor_context import build_advisor_context
from .advisor_events import append_advisor_event
from .advisor_registry import AdvisorSpec
from .advisor_state import now, write_run_record
from .memory_advisors import memory_advice
from .model_router import ModelSelection, select_model
from .planning_advisors import planning_advice
from .review_advisors import review_advice
from .human_review_advisors import human_review_advice
from .evidence_pack import stable_hash
from .advisor_model_client import request_model_advice

AdvisorHandler = Callable[[AdvisorSpec, Mapping[str, Any], ModelSelection], Mapping[str, Any]]


def _default_handler(spec: AdvisorSpec, context: Mapping[str, Any], selection: ModelSelection) -> Mapping[str, Any]:
    model_result = request_model_advice(spec, context, selection)
    if model_result is not None:
        return model_result
    if spec.stage == "post_experiment_review":
        return review_advice(spec.advisor_id, context)
    if spec.stage == "memory_compilation":
        return memory_advice(spec.advisor_id, context)
    if spec.stage == "plan_candidates":
        return planning_advice(spec.advisor_id, context)
    return human_review_advice(spec.advisor_id, context)


class AdvisorExecutor:
    def __init__(self, run_dir: str | Path, *, parent_run_id: str, handler: AdvisorHandler | None = None, max_concurrency: int = 4, models: Mapping[str, str] | None = None) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be positive")
        self.run_dir = Path(run_dir)
        self.parent_run_id = parent_run_id
        self.handler = handler or _default_handler
        self.max_concurrency = max_concurrency
        self.models = models

    def _output_dir(self, spec: AdvisorSpec) -> Path:
        folder = {"post_experiment_review": "advice", "memory_compilation": "memory_drafts", "plan_candidates": "plan_candidates", "human_review_precheck": "review_precheck"}[spec.stage]
        path = self.run_dir / "multi_agent" / folder / spec.advisor_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run_one(self, spec: AdvisorSpec, evidence_pack: Mapping[str, Any], *, dynamic_instruction: str, parent_advisor_task_id: str | None, mode: str) -> tuple[dict[str, Any], dict[str, Any]]:
        task_id = "adv_" + uuid.uuid4().hex[:16]
        started = now()
        selection = select_model(spec.model_tier, spec.fallback_model_tier, models=self.models)
        if self.models is None and selection.selected_model != "local-deterministic-advisor" and not (os.getenv("ADVISOR_BASE_URL", "").strip() and os.getenv("ADVISOR_API_KEY", "").strip()):
            selection = ModelSelection(spec.model_tier, "local-deterministic-advisor", False)
        try:
            context = build_advisor_context(spec, evidence_pack, dynamic_instruction=dynamic_instruction, parent_advisor_task_id=parent_advisor_task_id, mode=mode)
        except Exception as exc:
            record = {"advisor_task_id": task_id, "parent_run_id": self.parent_run_id, "advisor_id": spec.advisor_id, "status": "rejected_by_policy", **selection.as_dict(), "input_hash": "sha256:", "output_hash": "", "context_cache_key": "sha256:", "started_at": started, "ended_at": now(), "evidence_refs": [], "error_summary": str(exc), "parent_advisor_task_id": parent_advisor_task_id}
            return record, {"advisor_id": spec.advisor_id, "status": "rejected_by_policy", "summary": "Advisor context rejected by policy.", "findings": [], "forbidden_actions_requested": [], "input_hash": evidence_pack.get("evidence_pack_hash"), "snapshot_ids": evidence_pack.get("snapshot_ids", {})}
        output_dir = self._output_dir(spec)
        (output_dir / "advisor_input.json").write_text(json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        append_advisor_event(self.run_dir, "advisor_started", {"advisor_task_id": task_id, "advisor_id": spec.advisor_id, "input_hash": context["input_hash"], "context_cache_key": context["context_cache_key"]})
        try:
            last_error: Exception | None = None
            raw: dict[str, Any] | None = None
            for _attempt in range(spec.retry_count + 1):
                try:
                    raw = dict(self.handler(spec, context, selection))
                    break
                except Exception as exc:  # bounded retry for transient advisor/model failures
                    last_error = exc
            if raw is None:
                assert last_error is not None
                raise last_error
            requested_tools = [str(item) for item in raw.get("requested_tools") or []]
            denied_tools = [item for item in requested_tools if item not in spec.allowed_tools]
            advice = {"advisor_id": spec.advisor_id, "status": "completed", "summary": str(raw.get("summary") or ""), "findings": list(raw.get("findings") or []), "forbidden_actions_requested": list(raw.get("forbidden_actions_requested") or []) + denied_tools, "input_hash": evidence_pack.get("evidence_pack_hash"), "snapshot_ids": dict(evidence_pack.get("snapshot_ids") or {})}
            (output_dir / "advisor_output.json").write_text(json.dumps(advice, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            record = {"advisor_task_id": task_id, "parent_run_id": self.parent_run_id, "advisor_id": spec.advisor_id, "status": "completed", **selection.as_dict(), "input_hash": context["input_hash"], "output_hash": stable_hash(advice), "context_cache_key": context["context_cache_key"], "started_at": started, "ended_at": now(), "evidence_refs": list(context["evidence_pack_slice"].get("evidence_refs") or []), "error_summary": "", "parent_advisor_task_id": parent_advisor_task_id}
            return record, advice
        except Exception as exc:
            advice = {"advisor_id": spec.advisor_id, "status": "failed", "summary": "Advisor execution failed.", "findings": [], "forbidden_actions_requested": [], "input_hash": evidence_pack.get("evidence_pack_hash"), "snapshot_ids": dict(evidence_pack.get("snapshot_ids") or {})}
            record = {"advisor_task_id": task_id, "parent_run_id": self.parent_run_id, "advisor_id": spec.advisor_id, "status": "failed", **selection.as_dict(), "input_hash": context["input_hash"], "output_hash": "", "context_cache_key": context["context_cache_key"], "started_at": started, "ended_at": now(), "evidence_refs": list(context["evidence_pack_slice"].get("evidence_refs") or []), "error_summary": str(exc)[:1000], "parent_advisor_task_id": parent_advisor_task_id}
            return record, advice

    def execute(self, specs: Sequence[AdvisorSpec], evidence_pack: Mapping[str, Any], *, dynamic_instruction: str = "", parent_advisor_task_id: str | None = None, mode: str = "spawn") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records: list[dict[str, Any]] = []
        advice_items: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures = {pool.submit(self._run_one, spec, evidence_pack, dynamic_instruction=dynamic_instruction, parent_advisor_task_id=parent_advisor_task_id, mode=mode): spec for spec in specs}
            done, pending = concurrent.futures.wait(futures, timeout=max((spec.max_runtime_seconds for spec in specs), default=1))
            for future in done:
                record, advice = future.result()
                write_run_record(self.run_dir, record)
                append_advisor_event(self.run_dir, "advisor_completed" if record["status"] == "completed" else "advisor_failed", {"advisor_task_id": record["advisor_task_id"], "advisor_id": record["advisor_id"], "status": record["status"], "error_summary": record["error_summary"]})
                records.append(record)
                advice_items.append(advice)
            for future in pending:
                spec = futures[future]
                future.cancel()
                selection = select_model(spec.model_tier, spec.fallback_model_tier, models=self.models)
                task_id = "adv_" + uuid.uuid4().hex[:16]
                record = {"advisor_task_id": task_id, "parent_run_id": self.parent_run_id, "advisor_id": spec.advisor_id, "status": "timeout", **selection.as_dict(), "input_hash": "sha256:", "output_hash": "", "context_cache_key": "sha256:", "started_at": now(), "ended_at": now(), "evidence_refs": [], "error_summary": f"exceeded {spec.max_runtime_seconds}s", "parent_advisor_task_id": parent_advisor_task_id}
                advice = {"advisor_id": spec.advisor_id, "status": "timeout", "summary": "Advisor timed out; analysis omitted.", "findings": [], "forbidden_actions_requested": [], "input_hash": evidence_pack.get("evidence_pack_hash"), "snapshot_ids": dict(evidence_pack.get("snapshot_ids") or {})}
                write_run_record(self.run_dir, record)
                append_advisor_event(self.run_dir, "advisor_timeout", {"advisor_task_id": task_id, "advisor_id": spec.advisor_id, "timeout_seconds": spec.max_runtime_seconds})
                records.append(record)
                advice_items.append(advice)
        return records, advice_items
