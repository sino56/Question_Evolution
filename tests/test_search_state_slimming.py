import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from branch_artifacts import BranchArtifactStore
from search_coordinator import upgrade_search_state, upgrade_search_state_with_artifacts
from update_sample_state import append_unique_jsonl


def test_legacy_full_branches_are_extracted_from_lightweight_state():
    legacy = {
        "parent_node_id": "p",
        "selected_operator_ids": ["O10_evidence_sufficiency_ladder"],
        "operator_plan": [
            {
                "operator_id": "O10_evidence_sufficiency_ladder",
                "branch_id": "p::O10_evidence_sufficiency_ladder",
                "status": "completed",
                "branch_stage": "completed",
            }
        ],
        "branches": [
            {
                "branch_id": "p::O10_evidence_sufficiency_ladder",
                "prompt": "large prompt",
                "candidate_answer": "large answer",
            }
        ],
    }
    state, artifacts = upgrade_search_state_with_artifacts(legacy)
    assert "branches" not in state
    assert "large prompt" not in json.dumps(state, ensure_ascii=False)
    assert artifacts[0]["candidate_answer"] == "large answer"


def test_append_only_branch_store_is_idempotent_and_refuses_version_mixing(tmp_path):
    path = tmp_path / "branch_results.jsonl"
    store = BranchArtifactStore(path)
    record = {
        "branch_id": "p::O10",
        "parent_node_id": "p",
        "prompt": "prompt",
        "candidate_answer": "answer",
    }
    assert store.append(record, "complete_branch") is True
    assert store.append({**record, "candidate_answer": "changed"}, "complete_branch") is False
    assert len(list(store.iter_rows())) == 1
    with pytest.raises(ValueError, match="format version mismatch"):
        BranchArtifactStore(path, format_version=2)


def test_search_state_refuses_future_version_mixing():
    with pytest.raises(ValueError, match="search state version mismatch"):
        upgrade_search_state(
            {
                "search_state_version": 2,
                "parent_node_id": "p",
                "selected_operator_ids": [],
            }
        )


def test_full_payload_growth_does_not_expand_lightweight_state(tmp_path):
    path = tmp_path / "branches.jsonl"
    store = BranchArtifactStore(path)
    state = {
        "search_state_version": 1,
        "parent_node_id": "p",
        "branch_summaries": {},
    }
    huge_answer = "x" * 10000
    for index in range(100):
        branch_id = f"p::O{index}"
        store.append(
            {
                "branch_id": branch_id,
                "parent_node_id": "p",
                "candidate_answer": huge_answer,
            },
            "complete_branch",
        )
        state["branch_summaries"][branch_id] = {
            "branch_id": branch_id,
            "branch_status": "no_score_change",
            "child_score_rate": 0.8,
        }
    assert len(json.dumps(state)) < 20000
    assert path.stat().st_size > 1_000_000


def test_memory_idempotency_key_blocks_changed_duplicate_replay(tmp_path):
    output = tmp_path / "memory.jsonl"
    first = {
        "branch_id": "p::O10",
        "memory_idempotency_key": "p::O10::failure",
        "failure_reason": "first",
    }
    changed_replay = {
        **first,
        "failure_reason": "changed after recovery",
    }
    assert append_unique_jsonl([first], str(output)) == 1
    assert append_unique_jsonl([changed_replay], str(output)) == 0
    assert len(output.read_text(encoding="utf-8").splitlines()) == 1
