import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "artifact_cli.py"), *map(str, args)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_prepare_validate_and_copy_published_artifact(tmp_path):
    source = tmp_path / "source.jsonl"
    prepared = tmp_path / "round_input.jsonl"
    exported = tmp_path / "final" / "final_scored.jsonl"
    source.write_text(json.dumps({"sample_id": "a", "prompt": "题目"}, ensure_ascii=False) + "\n", encoding="utf-8")

    result = run_cli(
        "prepare-round-input",
        "--input",
        source,
        "--output",
        prepared,
        "--round",
        "3",
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(prepared.read_text(encoding="utf-8"))
    assert record["round"] == 3

    validated = run_cli(
        "validate",
        "--output",
        prepared,
        "--stage",
        "prepare_round_input",
        "--input",
        source,
    )
    assert validated.returncode == 0, validated.stderr

    copied = run_cli("copy-published", "--input", prepared, "--output", exported)
    assert copied.returncode == 0, copied.stderr
    assert exported.read_bytes() == prepared.read_bytes()
    assert Path(str(exported) + ".manifest.json").exists()


def test_validate_rejects_tampered_formal_output(tmp_path):
    source = tmp_path / "source.jsonl"
    prepared = tmp_path / "round_input.jsonl"
    source.write_text('{"sample_id":"a"}\n', encoding="utf-8")
    assert run_cli(
        "prepare-round-input", "--input", source, "--output", prepared, "--round", "0"
    ).returncode == 0
    prepared.write_text('{"sample_id":"tampered"}\n', encoding="utf-8")
    result = run_cli(
        "validate",
        "--output",
        prepared,
        "--stage",
        "prepare_round_input",
        "--input",
        source,
    )
    assert result.returncode == 1
    assert "mismatch" in result.stderr
