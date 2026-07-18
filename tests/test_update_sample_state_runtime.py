import json
from pathlib import Path

import pytest

import update_sample_state


def _read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_append_unique_jsonl_is_retry_safe(tmp_path: Path):
    output = tmp_path / "memory.jsonl"
    records = [{"sample_id": "a", "round": 1}, {"sample_id": "b", "round": 1}]

    assert update_sample_state.append_unique_jsonl(records, str(output)) == 2
    assert update_sample_state.append_unique_jsonl(records, str(output)) == 0
    assert _read_jsonl(output) == records


def test_memory_failure_prevents_formal_state_publish(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source = tmp_path / "effect.jsonl"
    source.write_text(json.dumps({"sample_id": "a", "prompt": "q"}) + "\n", encoding="utf-8")
    output = tmp_path / "state.jsonl"

    monkeypatch.setattr(
        update_sample_state,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "input": str(source),
                "output": str(output),
                "memory_dir": str(tmp_path / "memory"),
                "operator_memory": None,
                "failure_memory": None,
                "invalid_output": None,
                "preselection_invalid_input": None,
                "report_output": None,
                "no_memory_output": False,
                "performance_events": None,
            },
        )(),
    )
    monkeypatch.setattr(
        update_sample_state,
        "update_records",
        lambda records: ([dict(records[0])], [{"sample_id": "a"}], [], []),
    )
    monkeypatch.setattr(
        update_sample_state,
        "append_unique_jsonl",
        lambda records, path: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        update_sample_state.main()
    assert not output.exists()
    assert not Path(f"{output}.manifest.json").exists()
