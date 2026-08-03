"""Production runner for breadth-first vertical operator stacking.

Each frontier node is routed again and executed through the existing
``MultiOperatorSearchRunner``.  The vertical layer only coordinates parent
selection, path-scoped operator plans, depth/global termination, and normalized
append-only evidence artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from contextlib import contextmanager
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from branch_artifacts import BranchArtifactStore
from multi_operator_search import MultiOperatorSearchRunner, _python_stage, _run
from operator_router import load_jsonl_if_exists, route_records
from pipeline_runtime import (
    REQUEST_BUDGET_PATH_ENV,
    initialize_request_budget,
    load_json_records,
    publish_records,
    request_budget_usage,
    sha256_file,
)
from search_coordinator import (
    _write_jsonl_atomic,
    initialize_search_state,
    mark_branch_terminal,
    merge_decision_result,
    upgrade_search_state,
)
from vertical_artifacts import VerticalArtifactStore
from vertical_search import (
    SYSTEM_TERMINATION_REASONS,
    attach_vertical_node,
    build_boundary_edge,
    build_boundary_path,
    build_child_node,
    build_operator_attempt,
    build_root_node,
    build_vertical_operator_plan,
    claim_next_frontier,
    complete_frontier,
    initialize_vertical_search_state,
    input_record_sha256,
    mark_system_termination,
    normalized_prompt_hash,
    reconcile_vertical_boundary_counts,
    sample_identity,
    upgrade_vertical_search_state,
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _artifact_records(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    store = BranchArtifactStore(path)
    return list(store.iter_rows())


def _node_metadata(record: Mapping[str, Any]) -> Mapping[str, Any]:
    node = record.get("vertical_node")
    return node if isinstance(node, Mapping) else record


class VerticalOperatorSearchRunner:
    def __init__(
        self,
        *,
        project_dir: Path,
        work_dir: Path,
        memory_dir: Path,
        branch_window: int,
        boundary_target: Optional[int],
        max_depth: int,
        allow_operator_repeat_in_path: bool,
        pipeline_mode: str,
        max_iterations: int,
        rule_only_difficulty: bool,
        defer_gpt_experimental_evaluation: bool,
        artifact_retention: str,
        max_request_attempts_per_sample: int = 0,
        max_evaluations_per_sample: int = 0,
        sample_timeout_seconds: float = 0.0,
        single_operator_boundary_target: Optional[int] = None,
        stacked_operator_boundary_target: Optional[int] = None,
        total_boundary_hard_cap: Optional[int] = None,
        routing_mode: str = "",
        router_model: str = "",
        router_base_url: str = "",
        router_timeout_seconds: float = 60.0,
        router_retries: int = 0,
        router_concurrency: int = 20,
        router_cache: str = "",
        profile_model: str = "",
        profile_base_url: str = "",
        profile_concurrency: int = 5,
    ):
        if max_depth not in {2, 3}:
            raise ValueError("max_depth must be 2 or 3")
        if branch_window < 1:
            raise ValueError("branch_window must be >= 1")
        if pipeline_mode not in {"step", "stream"}:
            raise ValueError("pipeline_mode must be step or stream")
        self.project_dir = project_dir
        self.work_dir = work_dir
        self.memory_dir = memory_dir
        self.branch_window = branch_window
        legacy_target = int(boundary_target or 5)
        self.single_operator_boundary_target = int(
            single_operator_boundary_target
            if single_operator_boundary_target is not None
            else legacy_target
        )
        self.stacked_operator_boundary_target = (
            0
            if max_depth == 2
            else int(
                stacked_operator_boundary_target
                if stacked_operator_boundary_target is not None
                else legacy_target
            )
        )
        if self.single_operator_boundary_target < 1:
            raise ValueError("single_operator_boundary_target must be >= 1")
        if self.stacked_operator_boundary_target < 0:
            raise ValueError("stacked_operator_boundary_target must be >= 0")
        if max_depth == 2 and stacked_operator_boundary_target not in {None, 0}:
            raise ValueError("max_depth=2 requires stacked_operator_boundary_target=0")
        self.total_boundary_hard_cap = int(
            total_boundary_hard_cap
            if total_boundary_hard_cap is not None
            else self.single_operator_boundary_target
            + self.stacked_operator_boundary_target
        )
        if self.total_boundary_hard_cap < max(
            self.single_operator_boundary_target,
            self.stacked_operator_boundary_target,
        ):
            raise ValueError(
                "total_boundary_hard_cap must be at least each enabled layer target"
            )
        # Compatibility projection for callers which still inspect this field.
        self.boundary_target = self.total_boundary_hard_cap
        self.max_depth = max_depth
        self.allow_operator_repeat_in_path = allow_operator_repeat_in_path
        self.pipeline_mode = pipeline_mode
        self.max_iterations = max_iterations
        self.rule_only_difficulty = rule_only_difficulty
        self.defer_gpt_experimental_evaluation = defer_gpt_experimental_evaluation
        self.artifact_retention = artifact_retention
        self.max_request_attempts_per_sample = max(0, max_request_attempts_per_sample)
        self.max_evaluations_per_sample = max(0, max_evaluations_per_sample)
        self.sample_timeout_seconds = max(0.0, sample_timeout_seconds)
        self.routing_mode = (routing_mode or os.getenv("ROUTING_MODE", "rule")).strip().lower()
        self.router_model = router_model or os.getenv("ROUTER_MODEL", "")
        self.router_base_url = router_base_url or os.getenv("ROUTER_BASE_URL", "")
        self.router_timeout_seconds = max(0.0, float(router_timeout_seconds))
        self.router_retries = max(0, int(router_retries))
        self.router_concurrency = max(1, int(router_concurrency))
        self.router_cache = Path(router_cache) if router_cache else self.work_dir / "frontier_router_cache.jsonl"
        self.profile_model = profile_model or os.getenv("PROFILE_MODEL", "")
        self.profile_base_url = profile_base_url or os.getenv("PROFILE_BASE_URL", "")
        self.profile_concurrency = max(1, int(profile_concurrency))
        self.checkpoint_path = self.work_dir / "vertical_search_checkpoint.jsonl"
        self.artifacts = VerticalArtifactStore(self.work_dir)
        self.started_at_by_sample: Dict[str, float] = {}
        self.run_started_at = time.monotonic()

    def _memory_paths(self) -> Tuple[Path, Path]:
        return (
            self.memory_dir / "operator_memory_bank.jsonl",
            self.memory_dir / "failure_memory_bank.jsonl",
        )

    def _request_budget_path(self, sample_key: str) -> Path:
        import hashlib

        digest = hashlib.sha256(sample_key.encode("utf-8")).hexdigest()[:16]
        return self.work_dir / "request_budgets" / f"{digest}.sqlite"

    @contextmanager
    def _sample_request_budget(self, sample_key: str):
        if not self.max_request_attempts_per_sample:
            yield
            return
        ledger = self._request_budget_path(sample_key)
        initialize_request_budget(ledger, self.max_request_attempts_per_sample)
        previous = os.environ.get(REQUEST_BUDGET_PATH_ENV)
        os.environ[REQUEST_BUDGET_PATH_ENV] = str(ledger)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(REQUEST_BUDGET_PATH_ENV, None)
            else:
                os.environ[REQUEST_BUDGET_PATH_ENV] = previous

    def _request_budget_exhausted(self, sample_key: str) -> bool:
        return bool(
            self.max_request_attempts_per_sample
            and request_budget_usage(self._request_budget_path(sample_key))
            >= self.max_request_attempts_per_sample
        )

    def _remaining_sample_seconds(self, sample_key: str) -> Optional[float]:
        if not self.sample_timeout_seconds:
            return None
        started_at = self.started_at_by_sample.setdefault(sample_key, time.monotonic())
        elapsed = time.monotonic() - started_at
        remaining = self.sample_timeout_seconds - elapsed
        if remaining <= 0:
            raise TimeoutError("vertical search sample timeout exceeded")
        return remaining

    @contextmanager
    def _sample_stage_deadline(self, sample_key: str):
        """Expose a per-sample deadline to every subprocess in one closure."""

        remaining = self._remaining_sample_seconds(sample_key)
        if remaining is None:
            yield
            return
        key = "SEARCH_STAGE_DEADLINE_EPOCH"
        previous = os.environ.get(key)
        os.environ[key] = str(time.time() + remaining)
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = previous

    def _memory_snapshot(self) -> Dict[str, Any]:
        operator_path, failure_path = self._memory_paths()
        return {
            "operator_memory_sha256": (
                sha256_file(operator_path) if operator_path.is_file() else None
            ),
            "failure_memory_sha256": (
                sha256_file(failure_path) if failure_path.is_file() else None
            ),
        }

    def _frontier_profile_paths(self, node_id: str) -> Tuple[Path, Path]:
        parent_dir = self._parent_work_dir(node_id)
        return (
            parent_dir / "frontier_profile_input.jsonl",
            parent_dir / "frontier_profiled_parent.jsonl",
        )

    @staticmethod
    def _frontier_context(
        parent_node: Mapping[str, Any],
        parent_record: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return {
            "enabled": True,
            "root_node_id": parent_node["root_node_id"],
            "parent_node_id": parent_node["node_id"],
            "parent_depth": int(parent_node["depth"]),
            "operator_stack": list(parent_node.get("operator_stack") or []),
            "direct_parent_score_rate": float(parent_node["score_rate"]),
            "root_score_rate": float(parent_node["root_score_rate"]),
            "prompt_sha256": normalized_prompt_hash(parent_record.get("prompt")),
        }

    def _frontier_profile_input(
        self,
        parent_record: Mapping[str, Any],
        parent_node: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Prepare one current-node profile input without root-score leakage."""

        prepared = deepcopy(dict(parent_record))
        for field in (
            "round0_score_trials",
            "round0_score_summary",
            "representative_round0_answer",
            "search_state",
            "multi_operator_search_state",
            "vertical_search_state",
        ):
            prepared.pop(field, None)
        meta_info = prepared.get("meta_info")
        meta_copy = deepcopy(dict(meta_info)) if isinstance(meta_info, Mapping) else {}
        # The previous-parent snapshot is useful to downstream evolution, but
        # it must not influence a fresh diagnosis of this frontier.
        meta_copy.pop("parent_snapshot", None)
        # The generic profile stage receives metadata rather than top-level
        # rubric fields.  Put the current evaluation contract in a scoped
        # metadata object so the frontier diagnosis sees the new rubric and
        # score prompt without changing the shared profile-stage API.
        meta_copy["frontier_evaluation_evidence"] = {
            "rubric": deepcopy(parent_record.get("rubric")),
            "score_prompt": parent_record.get("score_prompt"),
        }
        prepared["meta_info"] = meta_copy
        prepared["frontier_route"] = self._frontier_context(parent_node, parent_record)
        return prepared

    def _profile_frontier(
        self,
        parent_record: Mapping[str, Any],
        parent_node: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """Persist and reuse a fresh profile for one depth-2 frontier node."""

        profile_input_path, profile_output_path = self._frontier_profile_paths(
            _clean(parent_node["node_id"])
        )
        if not profile_output_path.is_file():
            profile_input_path.parent.mkdir(parents=True, exist_ok=True)
            profile_input = self._frontier_profile_input(parent_record, parent_node)
            _write_jsonl_atomic([profile_input], str(profile_input_path))
            command = _python_stage(
                self.project_dir,
                "profile_samples.py",
                "--input",
                str(profile_input_path),
                "--output",
                str(profile_output_path),
                "--model",
                self.profile_model,
                "--base-url",
                self.profile_base_url,
                "--concurrency",
                str(self.profile_concurrency),
            )
            _run(
                command,
                cwd=self.project_dir,
                timeout_seconds=self._remaining_sample_seconds(
                    _clean(parent_node["sample_id"])
                ),
            )
        rows = load_json_records(
            str(profile_output_path), stage="vertical_frontier_profile_recovery"
        )
        if len(rows) != 1:
            raise RuntimeError("frontier profile must contain exactly one record")
        profiled = deepcopy(dict(rows[0]))
        context = self._frontier_context(parent_node, parent_record)
        metadata = profiled.get("profile_metadata")
        if isinstance(metadata, Mapping):
            context["profile_version"] = _clean(metadata.get("profile_model")) or None
        profiled["frontier_route"] = context
        return profiled

    @staticmethod
    def _is_usable_route(record: Mapping[str, Any]) -> bool:
        route = record.get("operator_route")
        if not isinstance(route, Mapping):
            return False
        selected = route.get("selected_operator_ids")
        return isinstance(selected, list) or bool(
            _clean(route.get("primary_operator"))
            or list(route.get("backup_operators") or [])
        )

    def _normalize_vertical_route(
        self,
        routed: Mapping[str, Any],
        *,
        parent_node: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> Tuple[Dict[str, Any], List[str]]:
        normalized = deepcopy(dict(routed))
        plan = build_vertical_operator_plan(
            normalized,
            operator_stack=parent_node.get("operator_stack") or [],
            allow_operator_repeat_in_path=bool(
                state.get("allow_operator_repeat_in_path")
            ),
        )
        route = deepcopy(dict(normalized.get("operator_route") or {}))
        route["vertical_original_avoid_operators"] = list(
            route.get("avoid_operators") or []
        )
        route["vertical_router_assignment_mode"] = route.get("assignment_mode")
        # The vertical coordinator freezes one newly routed plan itself.  The
        # horizontal executor must therefore treat it as a natural plan even
        # when the upstream Router produced a live-assignment route.
        route["assignment_mode"] = "natural"
        route["selected_operator_ids"] = list(plan)
        route["primary_operator"] = plan[0] if plan else None
        route["backup_operators"] = plan[1:]
        route["avoid_operators"] = []
        normalized["operator_route"] = route
        return normalized, plan

    def _route_frontier(
        self,
        parent_record: Mapping[str, Any],
        parent_node: Mapping[str, Any],
        state: Mapping[str, Any],
    ) -> Tuple[Dict[str, Any], List[str], Dict[str, Any]]:
        # The root uses the already admitted and routed input.  A frontier is
        # different: profile and route evidence must be rebuilt from its own
        # question, answers, rubric, score prompt, and score.
        if int(parent_node.get("depth") or 0) == 1 and self._is_usable_route(
            parent_record
        ):
            routed = deepcopy(dict(parent_record))
        else:
            operator_path, failure_path = self._memory_paths()
            profile_input = self._profile_frontier(parent_record, parent_node)
            remaining = self._remaining_sample_seconds(_clean(parent_node["sample_id"]))
            routed = route_records(
                [profile_input],
                operator_memory=load_jsonl_if_exists(str(operator_path)),
                failure_memory=load_jsonl_if_exists(str(failure_path)),
                routing_mode=self.routing_mode,
                assignment_mode=("live" if self.routing_mode == "hybrid" else "natural"),
                router_model=self.router_model,
                router_base_url=self.router_base_url,
                router_timeout_seconds=(
                    min(self.router_timeout_seconds, remaining)
                    if remaining is not None
                    else self.router_timeout_seconds
                ),
                router_retries=self.router_retries,
                router_concurrency=self.router_concurrency,
                router_cache=str(self.router_cache),
            )[0]
        routed, plan = self._normalize_vertical_route(
            routed, parent_node=parent_node, state=state
        )
        return routed, plan, self._memory_snapshot()

    def _parent_work_dir(self, node_id: str) -> Path:
        import hashlib

        digest = hashlib.sha256(node_id.encode("utf-8")).hexdigest()[:16]
        return self.work_dir / "parents" / digest

    def _latest_parent_state(
        self,
        parent_dir: Path,
        prepared: Mapping[str, Any],
    ) -> Optional[Dict[str, Any]]:
        candidates = [parent_dir / "parent_search_state.jsonl"]
        candidates.extend(
            sorted(
                parent_dir.glob("wave_*/search_state_updated.jsonl"),
                reverse=True,
            )
        )
        candidates.extend(
            sorted(
                parent_dir.glob("wave_*/search_state_claimed.jsonl"),
                reverse=True,
            )
        )
        parent_id = _clean(prepared.get("parent_node_id"))
        for path in candidates:
            if not path.is_file():
                continue
            rows = load_json_records(str(path), stage="vertical_parent_recovery")
            for row in rows:
                raw_state = row.get("search_state")
                if not isinstance(raw_state, Mapping):
                    continue
                if _clean(raw_state.get("parent_node_id")) == parent_id:
                    return upgrade_search_state(raw_state, record=prepared)
        return None

    def _prepare_parent(
        self,
        routed_record: Mapping[str, Any],
        parent_node: Mapping[str, Any],
        state: Mapping[str, Any],
        plan: Sequence[str],
        *,
        local_boundary_target: int,
    ) -> Dict[str, Any]:
        prepared = deepcopy(dict(routed_record))
        for field in (
            "search_state",
            "multi_operator_search_state",
            "vertical_search_state",
            "branch_id",
            "candidate_id",
            "candidate_group_id",
            "candidate_operator",
            "node_id",
            "vertical_node",
        ):
            prepared.pop(field, None)
        prepared["parent_node_id"] = parent_node["node_id"]
        prepared["vertical_search_context"] = {
            "root_node_id": parent_node["root_node_id"],
            "parent_node_id": parent_node["node_id"],
            "parent_depth": parent_node["depth"],
            "child_depth": int(parent_node["depth"]) + 1,
            "operator_stack": list(parent_node.get("operator_stack") or []),
            "max_depth": state["max_depth"],
            "route_type": (
                "frontier_route" if int(parent_node["depth"]) > 1 else "root_route"
            ),
        }
        horizontal = initialize_search_state(
            prepared,
            branch_window=self.branch_window,
            boundary_target=local_boundary_target,
            operator_sort_mode="route",
        )
        horizontal["seen_prompt_hashes"] = list(state["registered_prompt_hashes"])
        horizontal["selected_operator_ids"] = list(plan)
        prepared["search_state"] = horizontal
        parent_dir = self._parent_work_dir(parent_node["node_id"])
        recovered = self._latest_parent_state(parent_dir, prepared)
        if recovered is not None:
            recovered["seen_prompt_hashes"] = list(
                dict.fromkeys(
                    list(state["registered_prompt_hashes"])
                    + list(recovered.get("seen_prompt_hashes") or [])
                )
            )
            prepared["search_state"] = recovered
        prepared["search_state"] = self._reconcile_parent_artifacts(
            prepared["search_state"],
            self._parent_work_dir(parent_node["node_id"]),
        )
        return prepared

    def _reconcile_parent_artifacts(
        self,
        horizontal_state: Mapping[str, Any],
        parent_dir: Path,
    ) -> Dict[str, Any]:
        """Promote durable branch artifacts into scheduler state after a crash."""

        path = parent_dir / "branch_results.jsonl"
        if not path.is_file():
            return deepcopy(dict(horizontal_state))
        state = upgrade_search_state(horizontal_state)
        entries = {
            _clean(entry.get("branch_id")): entry
            for entry in state.get("operator_plan") or []
        }
        for row in _artifact_records(path):
            branch_id = _clean(row.get("branch_id"))
            entry = entries.get(branch_id)
            if entry is None:
                continue
            existing = state.get("branch_summaries", {}).get(branch_id)
            if isinstance(existing, Mapping) and _clean(
                existing.get("branch_status")
            ):
                continue
            if entry.get("status") == "pending":
                entry["status"] = "running"
                entry["branch_stage"] = "artifact_recovered"
            artifact_type = _clean(row.get("artifact_type"))
            record = row.get("record")
            record = record if isinstance(record, Mapping) else {}
            if artifact_type == "complete_branch":
                state = merge_decision_result(state, record)
                entries = {
                    _clean(item.get("branch_id")): item
                    for item in state.get("operator_plan") or []
                }
                continue
            branch_status = _clean(record.get("branch_status"))
            if branch_status in {
                "duplicate_exhausted",
                "validation_failed",
                "not_applicable",
                "branch_error",
            }:
                state = mark_branch_terminal(
                    state,
                    branch_id=branch_id,
                    branch_status=branch_status,
                    reason=_clean(record.get("terminal_reason")),
                )
                entries = {
                    _clean(item.get("branch_id")): item
                    for item in state.get("operator_plan") or []
                }
        return state

    @staticmethod
    def _active_execution(
        state: Mapping[str, Any], parent_node_id: str
    ) -> Optional[Mapping[str, Any]]:
        for execution in reversed(state.get("execution_sequence") or []):
            if (
                _clean(execution.get("parent_node_id")) == parent_node_id
                and _clean(execution.get("status")) == "running"
            ):
                return execution
        return None

    @staticmethod
    def _record_active_plan(
        state: Mapping[str, Any],
        parent_node_id: str,
        *,
        plan: Sequence[str],
        operator_route: Mapping[str, Any],
        memory_version: Mapping[str, Any],
        frontier_route: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        updated = upgrade_vertical_search_state(state)
        for execution in reversed(updated["execution_sequence"]):
            if (
                _clean(execution.get("parent_node_id")) == parent_node_id
                and _clean(execution.get("status")) == "running"
            ):
                execution["operator_plan"] = list(plan)
                execution["operator_route"] = deepcopy(dict(operator_route))
                execution["memory_version"] = deepcopy(dict(memory_version))
                if frontier_route is not None:
                    execution["frontier_route"] = deepcopy(dict(frontier_route))
                break
        return updated

    def _execute_parent(
        self,
        prepared: Mapping[str, Any],
        parent_node: Mapping[str, Any],
        *,
        local_boundary_target: int,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        parent_dir = self._parent_work_dir(_clean(parent_node.get("node_id")))
        parent_dir.mkdir(parents=True, exist_ok=True)
        runner = MultiOperatorSearchRunner(
            project_dir=self.project_dir,
            work_dir=parent_dir,
            memory_dir=self.memory_dir,
            branch_window=self.branch_window,
            boundary_target=local_boundary_target,
            operator_sort_mode="route",
            operator_statistics=None,
            exploration_ratio=0.0,
            max_iterations=self.max_iterations,
            rule_only_difficulty=self.rule_only_difficulty,
            defer_gpt_experimental_evaluation=self.defer_gpt_experimental_evaluation,
            artifact_retention=self.artifact_retention,
        )
        sample_key = _clean(parent_node["sample_id"])
        with self._sample_request_budget(sample_key), self._sample_stage_deadline(sample_key):
            rows = (
                runner.run_stream([prepared])
                if self.pipeline_mode == "stream"
                else runner.run(
                    [prepared], output_path=parent_dir / "parent_search_state.jsonl"
                )
            )
        if len(rows) != 1:
            raise RuntimeError("vertical parent execution must return exactly one state")
        _write_jsonl_atomic(rows, str(parent_dir / "parent_search_state.jsonl"))
        artifacts = _artifact_records(parent_dir / "branch_results.jsonl")
        runner.cleanup_published_intermediates(self.pipeline_mode)
        return deepcopy(dict(rows[0]["search_state"])), artifacts

    @staticmethod
    def _branch_artifacts_for_parent(
        artifacts: Iterable[Mapping[str, Any]], parent_node_id: str
    ) -> Dict[str, Mapping[str, Any]]:
        result: Dict[str, Mapping[str, Any]] = {}
        for row in artifacts:
            if _clean(row.get("parent_node_id")) != parent_node_id:
                continue
            branch_id = _clean(row.get("branch_id"))
            if branch_id:
                result[branch_id] = row
        return result

    def _merge_parent_artifacts(
        self,
        state: Mapping[str, Any],
        parent_node: Mapping[str, Any],
        horizontal_state: Mapping[str, Any],
        artifacts: Sequence[Mapping[str, Any]],
        plan: Sequence[str],
        memory_version: Mapping[str, Any],
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        rows_by_branch = self._branch_artifacts_for_parent(
            artifacts, _clean(parent_node.get("node_id"))
        )
        nodes_by_id = {
            _clean(record.get("node_id")): _node_metadata(record)
            for record in self.artifacts.iter_records("node")
        }
        child_records: List[Dict[str, Any]] = []
        plan_entries = list(horizontal_state.get("operator_plan") or [])
        plan_rank = {operator_id: index for index, operator_id in enumerate(plan, 1)}
        for entry in plan_entries:
            branch_id = _clean(entry.get("branch_id"))
            row = rows_by_branch.get(branch_id)
            if not row or _clean(row.get("artifact_type")) != "complete_branch":
                continue
            branch = row.get("record")
            if not isinstance(branch, Mapping):
                continue
            child_node = build_child_node(
                parent_node,
                branch,
                max_depth=int(state["max_depth"]),
                generation_sequence=plan_rank.get(_clean(entry.get("operator_id")), 0),
            )
            child = attach_vertical_node(branch, child_node)
            child_records.append(child)
            nodes_by_id[child_node["node_id"]] = child_node
            self.artifacts.append("node", child)
            if child_node["node_status"] == "boundary_candidate":
                self.artifacts.append("edge", build_boundary_edge(child_node))
                self.artifacts.append(
                    "path", build_boundary_path(child_node, nodes_by_id)
                )

        summaries = horizontal_state.get("branch_summaries")
        summaries = summaries if isinstance(summaries, Mapping) else {}
        completed_attempt_count = 0
        target_reached = (
            int(horizontal_state.get("boundary_candidate_count") or 0)
            >= int(horizontal_state.get("boundary_target") or 1)
        )
        for rank, entry in enumerate(plan_entries, 1):
            branch_id = _clean(entry.get("branch_id"))
            summary = summaries.get(branch_id)
            summary = summary if isinstance(summary, Mapping) else {}
            branch_status = _clean(summary.get("branch_status"))
            entry_status = _clean(entry.get("status"))
            override = None
            if branch_status in {
                "duplicate_exhausted",
                "validation_failed",
                "not_applicable",
                "branch_error",
            }:
                override = branch_status
            elif entry_status == "pending" and target_reached:
                override = "skipped_global_termination"
            elif entry_status in {"completed", "duplicate_exhausted", "not_applicable", "branch_error"}:
                override = entry_status
            attempt = build_operator_attempt(
                parent_node,
                entry,
                operator_rank=rank,
                branch_summary=summary,
                status_override=override,
            )
            self.artifacts.append("attempt", attempt)
            if attempt["status"] not in {
                "pending",
                "running",
                "skipped_depth_limit",
                "skipped_global_termination",
            }:
                completed_attempt_count += 1
        updated = complete_frontier(
            state,
            parent_node,
            child_records,
            completed_attempt_count=completed_attempt_count,
            operator_plan=plan,
            memory_version=memory_version,
        )
        return updated, child_records

    @staticmethod
    def _remaining_boundary_slots(
        state: Mapping[str, Any], parent_node: Mapping[str, Any]
    ) -> int:
        total_remaining = max(
            0,
            int(state["total_boundary_hard_cap"])
            - int(state["total_boundary_count"]),
        )
        depth = int(parent_node.get("depth") or 0)
        if depth == 1:
            layer_remaining = max(
                0,
                int(state["single_operator_boundary_target"])
                - int(state["single_operator_boundary_count"]),
            )
        elif depth == 2:
            layer_remaining = max(
                0,
                int(state["stacked_operator_boundary_target"])
                - int(state["stacked_operator_boundary_count"]),
            )
        else:
            return 0
        return min(total_remaining, layer_remaining)

    def _checkpoint(self, records: Sequence[Mapping[str, Any]]) -> None:
        _write_jsonl_atomic(records, str(self.checkpoint_path))

    def _initial_records(
        self, records: Sequence[Mapping[str, Any]]
    ) -> List[Dict[str, Any]]:
        checkpoint_by_sample: Dict[str, Dict[str, Any]] = {}
        if self.checkpoint_path.is_file():
            for row in load_json_records(
                str(self.checkpoint_path), stage="vertical_search_recovery"
            ):
                checkpoint_by_sample[sample_identity(row)] = dict(row)
        initialized: List[Dict[str, Any]] = []
        for input_record in records:
            identity = sample_identity(input_record)
            if identity in checkpoint_by_sample:
                recovered_record = checkpoint_by_sample[identity]
                recovered_state = recovered_record.get("vertical_search_state")
                if isinstance(recovered_state, Mapping):
                    recovered_state = upgrade_vertical_search_state(recovered_state)
                    expected_input_sha256 = input_record_sha256(input_record)
                    recovered_input_sha256 = _clean(
                        recovered_state.get("input_record_sha256")
                    )
                    if recovered_input_sha256 != expected_input_sha256:
                        raise ValueError(
                            "vertical checkpoint input fingerprint mismatch for "
                            f"{identity}; use a new work directory for changed input"
                        )
                    if (
                        recovered_state.get("status") == "partial"
                        and recovered_state.get("termination_reason")
                        in SYSTEM_TERMINATION_REASONS
                    ):
                        recovered_state["status"] = "running"
                        recovered_state["termination_reason"] = None
                        recovered_state["resumed_from_system_termination"] = True
                    recovered_record["vertical_search_state"] = recovered_state
                initialized.append(recovered_record)
                continue
            record = deepcopy(dict(input_record))
            state = initialize_vertical_search_state(
                record,
                max_depth=self.max_depth,
                boundary_target=self.boundary_target,
                single_operator_boundary_target=self.single_operator_boundary_target,
                stacked_operator_boundary_target=self.stacked_operator_boundary_target,
                total_boundary_hard_cap=self.total_boundary_hard_cap,
                allow_operator_repeat_in_path=self.allow_operator_repeat_in_path,
            )
            if state is not None:
                root = build_root_node(record, max_depth=self.max_depth)
                root_record = attach_vertical_node(record, root)
                self.artifacts.append("node", root_record)
                record["vertical_search_state"] = state
            initialized.append(record)
        self._checkpoint(initialized)
        return initialized

    @staticmethod
    def _assert_unique_input_identities(
        records: Sequence[Mapping[str, Any]],
    ) -> None:
        """Reject inputs that would otherwise share a checkpoint/artifact tree."""

        first_positions: Dict[str, int] = {}
        duplicates: List[str] = []
        for position, record in enumerate(records, start=1):
            identity = sample_identity(record)
            if identity in first_positions:
                duplicates.append(
                    f"{identity} (rows {first_positions[identity]} and {position})"
                )
            else:
                first_positions[identity] = position
        if duplicates:
            raise ValueError(
                "vertical search requires unique sample_id/index identities; "
                "duplicate inputs would share artifacts: " + "; ".join(duplicates)
            )

    def _budget_reason(
        self, state: Mapping[str, Any], sample_key: str
    ) -> Optional[str]:
        if (
            self._request_budget_exhausted(sample_key)
        ):
            return "request_budget_exhausted"
        if self.max_evaluations_per_sample:
            scored = sum(
                1
                for record in self.artifacts.iter_records("node")
                if _clean(record.get("sample_id")) == sample_key
                and int(record.get("depth") or 0) > 1
            )
            if scored >= self.max_evaluations_per_sample:
                return "evaluation_budget_exhausted"
        if self.sample_timeout_seconds:
            elapsed = time.monotonic() - self.started_at_by_sample[sample_key]
            if elapsed >= self.sample_timeout_seconds:
                return "timeout"
        return None

    def run(self, records: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
        self.run_started_at = time.monotonic()
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self._assert_unique_input_identities(records)
        output_records = self._initial_records(records)
        for output_record in output_records:
            raw_state = output_record.get("vertical_search_state")
            if not isinstance(raw_state, Mapping):
                continue
            state = upgrade_vertical_search_state(raw_state)
            sample_key = sample_identity(output_record)
            self.started_at_by_sample.setdefault(sample_key, time.monotonic())
            while state["status"] == "running":
                all_node_records = list(self.artifacts.iter_records("node"))
                node_records = {
                    _clean(record.get("node_id")): record
                    for record in all_node_records
                    if _clean(record.get("sample_id")) == sample_key
                }
                nodes = {
                    node_id: _node_metadata(record)
                    for node_id, record in node_records.items()
                }
                state = reconcile_vertical_boundary_counts(state, nodes)
                budget_reason = self._budget_reason(state, sample_key)
                if budget_reason:
                    state = mark_system_termination(state, budget_reason)
                    break
                state, parent_id = claim_next_frontier(state, nodes)
                if parent_id is None:
                    break
                output_record["vertical_search_state"] = state
                self._checkpoint(output_records)
                parent_record = node_records[parent_id]
                parent_node = nodes[parent_id]
                active_execution = self._active_execution(state, parent_id)
                saved_plan = (
                    list(active_execution.get("operator_plan") or [])
                    if isinstance(active_execution, Mapping)
                    else []
                )
                saved_route = (
                    active_execution.get("operator_route")
                    if isinstance(active_execution, Mapping)
                    else None
                )
                try:
                    if saved_plan and isinstance(saved_route, Mapping):
                        with self._sample_request_budget(sample_key):
                            routed = (
                                self._profile_frontier(parent_record, parent_node)
                                if int(parent_node.get("depth") or 0) > 1
                                else deepcopy(dict(parent_record))
                            )
                        routed["operator_route"] = deepcopy(dict(saved_route))
                        plan = saved_plan
                        saved_memory = active_execution.get("memory_version")
                        memory_version = (
                            deepcopy(dict(saved_memory))
                            if isinstance(saved_memory, Mapping)
                            else {}
                        )
                    else:
                        with self._sample_request_budget(sample_key):
                            routed, plan, memory_version = self._route_frontier(
                                parent_record, parent_node, state
                            )
                        state = self._record_active_plan(
                            state,
                            parent_id,
                            plan=plan,
                            operator_route=routed.get("operator_route") or {},
                            memory_version=memory_version,
                            frontier_route=(
                                routed.get("frontier_route")
                                if isinstance(routed.get("frontier_route"), Mapping)
                                else None
                            ),
                        )
                        output_record["vertical_search_state"] = state
                        self._checkpoint(output_records)
                except TimeoutError:
                    state = mark_system_termination(state, "timeout")
                    output_record["vertical_search_state"] = state
                    self._checkpoint(output_records)
                    break
                except Exception:
                    if self._request_budget_exhausted(sample_key):
                        state = mark_system_termination(
                            state, "request_budget_exhausted"
                        )
                        output_record["vertical_search_state"] = state
                        self._checkpoint(output_records)
                        break
                    state = mark_system_termination(state, "fatal_error")
                    output_record["vertical_search_state"] = state
                    self._checkpoint(output_records)
                    raise
                if not plan:
                    state = complete_frontier(
                        state,
                        parent_node,
                        [],
                        completed_attempt_count=0,
                        operator_plan=[],
                        memory_version=memory_version,
                    )
                    output_record["vertical_search_state"] = state
                    self._checkpoint(output_records)
                    continue

                remaining_target = self._remaining_boundary_slots(state, parent_node)
                if remaining_target <= 0:
                    state = complete_frontier(
                        state,
                        parent_node,
                        [],
                        completed_attempt_count=0,
                        operator_plan=[],
                        memory_version=memory_version,
                    )
                    output_record["vertical_search_state"] = state
                    self._checkpoint(output_records)
                    continue
                if self.max_evaluations_per_sample:
                    scored_count = sum(
                        1
                        for node in self.artifacts.iter_records("node")
                        if _clean(node.get("sample_id")) == sample_key
                        and int(node.get("depth") or 0) > 1
                    )
                    remaining_evaluations = max(
                        0, self.max_evaluations_per_sample - scored_count
                    )
                    if remaining_evaluations < len(plan):
                        state = mark_system_termination(
                            state, "evaluation_budget_exhausted"
                        )
                        break
                if not plan:
                    state = mark_system_termination(
                        state, "request_budget_exhausted"
                    )
                    break
                prepared = self._prepare_parent(
                    routed,
                    parent_node,
                    state,
                    plan,
                    local_boundary_target=remaining_target,
                )
                try:
                    with self._sample_request_budget(sample_key), self._sample_stage_deadline(sample_key):
                        horizontal_state, artifacts = self._execute_parent(
                            prepared,
                            parent_node,
                            local_boundary_target=remaining_target,
                        )
                    state, _children = self._merge_parent_artifacts(
                        state,
                        parent_node,
                        horizontal_state,
                        artifacts,
                        plan,
                        memory_version,
                    )
                except TimeoutError:
                    state = mark_system_termination(state, "timeout")
                    output_record["vertical_search_state"] = state
                    self._checkpoint(output_records)
                    break
                except Exception:
                    if self._request_budget_exhausted(sample_key):
                        state = mark_system_termination(
                            state, "request_budget_exhausted"
                        )
                        output_record["vertical_search_state"] = state
                        self._checkpoint(output_records)
                        break
                    state = mark_system_termination(state, "fatal_error")
                    output_record["vertical_search_state"] = state
                    self._checkpoint(output_records)
                    raise
                output_record["vertical_search_state"] = state
                self._checkpoint(output_records)
            output_record["vertical_search_state"] = state
            self._checkpoint(output_records)
        self._write_summary(output_records)
        return output_records

    def _write_summary(self, records: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
        nodes = list(self.artifacts.iter_records("node"))
        attempts = list(self.artifacts.iter_records("attempt"))
        edges = list(self.artifacts.iter_records("edge"))
        paths = list(self.artifacts.iter_records("path"))
        states = [
            record.get("vertical_search_state")
            for record in records
            if isinstance(record.get("vertical_search_state"), Mapping)
        ]
        sample_ids = {_clean(state.get("root_node_id")) for state in states}
        edges_by_sample = Counter(_clean(edge.get("sample_id")) for edge in edges)
        paths_by_sample = Counter(_clean(path.get("sample_id")) for path in paths)
        single_boundaries_by_sample = Counter(
            _clean(node.get("sample_id"))
            for node in nodes
            if int(node.get("depth") or 0) == 2
            and _clean(node.get("node_status")) == "boundary_candidate"
        )
        stacked_boundaries_by_sample = Counter(
            _clean(node.get("sample_id"))
            for node in nodes
            if int(node.get("depth") or 0) == 3
            and _clean(node.get("node_status")) == "boundary_candidate"
        )
        scored_by_sample = Counter(
            _clean(node.get("sample_id"))
            for node in nodes
            if int(node.get("depth") or 0) > 1
        )
        termination_distribution = Counter(
            _clean(state.get("termination_reason")) for state in states
        )
        operator_metrics: Dict[str, Dict[str, Any]] = {}
        attempts_by_operator: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for attempt in attempts:
            attempts_by_operator[_clean(attempt.get("operator_id"))].append(attempt)
        nodes_by_operator: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
        for node in nodes:
            operator = _clean(node.get("operator_from_parent"))
            if operator:
                nodes_by_operator[operator].append(node)
        for operator, planned_operator_attempts in sorted(attempts_by_operator.items()):
            operator_attempts = [
                attempt
                for attempt in planned_operator_attempts
                if attempt.get("status")
                not in {
                    "pending",
                    "running",
                    "skipped_depth_limit",
                    "skipped_global_termination",
                }
            ]
            completed_nodes = nodes_by_operator.get(operator, [])
            count = len(operator_attempts)
            operator_metrics[operator] = {
                "planned_attempt_count": len(planned_operator_attempts),
                "attempt_count": count,
                "validation_pass_rate": len(completed_nodes) / count if count else 0.0,
                "scoring_completion_rate": len(completed_nodes) / count if count else 0.0,
                "boundary_edge_hit_rate": (
                    sum(node.get("node_status") == "boundary_candidate" for node in completed_nodes)
                    / len(completed_nodes)
                    if completed_nodes
                    else 0.0
                ),
                "score_increased_rate": (
                    sum(node.get("node_status") == "score_increased" for node in completed_nodes)
                    / len(completed_nodes)
                    if completed_nodes
                    else 0.0
                ),
                "not_applicable_rate": sum(
                    attempt.get("status") == "not_applicable" for attempt in operator_attempts
                ) / count if count else 0.0,
                "duplicate_exhausted_rate": sum(
                    attempt.get("status") == "duplicate_exhausted" for attempt in operator_attempts
                ) / count if count else 0.0,
            }
        scored_nodes = [node for node in nodes if int(node.get("depth") or 0) > 1]
        generation_request_count = sum(
            int(attempt.get("generation_attempt_count") or 0) for attempt in attempts
        )
        observed_scoring_request_count = 0
        for node in scored_nodes:
            for field in ("qwen_score_summary", "gpt_score_summary"):
                score_summary = node.get(field)
                if isinstance(score_summary, Mapping):
                    observed_scoring_request_count += int(
                        score_summary.get("requested_count") or 0
                    )
        # Every scored branch also executes one reference-answer and one rubric
        # generation request in the unchanged downstream closure.
        observed_api_request_count = (
            generation_request_count
            + observed_scoring_request_count
            + (2 * len(scored_nodes))
        )
        elapsed_seconds = max(0.0, time.monotonic() - self.run_started_at)
        ordered_two = Counter(
            ">".join(node.get("operator_stack") or [])
            for node in scored_nodes
            if len(node.get("operator_stack") or []) == 2
        )
        unordered = Counter(
            "+".join(sorted(node.get("operator_stack") or []))
            for node in scored_nodes
            if len(node.get("operator_stack") or []) >= 2
        )
        ordered_two_hits = Counter(
            ">".join(node.get("operator_stack") or [])
            for node in scored_nodes
            if len(node.get("operator_stack") or []) == 2
            and node.get("node_status") == "boundary_candidate"
        )
        unordered_hits = Counter(
            "+".join(sorted(node.get("operator_stack") or []))
            for node in scored_nodes
            if len(node.get("operator_stack") or []) >= 2
            and node.get("node_status") == "boundary_candidate"
        )
        reverse_order_comparisons: Dict[str, Dict[str, Any]] = {}
        for key, count in ordered_two.items():
            left, right = key.split(">", 1)
            reverse = f"{right}>{left}"
            if reverse not in ordered_two or key > reverse:
                continue
            reverse_order_comparisons[f"{key}|{reverse}"] = {
                key: {
                    "occurrences": count,
                    "boundary_hit_rate": ordered_two_hits[key] / count,
                },
                reverse: {
                    "occurrences": ordered_two[reverse],
                    "boundary_hit_rate": (
                        ordered_two_hits[reverse] / ordered_two[reverse]
                    ),
                },
            }
        edge_deltas = [float(node["edge_delta_score_rate"]) for node in scored_nodes]
        root_deltas = [float(node["root_delta_score_rate"]) for node in scored_nodes]
        vertical_records = [
            record
            for record in records
            if isinstance(record.get("vertical_search_state"), Mapping)
        ]
        summary = {
            "search_mode": "multi_operator_vertical_stack",
            "input_sample_count": len(records),
            "vertical_search_sample_count": len(states),
            "average_completed_depth": (
                statistics.fmean(
                    max(
                        (int(node.get("depth") or 1) for node in nodes if _clean(node.get("root_node_id")) == root_id),
                        default=1,
                    )
                    for root_id in sample_ids
                )
                if sample_ids
                else 0.0
            ),
            "average_scored_node_count": statistics.fmean(
                scored_by_sample.get(sample_identity(record), 0)
                for record in vertical_records
            ) if vertical_records else 0.0,
            "average_boundary_edge_count": statistics.fmean(
                edges_by_sample.get(sample_identity(record), 0)
                for record in vertical_records
            ) if vertical_records else 0.0,
            "average_boundary_path_count": statistics.fmean(
                paths_by_sample.get(sample_identity(record), 0)
                for record in vertical_records
            ) if vertical_records else 0.0,
            "average_single_operator_boundary_count": statistics.fmean(
                single_boundaries_by_sample.get(sample_identity(record), 0)
                for record in vertical_records
            ) if vertical_records else 0.0,
            "average_stacked_operator_boundary_count": statistics.fmean(
                stacked_boundaries_by_sample.get(sample_identity(record), 0)
                for record in vertical_records
            ) if vertical_records else 0.0,
            "normal_termination_sample_count": sum(
                reason
                in {
                    "operator_space_exhausted",
                    "single_operator_boundary_target_reached",
                    "stacked_operator_boundary_target_reached",
                    "total_boundary_hard_cap_reached",
                }
                for reason in termination_distribution.elements()
            ),
            "system_protection_termination_sample_count": sum(
                reason in SYSTEM_TERMINATION_REASONS
                for reason in termination_distribution.elements()
            ),
            "termination_reason_distribution": dict(termination_distribution),
            "operator_metrics": operator_metrics,
            "combination_metrics": {
                "ordered_pair_occurrences": dict(ordered_two),
                "ordered_pair_boundary_hit_rates": {
                    key: ordered_two_hits[key] / count
                    for key, count in ordered_two.items()
                },
                "unordered_combination_occurrences": dict(unordered),
                "unordered_combination_boundary_hit_rates": {
                    key: unordered_hits[key] / count
                    for key, count in unordered.items()
                },
                "reverse_order_comparisons": reverse_order_comparisons,
                "average_edge_delta_score_rate": statistics.fmean(edge_deltas) if edge_deltas else None,
                "average_root_delta_score_rate": statistics.fmean(root_deltas) if root_deltas else None,
            },
            "budget_metrics": {
                "api_request_count": observed_api_request_count,
                "api_request_count_scope": (
                    "generation plus recorded scoring summaries plus one reference-answer "
                    "and rubric request per scored node"
                ),
                "generation_request_count": generation_request_count,
                "completed_operator_attempt_count": len([
                    attempt for attempt in attempts
                    if attempt.get("status") not in {"pending", "running", "skipped_global_termination", "skipped_depth_limit"}
                ]),
                "evaluation_count": len(scored_nodes),
                "single_operator_boundary_count": sum(single_boundaries_by_sample.values()),
                "stacked_operator_boundary_count": sum(stacked_boundaries_by_sample.values()),
                "average_sample_runtime_seconds": (
                    elapsed_seconds / len(states) if states else 0.0
                ),
                "average_boundary_path_api_cost": (
                    observed_api_request_count / len(paths) if paths else None
                ),
                "max_depth_unexpanded_boundary_node_count": sum(
                    node.get("frontier_status") == "depth_limit" for node in nodes
                ),
                "system_protection_termination_count": sum(
                    reason in SYSTEM_TERMINATION_REASONS
                    for reason in termination_distribution.elements()
                ),
            },
            "artifact_counts": {
                "nodes": len(nodes),
                "attempts": len(attempts),
                "boundary_edges": len(edges),
                "boundary_paths": len(paths),
            },
        }
        (self.work_dir / "vertical_search_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run multi-operator vertical stack search."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--memory-dir", default="memory")
    parser.add_argument("--branch-window", type=int, default=1)
    parser.add_argument(
        "--boundary-target",
        type=int,
        default=5,
        help="Deprecated compatibility alias; supplies both layer targets when new targets are omitted.",
    )
    parser.add_argument("--single-operator-boundary-target", type=int, default=None)
    parser.add_argument("--stacked-operator-boundary-target", type=int, default=None)
    parser.add_argument("--total-boundary-hard-cap", type=int, default=None)
    parser.add_argument("--max-depth", type=int, default=3)
    parser.add_argument("--allow-operator-repeat-in-path", action="store_true")
    parser.add_argument("--pipeline-mode", choices=["step", "stream"], default="step")
    parser.add_argument("--max-iterations", type=int, default=1000)
    parser.add_argument("--rule-only-difficulty", action="store_true")
    parser.add_argument("--defer-gpt-experimental-evaluation", action="store_true")
    parser.add_argument(
        "--artifact-retention",
        choices=["compact", "full"],
        default=os.getenv("SEARCH_ARTIFACT_RETENTION", "compact"),
    )
    parser.add_argument("--max-request-attempts-per-sample", type=int, default=0)
    parser.add_argument("--max-evaluations-per-sample", type=int, default=0)
    parser.add_argument("--sample-timeout-seconds", type=float, default=0.0)
    parser.add_argument(
        "--routing-mode",
        choices=["rule", "hybrid"],
        default=os.getenv("ROUTING_MODE", "rule"),
    )
    parser.add_argument("--router-model", default=os.getenv("ROUTER_MODEL", ""))
    parser.add_argument("--router-base-url", default=os.getenv("ROUTER_BASE_URL", ""))
    parser.add_argument("--router-timeout", type=float, default=float(os.getenv("ROUTER_TIMEOUT", "60")))
    parser.add_argument("--router-retries", type=int, default=int(os.getenv("ROUTER_RETRIES", "0")))
    parser.add_argument("--router-concurrency", type=int, default=int(os.getenv("ROUTER_CONCURRENCY", "20")))
    parser.add_argument("--router-cache", default=None)
    parser.add_argument("--profile-model", default=os.getenv("PROFILE_MODEL", ""))
    parser.add_argument("--profile-base-url", default=os.getenv("PROFILE_BASE_URL", ""))
    parser.add_argument("--profile-concurrency", type=int, default=int(os.getenv("PROFILE_CONCURRENCY", "5")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    project_dir = Path(__file__).resolve().parent
    records = load_json_records(args.input, stage="vertical_operator_search")
    runner = VerticalOperatorSearchRunner(
        project_dir=project_dir,
        work_dir=Path(args.work_dir),
        memory_dir=Path(args.memory_dir),
        branch_window=args.branch_window,
        boundary_target=args.boundary_target,
        single_operator_boundary_target=args.single_operator_boundary_target,
        stacked_operator_boundary_target=args.stacked_operator_boundary_target,
        total_boundary_hard_cap=args.total_boundary_hard_cap,
        max_depth=args.max_depth,
        allow_operator_repeat_in_path=args.allow_operator_repeat_in_path,
        pipeline_mode=args.pipeline_mode,
        max_iterations=args.max_iterations,
        rule_only_difficulty=args.rule_only_difficulty,
        defer_gpt_experimental_evaluation=args.defer_gpt_experimental_evaluation,
        artifact_retention=args.artifact_retention,
        max_request_attempts_per_sample=args.max_request_attempts_per_sample,
        max_evaluations_per_sample=args.max_evaluations_per_sample,
        sample_timeout_seconds=args.sample_timeout_seconds,
        routing_mode=args.routing_mode,
        router_model=args.router_model,
        router_base_url=args.router_base_url,
        router_timeout_seconds=args.router_timeout,
        router_retries=args.router_retries,
        router_concurrency=args.router_concurrency,
        router_cache=args.router_cache or "",
        profile_model=args.profile_model,
        profile_base_url=args.profile_base_url,
        profile_concurrency=args.profile_concurrency,
    )
    final_records = runner.run(records)
    sidecars = []
    for kind, filename in (
        ("vertical_nodes", "vertical_nodes.jsonl"),
        ("operator_attempts", "operator_attempts.jsonl"),
        ("boundary_edges", "boundary_edges.jsonl"),
        ("boundary_paths", "boundary_paths.jsonl"),
    ):
        path = Path(args.work_dir) / filename
        if path.is_file():
            count = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
            sidecars.append((str(path), kind, count))
    summary_path = Path(args.work_dir) / "vertical_search_summary.json"
    sidecars.append((str(summary_path), "vertical_search_summary", 1))
    publish_records(
        final_records,
        args.output,
        stage="vertical_operator_search",
        input_path=args.input,
        config={
            "search_mode": "multi_operator_vertical_stack",
            "branch_window": args.branch_window,
            "boundary_target": args.boundary_target,
            "single_operator_boundary_target": args.single_operator_boundary_target,
            "stacked_operator_boundary_target": args.stacked_operator_boundary_target,
            "total_boundary_hard_cap": args.total_boundary_hard_cap,
            "max_depth": args.max_depth,
            "allow_operator_repeat_in_path": args.allow_operator_repeat_in_path,
            "pipeline_mode": args.pipeline_mode,
            "artifact_retention": args.artifact_retention,
            "max_request_attempts_per_sample": args.max_request_attempts_per_sample,
            "max_evaluations_per_sample": args.max_evaluations_per_sample,
            "sample_timeout_seconds": args.sample_timeout_seconds,
            "routing_mode": args.routing_mode,
            "router_model": args.router_model,
            "router_timeout": args.router_timeout,
            "router_concurrency": args.router_concurrency,
            "profile_model": args.profile_model,
            "profile_concurrency": args.profile_concurrency,
        },
        code_paths=[
            __file__,
            str(project_dir / "vertical_search.py"),
            str(project_dir / "vertical_artifacts.py"),
            str(project_dir / "multi_operator_search.py"),
        ],
        sidecars=sidecars,
    )


if __name__ == "__main__":
    main()
