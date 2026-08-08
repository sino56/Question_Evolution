"""Load and enforce the narrow runtime contract of registered Agent Skills."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from schema_validation import SchemaValidationError, load_schema, validate_instance

from ..events import append_event
from .skill_registry import GLOBAL_FORBIDDEN_ACTIONS, SKILL_ROOT, SkillSpec, get_skill, list_skills


REQUIRED_SECTIONS = (
    "Applicable scenarios",
    "Input materials",
    "Prohibited actions",
    "Workflow",
    "Output structure",
    "Failure fallback",
    "Acceptance criteria",
)
FORBIDDEN_CONTEXT_MARKERS = ("complete_parent_context", "complete_experiment_directory", "complete_model_response", "full_memory")


class SkillRequestRejected(ValueError):
    """A caller asked a Skill to exceed its declared read-only contract."""


class SkillOutputRejected(ValueError):
    """A Skill output cannot enter an auditable downstream workflow."""


@dataclass(frozen=True)
class LoadedSkill:
    spec: SkillSpec
    content: str


@dataclass(frozen=True)
class SkillLoadResult:
    stage: str
    loaded: tuple[LoadedSkill, ...]
    fallback_to_base_rules: bool
    failures: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "loaded_skill_ids": [item.spec.skill_id for item in self.loaded],
            "fallback_to_base_rules": self.fallback_to_base_rules,
            "failures": list(self.failures),
        }


def _validate_document(spec: SkillSpec, content: str) -> None:
    if not content.startswith(f"# {spec.skill_id}\n"):
        raise SkillRequestRejected("skill document title does not match its registry id")
    for number, section in enumerate(REQUIRED_SECTIONS, start=1):
        if f"## {number}. {section}" not in content:
            raise SkillRequestRejected(f"skill document is missing required section: {section}")
    if "evidence_refs" not in content and "artifact_refs" not in content:
        raise SkillRequestRejected("skill document must require auditable evidence references")


def validate_skill_request(
    skill_id: str,
    *,
    requested_context_layers: Iterable[str],
    requested_actions: Iterable[str] = (),
) -> SkillSpec:
    spec = get_skill(skill_id)
    requested_layers = {str(item) for item in requested_context_layers}
    forbidden_layers = requested_layers - set(spec.allowed_context_layers)
    forbidden_layers |= requested_layers & set(FORBIDDEN_CONTEXT_MARKERS)
    if forbidden_layers:
        raise SkillRequestRejected("skill requested forbidden context layer(s): " + ", ".join(sorted(forbidden_layers)))
    requested = {str(item) for item in requested_actions}
    disallowed = requested & (set(spec.forbidden_actions) | GLOBAL_FORBIDDEN_ACTIONS)
    if disallowed:
        raise SkillRequestRejected("skill requested forbidden action(s): " + ", ".join(sorted(disallowed)))
    return spec


def _append(path: Path | None, event_type: str, payload: Mapping[str, Any]) -> None:
    if path is not None:
        append_event(path, event_type, payload)


def load_stage_skills(
    stage: str,
    *,
    requested_context_layers: Iterable[str],
    available_inputs: Iterable[str] | None = None,
    event_path: str | Path | None = None,
    skill_root: Path = SKILL_ROOT,
) -> SkillLoadResult:
    """Load every registered stage procedure; errors fall back to base rules.

    The result carries no extra authority.  Callers use it only to record that
    their normal safe implementation is following the available procedure.
    """

    target = Path(event_path) if event_path is not None else None
    loaded: list[LoadedSkill] = []
    failures: list[str] = []
    for spec in list_skills(stage=stage):
        try:
            validate_skill_request(spec.skill_id, requested_context_layers=requested_context_layers)
            if available_inputs is not None:
                missing = set(spec.required_inputs) - {str(item) for item in available_inputs}
                if missing:
                    raise SkillRequestRejected("required input material is unavailable: " + ", ".join(sorted(missing)))
            content = (skill_root / spec.skill_id / "SKILL.md").read_text(encoding="utf-8")
            _validate_document(spec, content)
            loaded.append(LoadedSkill(spec=spec, content=content))
            _append(target, "skill_loaded", {"skill_id": spec.skill_id, "stage": stage, "skill_version": spec.version})
        except (OSError, ValueError, SkillRequestRejected) as exc:
            failures.append(f"{spec.skill_id}: {exc}")
            _append(target, "skill_load_failed", {"skill_id": spec.skill_id, "stage": stage, "error_summary": str(exc)[:1000], "fallback": "base_rules"})
    return SkillLoadResult(stage=stage, loaded=tuple(loaded), fallback_to_base_rules=bool(failures), failures=tuple(failures))


def validate_skill_output(skill_id: str, output: Mapping[str, Any]) -> dict[str, Any]:
    """Reject unauditable Skill output before it reaches a report or proposal."""

    spec = get_skill(skill_id)
    if not isinstance(output, Mapping):
        raise SkillOutputRejected("skill output must be an object")
    value = dict(output)
    value.setdefault("skill_id", skill_id)
    if value["skill_id"] != skill_id:
        raise SkillOutputRejected("skill output skill_id does not match registry")
    if value.get("status") == "active":
        raise SkillOutputRejected("Skill output cannot publish active state")
    if value.get("confirmed_boundary") is True:
        raise SkillOutputRejected("Skill output cannot replace human boundary confirmation")
    refs = value.get("evidence_refs") or value.get("artifact_refs") or []
    if not isinstance(refs, list) or not refs:
        raise SkillOutputRejected("skill output requires evidence_refs or artifact_refs")
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    if len(serialized) > 12000:
        raise SkillOutputRejected("skill output exceeds the audit-safe size limit")
    forbidden = set(value.get("requested_actions") or []) & (set(spec.forbidden_actions) | GLOBAL_FORBIDDEN_ACTIONS)
    if forbidden:
        raise SkillOutputRejected("skill output requests forbidden action(s): " + ", ".join(sorted(forbidden)))
    schema_path = Path(__file__).resolve().parents[2] / "schemas" / spec.output_schema
    try:
        validate_instance(value, load_schema(schema_path), schema_dir=schema_path.parent)
    except SchemaValidationError as exc:
        raise SkillOutputRejected(f"skill output violates {spec.output_schema}: {exc}") from exc
    return value
