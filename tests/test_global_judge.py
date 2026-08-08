import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_runtime.global_judge import (
    EvidencePackRejected,
    GlobalJudgeGovernance,
    PolicyGuardRejected,
    ProposalRejected,
    PublicationRejected,
    build_evidence_pack,
    policy_guard,
    replay_holdout,
    run_global_judge,
    validate_proposal,
    write_json,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def _experiment(tmp_path: Path) -> Path:
    exp = tmp_path / "experiments" / "2026-08-08" / "exp1"
    rows = [
        {
            "sample_id": "s-1", "round": 1, "branch_id": "s-1::O16", "operator_used": "O16",
            "branch_status": "score_increased", "score_rate": 0.8,
            "operator_route": {"selected_operator": "O16", "routing_mode": "rule"},
            "sample_signature": {"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "joint_conditions"},
        },
        {
            "sample_id": "s-2", "round": 1, "branch_id": "s-2::O16", "operator_used": "O16",
            "branch_status": "score_increased", "score_rate": 0.75,
            "operator_route": {"selected_operator": "O16", "routing_mode": "rule"},
            "sample_signature": {"scene_family": "traffic", "question_form": "necessity", "reasoning_mechanism": "joint_conditions"},
        },
    ]
    _write_jsonl(exp / "round_1" / "branch_results.jsonl", rows)
    _write_jsonl(exp / "memory" / "failure_memory_bank.jsonl", [{"sample_id": "s-1", "failure_reason": "score_increased"}])
    return exp


def _digest_tree(root: Path) -> dict[str, str]:
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in root.rglob("*") if path.is_file()}


def test_evidence_pack_requires_snapshot(tmp_path: Path):
    with pytest.raises(EvidencePackRejected, match="Snapshot"):
        build_evidence_pack(_experiment(tmp_path), project_root=tmp_path)


def test_proposal_without_evidence_reference_is_rejected():
    proposal = {
        "record_type": "optimization_proposal", "proposal_id": "x", "source_evidence_pack": "ep",
        "diagnosis_level": "router", "evidence_strength": "medium", "status": "proposed",
        "verification_plan": "replay", "publish_gate": {}, "evidence_refs": [],
    }
    with pytest.raises(ProposalRejected, match="evidence"):
        validate_proposal(proposal)


def test_global_judge_is_read_only_and_shadow_cannot_change_router_input(tmp_path: Path):
    exp = _experiment(tmp_path)
    before = _digest_tree(exp)
    pack = build_evidence_pack(exp, project_root=tmp_path, snapshot={"memory_snapshot_id": "MSNAP-test"})
    report = run_global_judge(pack)
    assert _digest_tree(exp) == before
    assert report["conclusion"].startswith("Offline advisory")
    assert report["diagnoses"]
    assert all(diagnosis["diagnosis_level"] in {"operator generation", "sample/data"} for diagnosis in report["diagnoses"])
    assert report["shadow_strategy_cards"]

    live_router_input = {"recommended_operator_ids": ["O16"], "avoid_operator_ids": []}
    frozen = copy.deepcopy(live_router_input)
    shadow = report["shadow_strategy_cards"][0]
    assert shadow["status"] == "shadow"
    assert "must not alter live Router input" in shadow["action_limit"]
    assert live_router_input == frozen


def test_active_publication_requires_replay_and_independent_approval(tmp_path: Path):
    exp = _experiment(tmp_path)
    pack = build_evidence_pack(exp, project_root=tmp_path, snapshot={"memory_snapshot_id": "MSNAP-test"})
    proposal = run_global_judge(pack)["proposals"][0]
    replay = replay_holdout(pack, proposal)
    governance = GlobalJudgeGovernance(tmp_path)

    with pytest.raises(PublicationRejected, match="approval"):
        governance.publish_active(proposal, replay, {}, actor_role="publisher")
    with pytest.raises(PolicyGuardRejected, match="publisher authority"):
        governance.publish_active(proposal, replay, {"approved_by": "reviewer", "approved_at": "2026-08-08T00:00:00Z", "decision": "approve_active", "risk_acknowledgement": "reviewed"}, actor_role="global_judge")

    approval = {"approved_by": "reviewer", "approved_at": "2026-08-08T00:00:00Z", "decision": "approve_active", "risk_acknowledgement": "reviewed holdout risks"}
    published = governance.publish_active(proposal, replay, approval, actor_role="publisher")
    assert published["status"] == "active"
    assert (tmp_path / "memory_global" / "global_judge" / "publication_ledger.jsonl").exists()


def test_policy_guard_rejects_formal_mutation_attempts():
    for target in ("modify prompt", "modify Router", "write rubric", "update score", "rewrite operator"):
        with pytest.raises(PolicyGuardRejected):
            policy_guard(action=target)


def test_global_judge_output_path_cannot_target_formal_artifacts(tmp_path: Path):
    forbidden = tmp_path / "round_1" / "branch_results.jsonl"
    with pytest.raises(PolicyGuardRejected, match="reports must be written"):
        write_json(forbidden, {"record_type": "global_judge_run_report"}, project_root=tmp_path)


def test_judge_instability_is_explicitly_diagnosed(tmp_path: Path):
    exp = _experiment(tmp_path)
    _write_jsonl(
        exp / "round_2" / "branch_results.jsonl",
        [{"sample_id": "unstable", "branch_status": "score_unchanged", "round0_score_summary": {"is_stable": False}}],
    )
    report = run_global_judge(build_evidence_pack(exp, project_root=tmp_path, snapshot={"memory_snapshot_id": "MSNAP-test"}))
    assert any(row["diagnosis_kind"] == "judge_instability" and row["diagnosis_level"] == "rubric/judge" for row in report["diagnoses"])
