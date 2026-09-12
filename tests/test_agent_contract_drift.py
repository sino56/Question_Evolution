"""Contract-drift CI assertions.

The harness keeps its contracts in three places at once: Python constants,
JSON schemas, and the loop scripts.  Without an executable check they drift
silently -- which is exactly how ``agent_decision.schema.json`` lost ``replan``
and ``suspend`` for an unknown length of time (report V-2 / X-4).

Every assertion below compares two independently maintained sources, so a
one-sided edit fails here instead of in production.
"""

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.context_layers import TOOL_REGISTRY_ORDER
from agent_runtime.env_contract import AGENT_INJECTABLE_ENV, parse_script_declaration
from agent_runtime.observer import MANIFEST_STATUSES, OBSERVATION_TYPES, BUDGET_TERMINAL_REASONS
from agent_runtime.policy import DECISIONS, ENV_ALLOWLIST, PLAN_KINDS, _REQUIRED_STEP_FIELDS, _REQUIRED_TOOL_OUTPUTS
from agent_runtime.recovery import RECOVERY_ACTIONS
from agent_runtime.skills import list_skills
from agent_runtime.task import REGISTERED_TOOLS, SUPPORTED_EXECUTION_SCOPES
from agent_runtime.tools import TOOL_SPECS

SCHEMA_DIR = ROOT / "schemas"
MAPPING_TABLE = ROOT / "docs" / "Agent改造方案" / "设计条目-实现-测试映射表.md"


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- decisions


def test_decision_action_enum_matches_policy_and_schema():
    schema = _schema("agent_decision.schema.json")
    declared = set(schema["properties"]["action"]["enum"])

    assert declared == set(DECISIONS), f"schema/policy decision drift: {declared ^ set(DECISIONS)}"
    assert schema["additionalProperties"] is True


def test_decision_schema_accepts_the_recovery_fields_decisions_emit(tmp_path):
    from agent_runtime.decisions import decide_next_action
    from agent_runtime.task import parse_agent_task
    from schema_validation import load_schema, validate_instance

    task = parse_agent_task(
        {"goal": "g", "input_file": "data/d.jsonl", "allowed_tools": ["run_full_loop"]}, project_root=tmp_path
    )
    decision = decide_next_action(task, {"status": "observed", "score_increased_count": 1})
    schema_path = SCHEMA_DIR / "agent_decision.schema.json"
    validate_instance(decision, load_schema(schema_path), schema_dir=schema_path.parent)

    assert decision["recovery_action"] in RECOVERY_ACTIONS


# ------------------------------------------------------------- observations


def test_observation_type_enum_matches_the_runtime_set():
    schema = _schema("agent_normalized_observation.schema.json")
    declared = set(schema["properties"]["type"]["enum"])

    assert declared == set(OBSERVATION_TYPES), f"schema/runtime observation drift: {declared ^ set(OBSERVATION_TYPES)}"


def test_manifest_status_enum_covers_every_runtime_state():
    schema = _schema("agent_observation.schema.json")
    declared = set(schema["properties"]["manifest_status"]["enum"])

    assert declared == set(MANIFEST_STATUSES), f"manifest status drift: {declared ^ set(MANIFEST_STATUSES)}"


def test_budget_terminal_reasons_are_declared_in_the_schema():
    schema = _schema("agent_observation.schema.json")
    property_schema = schema["properties"]["termination_reason"]

    # The field stays an open string (the pipeline may report its own reason),
    # but the documented set must cover every budget terminal reason, so a new
    # whitelist entry cannot land without updating the published contract.
    assert set(property_schema.get("type") or []) >= {"string", "null"}
    documented = set(property_schema.get("examples") or [])
    assert documented >= set(BUDGET_TERMINAL_REASONS), f"undeclared budget reason: {set(BUDGET_TERMINAL_REASONS) - documented}"


# ------------------------------------------------------------------- tools


def test_registered_tools_match_the_tool_specs_and_the_registry_order():
    assert set(TOOL_SPECS) == set(REGISTERED_TOOLS), "tool registry drift"
    assert set(TOOL_REGISTRY_ORDER) == set(REGISTERED_TOOLS), "context registry order drift"
    assert set(_REQUIRED_TOOL_OUTPUTS) <= set(REGISTERED_TOOLS)


def test_every_tool_spec_points_at_an_existing_schema():
    for name, spec in TOOL_SPECS.items():
        for schema_name in (spec.input_schema, spec.output_schema):
            assert (SCHEMA_DIR / schema_name).is_file(), f"{name} references a missing schema: {schema_name}"


# ------------------------------------------------------------------- plans


def test_plan_schema_step_contract_covers_the_policy_requirements():
    schema = _schema("agent_plan.schema.json")
    step_required = set(schema["properties"]["steps"]["items"]["required"])

    assert step_required >= set(_REQUIRED_STEP_FIELDS), f"plan step drift: {set(_REQUIRED_STEP_FIELDS) - step_required}"
    assert set(schema["properties"]["plan_kind"]["enum"]) == set(PLAN_KINDS)
    assert set(schema["properties"]["selected_execution_scope"]["enum"]) >= set(SUPPORTED_EXECUTION_SCOPES)


# ------------------------------------------------------------------- skills


def test_every_skill_declares_an_existing_output_schema():
    specs = list_skills()
    assert specs, "no Skill is registered"

    for spec in specs:
        assert spec.output_schema, f"{spec.skill_id} declares no output schema"
        assert (SCHEMA_DIR / spec.output_schema).is_file(), f"{spec.skill_id} references a missing schema: {spec.output_schema}"
        assert spec.allowed_context_layers, f"{spec.skill_id} declares no allowed context layer"


# -------------------------------------------------- cross-layer environment


def test_env_allowlist_and_both_loop_scripts_declare_the_same_set():
    assert set(ENV_ALLOWLIST) == set(AGENT_INJECTABLE_ENV)

    for script in ("run_loop.sh", "run_loop.ps1"):
        text = (ROOT / script).read_text(encoding="utf-8-sig")
        declared = parse_script_declaration(text)
        assert declared == set(AGENT_INJECTABLE_ENV), f"{script} environment declaration drift: {declared ^ set(AGENT_INJECTABLE_ENV)}"


def test_loop_scripts_expose_an_opt_in_agent_switch_with_a_recursion_guard():
    shell = (ROOT / "run_loop.sh").read_text(encoding="utf-8")
    powershell = (ROOT / "run_loop.ps1").read_text(encoding="utf-8-sig")

    assert "--agent" in shell and "question_evolution_agent.py" in shell
    assert "QE_AGENT_INNER" in shell
    assert "$Agent" in powershell and "question_evolution_agent.py" in powershell
    assert "QE_AGENT_INNER" in powershell
    # The switch is opt-in: the default must stay off.
    assert "AGENT_MODE=${AGENT_MODE:-false}" in shell


def test_tool_registry_marks_inner_loop_invocations(tmp_path, monkeypatch):
    import subprocess

    from agent_runtime.env_contract import INNER_LOOP_MARKER
    from agent_runtime.task import parse_agent_task
    from agent_runtime.tools import ToolRegistry

    captured = {}

    def runner(command, **kwargs):
        captured.update(kwargs.get("env") or {})
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr("agent_runtime.tools.shutil.which", lambda _: "bash")
    task = parse_agent_task({"goal": "g", "input_file": "data/d.jsonl", "allowed_tools": ["run_full_loop"]}, project_root=tmp_path)
    ToolRegistry(project_root=tmp_path, run_dir=tmp_path / "run", runner=runner).run_full_loop(task, {"EXP_ROOT": str(tmp_path / "experiments")})

    assert captured[INNER_LOOP_MARKER] == "1"


# ------------------------------------------------------------- X-4 mapping


def test_design_mapping_table_exists_and_references_real_paths():
    import re

    assert MAPPING_TABLE.is_file(), "the design/implementation/test mapping table is missing"
    text = MAPPING_TABLE.read_text(encoding="utf-8")

    # Backticked spans may list several paths separated by "、", so split each
    # span before validating. Only repository-relative paths are checked.
    #
    # Runtime artifacts are produced inside an experiment directory during a
    # run, so they legitimately have no repository counterpart and are exempt.
    runtime_artifacts = {"resolved_env.json", "experiment_dir.txt"}
    candidates: set[str] = set()
    for span in re.findall(r"`([^`]+)`", text):
        for token in re.split(r"[、,;\s]+", span):
            candidate = token.strip().strip("`")
            if not candidate or candidate.startswith(("http", "/", "$")):
                continue
            if candidate in runtime_artifacts:
                continue
            if re.fullmatch(r"[\w./\\\u4e00-\u9fff-]+\.(py|json|sh|ps1|md)", candidate):
                candidates.add(candidate.replace("\\", "/"))

    assert len(candidates) >= 15, f"the mapping table references too few paths: {sorted(candidates)}"
    missing = sorted(candidate for candidate in candidates if not (ROOT / candidate).is_file())
    assert not missing, f"the mapping table references missing paths: {missing}"


def test_mapping_table_covers_every_defect_id_tracked_by_the_plan():
    text = MAPPING_TABLE.read_text(encoding="utf-8")
    expected = [
        "R-1", "R-2", "R-3", "R-4", "R-5", "R-6",
        "M-1", "M-2", "M-3", "M-4", "M-5", "M-6", "M-7", "M-8", "M-9", "M-10",
        "V-1", "V-2", "V-3", "V-4", "V-5", "V-6", "V-8",
        "C-1", "C-2", "C-3", "C-5", "C-6", "C-7",
        "O-1", "O-2", "O-3", "O-4", "O-6", "O-7", "O-8", "O-9",
        "T-1", "T-2", "T-3", "T-4", "T-5", "T-6", "T-7", "T-8",
        "X-1", "X-3", "X-4", "X-5",
    ]
    absent = [identifier for identifier in expected if identifier not in text]
    assert not absent, f"the mapping table omits: {absent}"
