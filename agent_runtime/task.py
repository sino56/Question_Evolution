"""AgentTask parsing and validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


SEARCH_MODES = {
    "auto",
    "multi_operator_branch",
    "multi_operator_vertical_stack",
    "single_branch",
}
EXECUTION_SCOPES = {
    "full_iteration",
    "reference_rebuild_only",
    "debug_generation_only",
}
REVIEW_MODES = {"none", "report_only"}
PLANNING_MODES = {"deterministic", "model_assisted"}
REGISTERED_TOOLS = {
    "check_environment",
    "run_full_loop",
    "resume_full_loop",
    "observe_experiment",
    "write_agent_report",
}


class TaskValidationError(ValueError):
    """Raised when an AgentTask is malformed or exceeds the v1 boundary."""


def _clean(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _path_within_root(value: str, root: Path, *, field_name: str) -> str:
    candidate = Path(value)
    resolved = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise TaskValidationError(f"{field_name} must be inside the project root") from exc
    return str(resolved)


@dataclass(frozen=True)
class AgentTask:
    goal: str
    input_file: str = ""
    search_mode: str = "auto"
    boundary_target: int = 5
    max_search_steps: int = 25
    execution_scope: str = "full_iteration"
    review_mode: str = "none"
    planning_mode: str = "deterministic"
    allowed_tools: List[str] = field(default_factory=lambda: sorted(REGISTERED_TOOLS))
    allow_prompt_mutation: bool = False
    allow_memory_active_publish: bool = False
    allow_global_memory_read: bool = False
    exp_root: str = "experiments"
    resume_exp_dir: str = ""
    resume_start_round: Optional[int] = None

    @property
    def is_resume(self) -> bool:
        return bool(self.resume_exp_dir)

    @property
    def is_review_only(self) -> bool:
        return self.review_mode == "report_only"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def parse_agent_task(raw: Mapping[str, Any], *, project_root: Path) -> AgentTask:
    if not isinstance(raw, Mapping):
        raise TaskValidationError("AgentTask must be a JSON object")
    goal = _clean(raw.get("goal"))
    if not goal:
        raise TaskValidationError("AgentTask.goal is required")

    search_mode = _clean(raw.get("search_mode")) or "auto"
    if search_mode not in SEARCH_MODES:
        raise TaskValidationError(f"unsupported search_mode: {search_mode}")
    execution_scope = _clean(raw.get("execution_scope")) or "full_iteration"
    if execution_scope not in EXECUTION_SCOPES:
        raise TaskValidationError(f"unsupported execution_scope: {execution_scope}")
    review_mode = _clean(raw.get("review_mode")) or "none"
    if review_mode not in REVIEW_MODES:
        raise TaskValidationError(f"unsupported review_mode: {review_mode}")
    planning_mode = _clean(raw.get("planning_mode")) or "deterministic"
    if planning_mode not in PLANNING_MODES:
        raise TaskValidationError(f"unsupported planning_mode: {planning_mode}")

    input_file = _clean(raw.get("input_file"))
    resume_exp_dir = _clean(raw.get("resume_exp_dir"))
    if not input_file and not resume_exp_dir and review_mode != "report_only":
        raise TaskValidationError("AgentTask.input_file is required for a new experiment")
    if input_file:
        input_file = _path_within_root(input_file, project_root, field_name="input_file")
    if resume_exp_dir:
        resume_exp_dir = _path_within_root(resume_exp_dir, project_root, field_name="resume_exp_dir")

    resume_start_round = raw.get("resume_start_round")
    if resume_exp_dir and review_mode != "report_only":
        if not isinstance(resume_start_round, int) or isinstance(resume_start_round, bool) or resume_start_round < 1:
            raise TaskValidationError("resume_start_round must be a positive integer for resume")
    elif resume_start_round is not None:
        raise TaskValidationError("resume_start_round is only valid with resume_exp_dir")

    for number_field, default, minimum in (
        ("boundary_target", 5, 1),
        ("max_search_steps", 25, 1),
    ):
        value = raw.get(number_field, default)
        if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
            raise TaskValidationError(f"{number_field} must be an integer >= {minimum}")

    supplied_tools = raw.get("allowed_tools", sorted(REGISTERED_TOOLS))
    if not isinstance(supplied_tools, list) or not all(isinstance(tool, str) for tool in supplied_tools):
        raise TaskValidationError("allowed_tools must be a list of registered tool names")
    allowed_tools = list(dict.fromkeys(tool.strip() for tool in supplied_tools if tool.strip()))
    invalid_tools = sorted(set(allowed_tools) - REGISTERED_TOOLS)
    if invalid_tools:
        raise TaskValidationError("unregistered allowed_tools: " + ", ".join(invalid_tools))

    for flag in ("allow_prompt_mutation", "allow_memory_active_publish", "allow_global_memory_read"):
        value = raw.get(flag, False)
        if not isinstance(value, bool):
            raise TaskValidationError(f"{flag} must be boolean")
        if flag != "allow_global_memory_read" and value:
            label = "prompt mutation" if flag == "allow_prompt_mutation" else "active Memory publication"
            raise TaskValidationError(f"{label} is outside the v1 Agent boundary")

    exp_root = _clean(raw.get("exp_root")) or "experiments"
    exp_root = _path_within_root(exp_root, project_root, field_name="exp_root")
    return AgentTask(
        goal=goal,
        input_file=input_file,
        search_mode=search_mode,
        boundary_target=raw.get("boundary_target", 5),
        max_search_steps=raw.get("max_search_steps", 25),
        execution_scope=execution_scope,
        review_mode=review_mode,
        planning_mode=planning_mode,
        allowed_tools=allowed_tools,
        allow_prompt_mutation=bool(raw.get("allow_prompt_mutation", False)),
        allow_memory_active_publish=bool(raw.get("allow_memory_active_publish", False)),
        allow_global_memory_read=bool(raw.get("allow_global_memory_read", False)),
        exp_root=exp_root,
        resume_exp_dir=resume_exp_dir,
        resume_start_round=resume_start_round,
    )


def load_agent_task(path: str | Path, *, project_root: Path) -> AgentTask:
    source = Path(path)
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TaskValidationError(f"AgentTask file does not exist: {source}") from exc
    except json.JSONDecodeError as exc:
        raise TaskValidationError(f"AgentTask JSON is invalid: {exc.msg}") from exc
    return parse_agent_task(raw, project_root=project_root)
