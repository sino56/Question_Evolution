"""Bounded concurrent execution for advisory-only tasks."""

from __future__ import annotations

import concurrent.futures
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .advisor_context import build_advisor_context
from .advisor_events import append_advisor_event
from .advisor_registry import AdvisorSpec
from .advisor_state import now, write_run_record
from .memory_advisors import memory_advice
from .model_router import DETERMINISTIC_ADVISOR_MODEL, ModelSelection, select_model
from .review_advisors import review_advice
from .human_review_advisors import human_review_advice
from .evidence_pack import stable_hash
from .advisor_model_client import request_model_advice
from ..skills import load_stage_skills

AdvisorHandler = Callable[[AdvisorSpec, Mapping[str, Any], ModelSelection], Mapping[str, Any]]


def _credentials_present() -> bool:
    """Return True only when an advisor provider endpoint is fully configured."""

    return bool(os.getenv("ADVISOR_BASE_URL", "").strip() and os.getenv("ADVISOR_API_KEY", "").strip())


def _resolved_selection(spec: AdvisorSpec, models: Mapping[str, str] | None) -> ModelSelection:
    """Resolve the model selection with the realised credentials applied.

    ``request_model_advice`` returns ``None`` without provider credentials, so a
    selection that names an external model would be a label lie: the record
    would claim model-backed advice that actually came from the deterministic
    template (report V-8).  The correction below is applied regardless of
    whether a ``models`` mapping was injected into the executor.
    """

    selection = select_model(spec.model_tier, spec.fallback_model_tier, models=models)
    if str(selection.selected_model) != DETERMINISTIC_ADVISOR_MODEL and not _credentials_present():
        return ModelSelection(spec.model_tier, DETERMINISTIC_ADVISOR_MODEL, False)
    return selection


def _default_handler(spec: AdvisorSpec, context: Mapping[str, Any], selection: ModelSelection) -> Mapping[str, Any]:
    model_result = request_model_advice(spec, context, selection)
    if model_result is not None:
        model_result["_model_backed"] = True
        return model_result
    if spec.stage == "post_experiment_review":
        advice = review_advice(spec.advisor_id, context)
    elif spec.stage == "memory_compilation":
        advice = memory_advice(spec.advisor_id, context)
    else:
        advice = human_review_advice(spec.advisor_id, context)
    advice["_model_backed"] = False
    return advice


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
        folder = {"post_experiment_review": "advice", "memory_compilation": "memory_drafts", "human_review_precheck": "review_precheck"}[spec.stage]
        path = self.run_dir / "multi_agent" / folder / spec.advisor_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run_one(
        self,
        spec: AdvisorSpec,
        evidence_pack: Mapping[str, Any],
        *,
        dynamic_instruction: str,
        parent_advisor_task_id: str | None,
        mode: str,
        task_id: str,
        cancel_event: threading.Event,
        started_at: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        # ``task_id`` is minted by ``execute`` so the started event, the run
        # record, and any timeout record all reference the same advisor task
        # (the audit chain used to break across the timeout path).
        selection = _resolved_selection(spec, self.models)
        try:
            context = build_advisor_context(spec, evidence_pack, dynamic_instruction=dynamic_instruction, parent_advisor_task_id=parent_advisor_task_id, mode=mode)
        except Exception as exc:
            record = {"advisor_task_id": task_id, "parent_run_id": self.parent_run_id, "advisor_id": spec.advisor_id, "status": "rejected_by_policy", **selection.as_dict(), "input_hash": "sha256:", "output_hash": "", "context_cache_key": "sha256:", "started_at": started_at, "ended_at": now(), "evidence_refs": [], "error_summary": str(exc), "parent_advisor_task_id": parent_advisor_task_id}
            return record, {"advisor_id": spec.advisor_id, "status": "rejected_by_policy", "summary": "Advisor context rejected by policy.", "findings": [], "forbidden_actions_requested": [], "input_hash": evidence_pack.get("evidence_pack_hash"), "snapshot_ids": evidence_pack.get("snapshot_ids", {})}
        load_stage_skills(
            "multi_agent_advice",
            requested_context_layers=("advisor_spec_context", "evidence_pack_slice", "advisor_dynamic_instruction", "artifact_refs"),
            available_inputs=("advisor_spec_context", "evidence_pack_slice", "allowed_tools", "output_schema"),
            event_path=self.run_dir / "multi_agent" / "advisor_events.jsonl",
        )
        output_dir = self._output_dir(spec)
        (output_dir / "advisor_input.json").write_text(json.dumps(context, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        append_advisor_event(self.run_dir, "advisor_started", {"advisor_task_id": task_id, "advisor_id": spec.advisor_id, "input_hash": context["input_hash"], "context_cache_key": context["context_cache_key"]})
        try:
            last_error: Exception | None = None
            raw: dict[str, Any] | None = None
            for _attempt in range(spec.retry_count + 1):
                if cancel_event.is_set():
                    raise TimeoutError(f"cancelled after {spec.max_runtime_seconds}s")
                try:
                    raw = dict(self.handler(spec, context, selection))
                    break
                except Exception as exc:  # bounded retry for transient advisor/model failures
                    last_error = exc
            if raw is None:
                assert last_error is not None
                raise last_error
            if cancel_event.is_set():
                # The deadline already expired and ``execute`` recorded a
                # timeout for this task_id; publishing a late completed output
                # would contradict that audit record (report: timeout integrity).
                append_advisor_event(self.run_dir, "advisor_output_suppressed", {"advisor_task_id": task_id, "advisor_id": spec.advisor_id, "timeout_seconds": spec.max_runtime_seconds})
                raise TimeoutError(f"exceeded {spec.max_runtime_seconds}s")
            model_backed = bool(raw.pop("_model_backed", False))
            if not model_backed and str(selection.selected_model) != DETERMINISTIC_ADVISOR_MODEL:
                # A custom or synthesis handler that never called the model
                # must not be reported under an external model name.
                selection = ModelSelection(spec.model_tier, DETERMINISTIC_ADVISOR_MODEL, bool(selection.fallback_used))
            requested_tools = [str(item) for item in raw.get("requested_tools") or []]
            denied_tools = [item for item in requested_tools if item not in spec.allowed_tools]
            advice = {"advisor_id": spec.advisor_id, "status": "completed", "summary": str(raw.get("summary") or ""), "findings": list(raw.get("findings") or []), "forbidden_actions_requested": list(raw.get("forbidden_actions_requested") or []) + denied_tools, "input_hash": evidence_pack.get("evidence_pack_hash"), "snapshot_ids": dict(evidence_pack.get("snapshot_ids") or {})}
            (output_dir / "advisor_output.json").write_text(json.dumps(advice, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            record = {"advisor_task_id": task_id, "parent_run_id": self.parent_run_id, "advisor_id": spec.advisor_id, "status": "completed", **selection.as_dict(), "input_hash": context["input_hash"], "output_hash": stable_hash(advice), "context_cache_key": context["context_cache_key"], "started_at": started_at, "ended_at": now(), "evidence_refs": list(context["evidence_pack_slice"].get("evidence_refs") or []), "error_summary": "", "parent_advisor_task_id": parent_advisor_task_id}
            return record, advice
        except Exception as exc:
            advice = {"advisor_id": spec.advisor_id, "status": "failed", "summary": "Advisor execution failed.", "findings": [], "forbidden_actions_requested": [], "input_hash": evidence_pack.get("evidence_pack_hash"), "snapshot_ids": dict(evidence_pack.get("snapshot_ids") or {})}
            record = {"advisor_task_id": task_id, "parent_run_id": self.parent_run_id, "advisor_id": spec.advisor_id, "status": "failed", **selection.as_dict(), "input_hash": context["input_hash"], "output_hash": "", "context_cache_key": context["context_cache_key"], "started_at": started_at, "ended_at": now(), "evidence_refs": list(context["evidence_pack_slice"].get("evidence_refs") or []), "error_summary": str(exc)[:1000], "parent_advisor_task_id": parent_advisor_task_id}
            return record, advice

    def execute(self, specs: Sequence[AdvisorSpec], evidence_pack: Mapping[str, Any], *, dynamic_instruction: str = "", parent_advisor_task_id: str | None = None, mode: str = "spawn") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        records: list[dict[str, Any]] = []
        advice_items: list[dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_concurrency) as pool:
            futures: dict[concurrent.futures.Future, tuple[AdvisorSpec, str, threading.Event, float]] = {}
            for spec in specs:
                task_id = "adv_" + uuid.uuid4().hex[:16]
                cancel_event = threading.Event()
                started_at = now()
                future = pool.submit(
                    self._run_one, spec, evidence_pack,
                    dynamic_instruction=dynamic_instruction, parent_advisor_task_id=parent_advisor_task_id,
                    mode=mode, task_id=task_id, cancel_event=cancel_event, started_at=started_at,
                )
                futures[future] = (spec, task_id, cancel_event, time.monotonic() + max(0.001, float(spec.max_runtime_seconds)))
            pending = set(futures)
            # Per-advisor deadlines: each spec owns its own budget instead of
            # the whole batch waiting on the largest ``max_runtime_seconds``.
            while pending:
                nearest_deadline = min(futures[future][3] for future in pending)
                done, _ = concurrent.futures.wait(
                    pending, timeout=max(0.0, nearest_deadline - time.monotonic()),
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                for future in done:
                    pending.discard(future)
                    spec, _task_id, _cancel, _deadline = futures[future]
                    try:
                        record, advice = future.result()
                    except Exception as exc:
                        record = {"advisor_task_id": "adv_" + uuid.uuid4().hex[:16], "parent_run_id": self.parent_run_id, "advisor_id": spec.advisor_id, "status": "failed", "model_tier": spec.model_tier, "selected_model": DETERMINISTIC_ADVISOR_MODEL, "fallback_used": False, "input_hash": "sha256:", "output_hash": "", "context_cache_key": "sha256:", "started_at": now(), "ended_at": now(), "evidence_refs": [], "error_summary": str(exc)[:1000], "parent_advisor_task_id": parent_advisor_task_id}
                        advice = {"advisor_id": spec.advisor_id, "status": "failed", "summary": "Advisor execution failed.", "findings": [], "forbidden_actions_requested": [], "input_hash": evidence_pack.get("evidence_pack_hash"), "snapshot_ids": dict(evidence_pack.get("snapshot_ids") or {})}
                    write_run_record(self.run_dir, record)
                    append_advisor_event(self.run_dir, "advisor_completed" if record["status"] == "completed" else "advisor_failed", {"advisor_task_id": record["advisor_task_id"], "advisor_id": record["advisor_id"], "status": record["status"], "error_summary": record["error_summary"]})
                    records.append(record)
                    advice_items.append(advice)
                now_monotonic = time.monotonic()
                for future in [item for item in pending if now_monotonic >= futures[item][3]]:
                    pending.discard(future)
                    spec, task_id, cancel_event, _deadline = futures[future]
                    future.cancel()
                    # The worker checks this event before publishing its output,
                    # so a late finish can no longer contradict the timeout.
                    cancel_event.set()
                    selection = _resolved_selection(spec, self.models)
                    record = {"advisor_task_id": task_id, "parent_run_id": self.parent_run_id, "advisor_id": spec.advisor_id, "status": "timeout", **selection.as_dict(), "input_hash": "sha256:", "output_hash": "", "context_cache_key": "sha256:", "started_at": now(), "ended_at": now(), "evidence_refs": [], "error_summary": f"exceeded {spec.max_runtime_seconds}s", "parent_advisor_task_id": parent_advisor_task_id}
                    advice = {"advisor_id": spec.advisor_id, "status": "timeout", "summary": "Advisor timed out; analysis omitted.", "findings": [], "forbidden_actions_requested": [], "input_hash": evidence_pack.get("evidence_pack_hash"), "snapshot_ids": dict(evidence_pack.get("snapshot_ids") or {})}
                    write_run_record(self.run_dir, record)
                    append_advisor_event(self.run_dir, "advisor_timeout", {"advisor_task_id": task_id, "advisor_id": spec.advisor_id, "timeout_seconds": spec.max_runtime_seconds})
                    records.append(record)
                    advice_items.append(advice)
        return records, advice_items
