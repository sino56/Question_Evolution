import json
from pathlib import Path

from agent_runtime.skills import validate_skill_output
from agent_runtime.skills.skill_registry import get_skill, list_skills
from schema_validation import load_schema, validate_instance


ROOT = Path(__file__).resolve().parents[1]


def test_every_registered_skill_has_a_minimal_schema_checked_input_output_example():
    examples = ROOT / "agent_skills" / "examples"
    for spec in list_skills():
        path = examples / f"{spec.skill_id}.json"
        example = json.loads(path.read_text(encoding="utf-8"))
        assert set(example) == {"input", "expected_output"}
        assert isinstance(example["input"], dict)
        output = example["expected_output"]
        validate_instance(output, load_schema(ROOT / "schemas" / spec.output_schema), schema_dir=ROOT / "schemas")
        accepted = validate_skill_output(spec.skill_id, output)
        assert accepted["skill_id"] == spec.skill_id


def test_examples_cover_exactly_the_registered_skill_set():
    examples = {path.stem for path in (ROOT / "agent_skills" / "examples").glob("*.json")}
    assert examples == {spec.skill_id for spec in list_skills()}
