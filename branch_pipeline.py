"""Recoverable per-branch stage pipeline for multi-operator search.

The pipeline is intentionally business-agnostic: callers provide the existing
generation, validation, answer, rubric, decision, post-decision/Memory, and GPT
experimental handlers.  Long-lived workers and bounded queues remove global
stage barriers while shared handler instances retain the project's fair request
pools.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import statistics
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from branch_artifacts import BranchArtifactStore

Record = Dict[str, Any]
StageHandler = Callable[[Record], Awaitable[Record]]
RecordCallback = Callable[[Record], Awaitable[None]]

MAIN_STAGES = (
    "generation",
    "validation",
    "reference_answer",
    "rubric",
    "decision",
)
FORK_STAGES = ("post_decision", "experimental")
ALL_STAGES = MAIN_STAGES + FORK_STAGES


def _branch_id(record: Mapping[str, Any]) -> str:
    value = str(record.get("branch_id") or record.get("candidate_id") or "").strip()
    if not value:
        raise ValueError("branch pipeline record is missing branch_id/candidate_id")
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        delete=False,
        dir=str(path.parent),
        suffix=".tmp",
    ) as target:
        temporary = target.name
        json.dump(value, target, ensure_ascii=False, sort_keys=True)
        target.write("\n")
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


class BranchCheckpointStore:
    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def branch_key(branch_id: str) -> str:
        return hashlib.sha256(branch_id.encode("utf-8")).hexdigest()

    def path(self, branch_id: str, stage: str) -> Path:
        if stage not in {*ALL_STAGES, "decision_consumed", "final", "branch_error"}:
            raise ValueError(f"unsupported branch checkpoint stage: {stage}")
        return self.root / self.branch_key(branch_id) / f"{stage}.json"

    def write(self, branch_id: str, stage: str, record: Mapping[str, Any]) -> None:
        _atomic_json(
            self.path(branch_id, stage),
            {
                "branch_id": branch_id,
                "stage": stage,
                "published_at": time.time(),
                "record": deepcopy(dict(record)),
            },
        )

    def read(self, branch_id: str, stage: str) -> Optional[Record]:
        path = self.path(branch_id, stage)
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("branch_id") != branch_id or payload.get("stage") != stage:
            raise ValueError(f"invalid checkpoint identity: {path}")
        record = payload.get("record")
        if not isinstance(record, dict):
            raise ValueError(f"invalid checkpoint record: {path}")
        return record

    def has(self, branch_id: str, stage: str) -> bool:
        return self.path(branch_id, stage).exists()

    def retain_only(self, branch_id: str, stages: Iterable[str]) -> None:
        """Remove superseded checkpoints after a newer durable state exists."""

        keep = set(stages)
        branch_dir = self.root / self.branch_key(branch_id)
        if not branch_dir.exists():
            return
        for stage in (*ALL_STAGES, "decision_consumed", "final", "branch_error"):
            if stage in keep:
                continue
            checkpoint = self.path(branch_id, stage)
            if checkpoint.exists():
                checkpoint.unlink()

    def prune_superseded_main(self, branch_id: str, current_stage: str) -> None:
        """Keep only the latest main-stage record needed for recovery."""

        if current_stage not in MAIN_STAGES:
            raise ValueError(f"not a main checkpoint stage: {current_stage}")
        current_index = MAIN_STAGES.index(current_stage)
        for stage in MAIN_STAGES[:current_index]:
            checkpoint = self.path(branch_id, stage)
            if checkpoint.exists():
                checkpoint.unlink()

    def confirmed_stage(self, branch_id: str) -> str:
        for stage in reversed(ALL_STAGES):
            if self.has(branch_id, stage):
                return stage
        return ""


class PerformanceEventLog:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self.events: List[Record] = []
        self._lock = asyncio.Lock()

    async def append(self, event: Mapping[str, Any]) -> None:
        row = {"timestamp": time.time(), **dict(event)}
        async with self._lock:
            self.events.append(row)
            if self.path:
                destination = Path(self.path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("a", encoding="utf-8") as target:
                    target.write(json.dumps(row, ensure_ascii=False) + "\n")

    @staticmethod
    def _percentile(values: Sequence[float], percentile: float) -> Optional[float]:
        if not values:
            return None
        ordered = sorted(values)
        index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * percentile)))
        return ordered[index]

    def summary(self) -> Record:
        starts: Dict[str, float] = {}
        completed: Dict[str, float] = {}
        failed: Dict[str, float] = {}
        model_error_branches: set[str] = set()
        decisions: Dict[str, float] = {}
        boundaries = 0
        queue_waits: List[float] = []
        stage_latencies: List[float] = []
        branch_parent: Dict[str, str] = {}
        pool_utilizations: List[float] = []
        request_count = 0
        retry_count = 0
        for event in self.events:
            branch_id = str(event.get("branch_id") or "")
            event_type = event.get("event")
            timestamp = float(event.get("timestamp") or 0)
            if event_type == "branch_received":
                starts.setdefault(branch_id, timestamp)
                branch_parent[branch_id] = str(event.get("parent_node_id") or "")
            elif event_type == "decision_consumed":
                decisions[branch_id] = timestamp
                if event.get("branch_status") == "boundary_candidate":
                    boundaries += 1
            elif event_type == "branch_completed":
                completed[branch_id] = timestamp
            elif event_type == "branch_failed":
                failed[branch_id] = timestamp
                model_error_branches.add(branch_id)
            elif event_type == "fork_stage_failed":
                model_error_branches.add(branch_id)
            if event_type == "stage_completed":
                stage_latencies.append(float(event.get("stage_seconds") or 0))
                queue_waits.append(float(event.get("queue_wait_seconds") or 0))
            elif event_type == "request_pool_snapshot":
                try:
                    limit = float(event.get("limit") or 0)
                    active = float(event.get("active") or 0)
                    if limit > 0:
                        pool_utilizations.append(active / limit)
                except (TypeError, ValueError):
                    pass
            elif event_type == "model_request":
                request_count += 1
                retry_count += int(event.get("retry_count") or 0)
        branch_latencies = [
            completed[branch_id] - started
            for branch_id, started in starts.items()
            if branch_id in completed
        ]
        decision_to_full_delays = [
            completed[branch_id] - decided_at
            for branch_id, decided_at in decisions.items()
            if branch_id in completed and completed[branch_id] >= decided_at
        ]
        parent_starts: Dict[str, List[float]] = {}
        parent_completions: Dict[str, List[float]] = {}
        for branch_id, started in starts.items():
            parent = branch_parent.get(branch_id) or branch_id
            parent_starts.setdefault(parent, []).append(started)
            if branch_id in completed:
                parent_completions.setdefault(parent, []).append(completed[branch_id])
        sample_latencies = [
            max(parent_completions[parent]) - min(start_values)
            for parent, start_values in parent_starts.items()
            if parent in parent_completions
        ]
        if self.events:
            elapsed = max(
                1e-9,
                max(float(event["timestamp"]) for event in self.events)
                - min(float(event["timestamp"]) for event in self.events),
            )
        else:
            elapsed = 0.0
        first_timestamp = (
            min(float(event["timestamp"]) for event in self.events)
            if self.events
            else 0.0
        )
        search_completed_seconds = (
            max(decisions.values()) - first_timestamp if decisions else 0.0
        )
        per_hour = 3600.0 / elapsed if elapsed else 0.0
        terminal_branches = set(completed) | set(failed)
        return {
            "wall_clock_seconds": elapsed,
            "search_completed_seconds": search_completed_seconds,
            "full_experiment_completed_seconds": elapsed,
            "branches_completed": len(completed),
            "branches_completed_per_wall_clock_hour": len(completed) * per_hour,
            "decision_evaluations_completed": len(decisions),
            "decision_evaluations_completed_per_wall_clock_hour": len(decisions) * per_hour,
            "boundary_candidates": boundaries,
            "boundary_candidates_per_wall_clock_hour": boundaries * per_hour,
            "p50_branch_latency": self._percentile(branch_latencies, 0.50),
            "p95_branch_latency": self._percentile(branch_latencies, 0.95),
            "p50_sample_termination_latency": self._percentile(sample_latencies, 0.50),
            "p95_sample_termination_latency": self._percentile(sample_latencies, 0.95),
            "p50_stage_latency": self._percentile(stage_latencies, 0.50),
            "p95_stage_latency": self._percentile(stage_latencies, 0.95),
            "average_queue_wait_seconds": (
                statistics.fmean(queue_waits) if queue_waits else 0.0
            ),
            "p50_decision_to_full_experiment_delay_seconds": self._percentile(
                decision_to_full_delays,
                0.50,
            ),
            "p95_decision_to_full_experiment_delay_seconds": self._percentile(
                decision_to_full_delays,
                0.95,
            ),
            "request_pool_utilization": (
                statistics.fmean(pool_utilizations) if pool_utilizations else None
            ),
            "retry_rate": retry_count / request_count if request_count else 0.0,
            "model_error_rate": (
                len(model_error_branches) / len(terminal_branches)
                if terminal_branches
                else 0.0
            ),
        }


class BranchPipeline:
    def __init__(
        self,
        *,
        handlers: Mapping[str, StageHandler],
        checkpoint_dir: str | os.PathLike[str],
        on_decision: RecordCallback,
        on_complete: RecordCallback,
        on_error: Optional[RecordCallback] = None,
        worker_counts: Optional[Mapping[str, int]] = None,
        queue_size: int = 32,
        performance_events: Optional[str] = None,
        branch_artifacts: Optional[str] = None,
        compact_checkpoints: bool = True,
    ):
        missing = [stage for stage in ALL_STAGES if stage not in handlers]
        if missing:
            raise ValueError(f"missing branch pipeline handlers: {', '.join(missing)}")
        if queue_size < 1:
            raise ValueError("queue_size must be >= 1")
        self.handlers = dict(handlers)
        self.checkpoints = BranchCheckpointStore(checkpoint_dir)
        self.on_decision = on_decision
        self.on_complete = on_complete
        self.on_error = on_error
        self.worker_counts = {
            stage: max(1, int((worker_counts or {}).get(stage, 1)))
            for stage in ALL_STAGES
        }
        self.queues = {
            stage: asyncio.Queue(maxsize=queue_size)
            for stage in ALL_STAGES
        }
        self.events = PerformanceEventLog(performance_events)
        self.artifact_store = (
            BranchArtifactStore(branch_artifacts) if branch_artifacts else None
        )
        self.compact_checkpoints = bool(compact_checkpoints)
        self._artifact_lock = asyncio.Lock()
        self._input_records: Dict[str, Record] = {}
        self._finalized: set[str] = set()
        self._remaining = 0
        self._done = asyncio.Event()
        self._generation_conditions: Dict[str, asyncio.Condition] = {}
        self._generation_next: Dict[str, int] = {}
        self._finalize_lock = asyncio.Lock()
        self._workers: List[asyncio.Task[None]] = []
        self._started = False
        self._closed = False
        self._submitted: set[str] = set()
        self._outcomes: asyncio.Queue[Record] = asyncio.Queue()

    @staticmethod
    def _parent_id(record: Mapping[str, Any]) -> str:
        return str(record.get("parent_node_id") or "").strip()

    @staticmethod
    def _generation_sequence(record: Mapping[str, Any]) -> int:
        dispatch = record.get("search_dispatch")
        dispatch = dispatch if isinstance(dispatch, Mapping) else {}
        try:
            return max(1, int(dispatch.get("generation_sequence") or 1))
        except (TypeError, ValueError):
            return 1

    async def _run_generation_serial(
        self,
        record: Record,
        handler: StageHandler,
    ) -> Record:
        parent_id = self._parent_id(record)
        if not parent_id:
            return await handler(record)
        condition = self._generation_conditions.setdefault(
            parent_id,
            asyncio.Condition(),
        )
        sequence = self._generation_sequence(record)
        async with condition:
            await condition.wait_for(
                lambda: sequence <= self._generation_next.get(parent_id, sequence)
            )
            try:
                return await handler(record)
            finally:
                # A failed sibling is terminal for that branch, but it must not
                # permanently block every later sibling of the same parent.
                self._generation_next[parent_id] = sequence + 1
                condition.notify_all()

    async def _consume_decision(self, record: Record) -> None:
        branch_id = _branch_id(record)
        # Replaying the callback is required after a coordinator crash: the
        # persistent marker proves the old process consumed the decision, but
        # not that its aggregate state was published. Callers must therefore
        # merge idempotently.
        await self.on_decision(deepcopy(record))
        if not self.checkpoints.has(branch_id, "decision_consumed"):
            # This is only an acknowledgement marker. Persisting the complete
            # scored record here duplicated the largest checkpoint without
            # adding any recovery information.
            self.checkpoints.write(
                branch_id,
                "decision_consumed",
                {"branch_id": branch_id} if self.compact_checkpoints else record,
            )
        await self.events.append(
            {
                "event": "decision_consumed",
                "branch_id": branch_id,
                "branch_status": record.get("branch_status"),
            }
        )
        await self._outcomes.put(
            {
                "branch_id": branch_id,
                "outcome": "decision",
                "record": deepcopy(record),
            }
        )

    async def _maybe_finalize(self, branch_id: str) -> None:
        async with self._finalize_lock:
            if branch_id in self._finalized:
                return
            post = self.checkpoints.read(branch_id, "post_decision")
            experimental = self.checkpoints.read(branch_id, "experimental")
            if post is None or experimental is None:
                return
            final = deepcopy(experimental)
            for field in (
                "effect_analysis",
                "evolution_state",
                "failure_memory_candidate",
                "memory_commit",
            ):
                if field in post:
                    final[field] = deepcopy(post[field])
            await self.on_complete(final)
            if self.artifact_store is not None:
                async with self._artifact_lock:
                    self.artifact_store.append(final, "complete_branch")
            self.checkpoints.write(branch_id, "final", final)
            # final.json plus the append-only branch artifact are sufficient
            # for replay. Keeping all seven stage snapshots made completed
            # branch storage grow by a large fixed multiplier.
            if self.compact_checkpoints:
                self.checkpoints.retain_only(branch_id, {"final"})
            self._finalized.add(branch_id)
            self._remaining -= 1
            await self.events.append(
                {"event": "branch_completed", "branch_id": branch_id}
            )
            if self._remaining <= 0:
                self._done.set()

    async def _fail_branch(
        self,
        record: Record,
        stage: str,
        exc: Exception,
    ) -> None:
        branch_id = _branch_id(record)
        exception_record = getattr(exc, "record", None)
        failed = deepcopy(
            dict(exception_record)
            if isinstance(exception_record, Mapping)
            else record
        )
        branch_status = str(
            getattr(exc, "branch_status", "") or "branch_error"
        ).strip()
        failed["branch_status"] = branch_status
        failed["branch_error"] = {
            "stage": stage,
            "error": str(exc),
        }
        self.checkpoints.write(branch_id, "branch_error", failed)
        if self.compact_checkpoints:
            self.checkpoints.retain_only(branch_id, {"branch_error"})
        if self.artifact_store is not None:
            async with self._artifact_lock:
                self.artifact_store.append(failed, "branch_error")
        if self.on_error is not None:
            await self.on_error(failed)
        if branch_id not in self._finalized:
            self._finalized.add(branch_id)
            self._remaining -= 1
        await self.events.append(
            {
                "event": "branch_failed",
                "branch_id": branch_id,
                "stage": stage,
                "error_type": type(exc).__name__,
            }
        )
        await self._outcomes.put(
            {
                "branch_id": branch_id,
                "outcome": "error",
                "record": deepcopy(failed),
            }
        )
        if self._remaining <= 0:
            self._done.set()

    async def _record_fork_failure(
        self,
        record: Record,
        stage: str,
        exc: Exception,
    ) -> None:
        branch_id = _branch_id(record)
        failed = deepcopy(record)
        failed[f"{stage}_error"] = {
            "error": str(exc),
            "error_type": type(exc).__name__,
        }
        if stage == "experimental":
            failed["experimental_evaluation_status"] = "failed"
        else:
            failed["post_decision_status"] = "failed"
        self.checkpoints.write(branch_id, stage, failed)
        await self.events.append(
            {
                "event": "fork_stage_failed",
                "branch_id": branch_id,
                "stage": stage,
                "error_type": type(exc).__name__,
            }
        )
        await self._maybe_finalize(branch_id)

    async def _worker(self, stage: str) -> None:
        queue = self.queues[stage]
        handler = self.handlers[stage]
        while True:
            queued_at, record = await queue.get()
            if record is None:
                queue.task_done()
                return
            branch_id = _branch_id(record)
            started = time.time()
            await self.events.append(
                {
                    "event": "stage_started",
                    "branch_id": branch_id,
                    "stage": stage,
                }
            )
            try:
                if stage == "generation":
                    result = await self._run_generation_serial(record, handler)
                else:
                    result = await handler(record)
                if _branch_id(result) != branch_id:
                    raise ValueError(
                        f"stage {stage} changed branch identity {branch_id}"
                    )
                self.checkpoints.write(branch_id, stage, result)
                if self.compact_checkpoints and stage in MAIN_STAGES:
                    # The new checkpoint is atomically durable before older
                    # main-stage snapshots are removed, so crash recovery
                    # always has at least the latest confirmed record.
                    self.checkpoints.prune_superseded_main(branch_id, stage)
                finished = time.time()
                await self.events.append(
                    {
                        "event": "stage_completed",
                        "branch_id": branch_id,
                        "stage": stage,
                        "queue_wait_seconds": max(0.0, started - queued_at),
                        "stage_seconds": max(0.0, finished - started),
                    }
                )
                if stage in MAIN_STAGES[:-1]:
                    next_stage = MAIN_STAGES[MAIN_STAGES.index(stage) + 1]
                    await self.queues[next_stage].put((time.time(), result))
                elif stage == "decision":
                    await self._consume_decision(result)
                    for fork_stage in FORK_STAGES:
                        if self.checkpoints.has(branch_id, fork_stage):
                            continue
                        await self.queues[fork_stage].put((time.time(), deepcopy(result)))
                else:
                    await self._maybe_finalize(branch_id)
            except Exception as exc:
                if stage in FORK_STAGES:
                    await self._record_fork_failure(record, stage, exc)
                else:
                    await self._fail_branch(record, stage, exc)
            finally:
                queue.task_done()

    def _latest_main_checkpoint(self, branch_id: str) -> tuple[str, Optional[Record]]:
        for stage in reversed(MAIN_STAGES):
            record = self.checkpoints.read(branch_id, stage)
            if record is not None:
                return stage, record
        return "", None

    async def _resume_record(self, record: Record) -> None:
        branch_id = _branch_id(record)
        await self.events.append(
            {
                "event": "branch_received",
                "branch_id": branch_id,
                "parent_node_id": self._parent_id(record),
            }
        )
        if self.checkpoints.has(branch_id, "final"):
            final = self.checkpoints.read(branch_id, "final") or record
            decision = self.checkpoints.read(branch_id, "decision") or final
            await self._consume_decision(decision)
            await self.on_complete(deepcopy(final))
            if self.compact_checkpoints:
                self.checkpoints.retain_only(branch_id, {"final"})
            self._finalized.add(branch_id)
            self._remaining -= 1
            await self._outcomes.put(
                {
                    "branch_id": branch_id,
                    "outcome": "final",
                    "record": final,
                }
            )
            return
        if self.checkpoints.has(branch_id, "branch_error"):
            failed = self.checkpoints.read(branch_id, "branch_error") or record
            if self.on_error is not None:
                await self.on_error(deepcopy(failed))
            self._finalized.add(branch_id)
            self._remaining -= 1
            await self._outcomes.put(
                {
                    "branch_id": branch_id,
                    "outcome": "error",
                    "record": failed,
                }
            )
            return
        latest_stage, latest = self._latest_main_checkpoint(branch_id)
        if latest_stage == "decision" and latest is not None:
            await self._consume_decision(latest)
            for fork_stage in FORK_STAGES:
                if not self.checkpoints.has(branch_id, fork_stage):
                    await self.queues[fork_stage].put((time.time(), deepcopy(latest)))
            await self._maybe_finalize(branch_id)
            return
        if latest_stage:
            next_stage = MAIN_STAGES[MAIN_STAGES.index(latest_stage) + 1]
            await self.queues[next_stage].put((time.time(), latest))
        else:
            await self.queues["generation"].put((time.time(), record))

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("branch pipeline is already closed")
        if self._started:
            return
        self._workers = [
            asyncio.create_task(self._worker(stage))
            for stage in ALL_STAGES
            for _ in range(self.worker_counts[stage])
        ]
        self._started = True

    async def submit(self, records: Iterable[Record]) -> List[str]:
        if not self._started:
            await self.start()
        if self._closed:
            raise RuntimeError("cannot submit to a closed branch pipeline")
        rows = [deepcopy(record) for record in records]
        branch_ids = [_branch_id(record) for record in rows]
        if len(branch_ids) != len(set(branch_ids)):
            raise ValueError("duplicate branch_id in pipeline input")
        repeated = [branch_id for branch_id in branch_ids if branch_id in self._submitted]
        if repeated:
            raise ValueError(f"branch already submitted: {repeated[0]}")
        self._submitted.update(branch_ids)
        self._input_records.update(zip(branch_ids, rows))
        self._remaining += len(rows)
        if not rows:
            return []
        self._done.clear()

        pending_generation_by_parent: Dict[str, List[int]] = {}
        for record in rows:
            branch_id = _branch_id(record)
            if not self.checkpoints.has(branch_id, "generation"):
                pending_generation_by_parent.setdefault(
                    self._parent_id(record),
                    [],
                ).append(self._generation_sequence(record))
        for parent_id, sequences in pending_generation_by_parent.items():
            if not parent_id or not sequences:
                continue
            next_sequence = min(sequences)
            self._generation_next[parent_id] = min(
                self._generation_next.get(parent_id, next_sequence),
                next_sequence,
            )
        for record in rows:
            await self._resume_record(record)
        if self._remaining <= 0:
            self._done.set()
        return branch_ids

    async def wait_for_outcome(self) -> Record:
        if not self._started:
            raise RuntimeError("branch pipeline has not started")
        return await self._outcomes.get()

    async def finish(self) -> Record:
        if not self._started:
            await self.start()
        if self._remaining <= 0:
            self._done.set()
        try:
            await self._done.wait()
            for queue in self.queues.values():
                await queue.join()
        finally:
            for stage, queue in self.queues.items():
                for _ in range(self.worker_counts[stage]):
                    await queue.put((time.time(), None))
            await asyncio.gather(*self._workers)
            self._closed = True
        return self.events.summary()

    async def run(self, records: Iterable[Record]) -> Record:
        await self.start()
        await self.submit(records)
        return await self.finish()
