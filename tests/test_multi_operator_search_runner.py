import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import multi_operator_search
from pipeline_runtime import validate_published_artifact
from search_coordinator import (
    initialize_search_state,
    mark_experimental_evaluation_completed,
    mark_experimental_evaluation_finished,
    merge_decision_result,
)


def make_runner(tmp_path, **overrides):
    options = {
        "project_dir": ROOT,
        "work_dir": tmp_path / "search",
        "memory_dir": tmp_path / "memory",
        "branch_window": 1,
        "boundary_target": 5,
        "operator_sort_mode": "route",
        "operator_statistics": None,
        "exploration_ratio": 0.1,
        "max_iterations": 100,
        "rule_only_difficulty": False,
        "defer_gpt_experimental_evaluation": True,
    }
    options.update(overrides)
    return multi_operator_search.MultiOperatorSearchRunner(**options)


def test_runner_publishes_terminal_no_candidate_state_without_model_calls(tmp_path, monkeypatch):
    input_path = tmp_path / "routed.jsonl"
    output_path = tmp_path / "search_state.jsonl"
    work_dir = tmp_path / "search"
    input_path.write_text(
        json.dumps(
            {
                "sample_id": "pass-through",
                "prompt": "parent",
                "score_rate": 0.5,
                "evolution_action": "pass_through_or_scoring_noise",
                "operator_route": {
                    "primary_operator": None,
                    "backup_operators": [],
                    "avoid_operators": [],
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "multi_operator_search.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
            "--work-dir",
            str(work_dir),
            "--memory-dir",
            str(tmp_path / "memory"),
            "--branch-window",
            "3",
        ],
    )

    multi_operator_search.main()

    record = json.loads(output_path.read_text(encoding="utf-8"))
    assert record["search_state"]["status"] == "completed"
    assert record["search_state"]["termination_reason"] == "candidate_list_exhausted"
    valid, reason = validate_published_artifact(
        str(output_path),
        stage="multi_operator_search",
        input_path=str(input_path),
        config={
            "branch_window": 3,
            "boundary_target": 5,
            "pipeline_mode": "step",
            "operator_sort_mode": "route",
            "exploration_ratio": 0.1,
            "max_iterations": 1000,
            "rule_only_difficulty": False,
            "defer_gpt_experimental_evaluation": False,
            "artifact_retention": "compact",
        },
    )
    assert valid, reason
    assert (work_dir / "search_summary.json").exists()
    assert list(work_dir.glob("wave_[0-9][0-9][0-9][0-9]")) == []


def test_full_and_terminal_branch_artifacts_match_the_declared_schema():
    schema = json.loads(
        (ROOT / "schemas" / "branch_result.schema.json").read_text(encoding="utf-8")
    )
    complete = multi_operator_search._complete_branch_artifact(
        {
            "branch_id": "parent::O1",
            "parent_node_id": "parent",
            "candidate_operator": "O1",
            "parent_score_rate": 0.8,
            "score_rate": 0.4,
            "decision_evaluation_status": "completed",
            "experimental_evaluation_status": "completed",
        }
    )
    terminal = multi_operator_search._terminal_branch_artifact(
        {
            "branch_id": "parent::O2",
            "parent_node_id": "parent",
            "candidate_operator": "O2",
            "parent_score_rate": 0.8,
        },
        branch_status="validation_failed",
        reason="invalid",
    )

    for record in (complete, terminal):
        assert set(schema["required"]).issubset(record)
        for field in (
            "branch_status",
            "decision_evaluation_status",
            "experimental_evaluation_status",
        ):
            assert record[field] in schema["properties"][field]["enum"]
    assert complete["branch_status"] == "boundary_candidate"
    assert terminal["decision_evaluation_status"] == "failed"


def test_experimental_completion_clears_the_lightweight_pending_count():
    state = initialize_search_state(
        {
            "sample_id": "sample",
            "prompt": "parent",
            "score_rate": 0.8,
            "operator_route": {
                "selected_operator_ids": ["O10_evidence_sufficiency_ladder"],
                "primary_operator": "O10_evidence_sufficiency_ladder",
                "backup_operators": [],
            },
        },
        branch_window=1,
        boundary_target=1,
    )
    branch_id = state["operator_plan"][0]["branch_id"]
    state["operator_plan"][0]["status"] = "running"
    state = merge_decision_result(
        state,
        {
            "branch_id": branch_id,
            "parent_score_rate": 0.8,
            "score_rate": 0.4,
            "experimental_evaluation_status": "pending",
        },
    )
    assert state["experimental_evaluation_pending_count"] == 1

    completed = mark_experimental_evaluation_completed(state, branch_id)

    assert completed["experimental_evaluation_pending_count"] == 0
    assert (
        completed["branch_summaries"][branch_id][
            "experimental_evaluation_status"
        ]
        == "completed"
    )

    failed_state = initialize_search_state(
        {
            "sample_id": "failed",
            "prompt": "parent",
            "score_rate": 0.8,
            "operator_route": {
                "primary_operator": "O10_evidence_sufficiency_ladder",
                "backup_operators": [],
            },
        }
    )
    failed_branch = failed_state["operator_plan"][0]["branch_id"]
    failed_state["operator_plan"][0]["status"] = "running"
    failed_state = merge_decision_result(
        failed_state,
        {
            "branch_id": failed_branch,
            "parent_score_rate": 0.8,
            "score_rate": 0.4,
            "experimental_evaluation_status": "pending",
        },
    )
    failed_state = mark_experimental_evaluation_finished(
        failed_state,
        failed_branch,
        status="failed",
    )
    assert failed_state["experimental_evaluation_pending_count"] == 0
    assert (
        failed_state["branch_summaries"][failed_branch][
            "experimental_evaluation_status"
        ]
        == "failed"
    )


def test_stream_mode_production_runner_refills_and_publishes_complete_branches(
    tmp_path,
    monkeypatch,
):
    runner = make_runner(tmp_path)
    operators = [
        "O10_evidence_sufficiency_ladder",
        "O11_unobserved_state_attribution",
    ]
    parent = {
        "sample_id": "stream",
        "prompt": "parent",
        "score_rate": 1.0,
        "evolution_action": "evolve_high_score_overscore",
        "operator_route": {
            "primary_operator": operators[0],
            "backup_operators": operators[1:],
            "avoid_operators": [],
        },
    }

    def fake_generate(branch_dir, _name, records):
        branch_dir.mkdir(parents=True, exist_ok=True)
        (branch_dir / "generation_stage.jsonl").write_text(
            "x" * 100_000,
            encoding="utf-8",
        )
        row = dict(records[0])
        row["prompt"] = f"candidate::{row['candidate_operator']}"
        row["question_evolved"] = True
        return [row]

    monkeypatch.setattr(runner, "_generate_batch", fake_generate)
    monkeypatch.setattr(runner, "_stream_validation", lambda row: dict(row))
    monkeypatch.setattr(runner, "_stream_reference_answer", lambda row: dict(row))
    monkeypatch.setattr(runner, "_stream_rubric", lambda row: dict(row))
    monkeypatch.setattr(
        runner,
        "_stream_decision",
        lambda row: {
            **row,
            "score_rate": (
                0.5
                if row["candidate_operator"] == operators[0]
                else 1.0
            ),
            "decision_evaluation_status": "completed",
            "experimental_evaluation_status": "pending",
        },
    )
    monkeypatch.setattr(
        runner,
        "_stream_experimental",
        lambda row: {
            **row,
            "experimental_evaluation_status": "completed",
        },
    )

    final = runner.run_stream([parent])

    state = final[0]["search_state"]
    assert state["attempted_selected_operator_ids"] == operators
    assert state["boundary_candidate_count"] == 1
    assert state["termination_reason"] == "candidate_list_exhausted"
    assert state["experimental_evaluation_pending_count"] == 0
    artifacts = list(runner.artifacts.iter_rows())
    assert len(artifacts) == 2
    assert {row["record"]["branch_status"] for row in artifacts} == {
        "boundary_candidate",
        "no_score_change",
    }
    stream_branches = runner.work_dir / "stream_branches"
    assert stream_branches.exists()
    assert list(stream_branches.iterdir()) == []
    checkpoint_dirs = list((runner.work_dir / "stream_checkpoints").iterdir())
    assert len(checkpoint_dirs) == 2
    assert all(
        [path.name for path in checkpoint_dir.iterdir()] == ["final.json"]
        for checkpoint_dir in checkpoint_dirs
    )


def test_compact_retention_removes_only_scoped_intermediates(tmp_path):
    runner = make_runner(tmp_path, artifact_retention="compact")
    branch_record = {"branch_id": "parent::O10", "candidate_id": "parent::O10"}
    branch_dir = runner._stream_branch_dir(branch_record)
    branch_dir.mkdir(parents=True)
    (branch_dir / "large_stage.jsonl").write_text("x" * 100_000, encoding="utf-8")
    retained = runner.work_dir / "branch_results.jsonl"
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_text("canonical\n", encoding="utf-8")

    runner._cleanup_stream_branch(branch_record)

    assert not branch_dir.exists()
    assert retained.read_text(encoding="utf-8") == "canonical\n"


def test_full_retention_keeps_stream_branch_and_step_wave(tmp_path):
    runner = make_runner(tmp_path, artifact_retention="full")
    branch_record = {"branch_id": "parent::O10", "candidate_id": "parent::O10"}
    branch_dir = runner._stream_branch_dir(branch_record)
    branch_dir.mkdir(parents=True)
    wave_dir = runner.work_dir / "wave_0001"
    wave_dir.mkdir(parents=True)

    runner._cleanup_stream_branch(branch_record)
    runner.cleanup_published_intermediates("step")

    assert branch_dir.exists()
    assert wave_dir.exists()


def test_compact_retention_removes_step_waves_after_publication(tmp_path):
    runner = make_runner(tmp_path, artifact_retention="compact")
    for name in ("wave_0001", "wave_0020"):
        wave_dir = runner.work_dir / name
        wave_dir.mkdir(parents=True)
        (wave_dir / "stage.jsonl").write_text("large", encoding="utf-8")
    unrelated = runner.work_dir / "wave_notes"
    unrelated.mkdir(parents=True)

    runner.cleanup_published_intermediates("step")

    assert not (runner.work_dir / "wave_0001").exists()
    assert not (runner.work_dir / "wave_0020").exists()
    assert unrelated.exists()


def test_cleanup_failure_does_not_fail_a_durable_branch(tmp_path, monkeypatch):
    runner = make_runner(tmp_path, artifact_retention="compact")
    branch_record = {"branch_id": "parent::O10", "candidate_id": "parent::O10"}
    branch_dir = runner._stream_branch_dir(branch_record)
    branch_dir.mkdir(parents=True)

    def fail_cleanup(_path):
        raise PermissionError("synthetic file lock")

    monkeypatch.setattr(multi_operator_search.shutil, "rmtree", fail_cleanup)

    runner._cleanup_stream_branch(branch_record)

    assert branch_dir.exists()
    warnings = json.loads(
        (runner.work_dir / "artifact_cleanup_warnings.json").read_text(
            encoding="utf-8"
        )
    )
    assert warnings[0]["path"] == str(branch_dir.resolve())
    assert "synthetic file lock" in warnings[0]["error"]
