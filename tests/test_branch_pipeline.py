import asyncio
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from branch_pipeline import ALL_STAGES, BranchCheckpointStore, BranchPipeline


def branch(branch_id, parent_id="parent", sequence=1):
    return {
        "branch_id": branch_id,
        "candidate_id": branch_id,
        "parent_node_id": parent_id,
        "search_dispatch": {
            "generation_sequence": sequence,
            "sibling_generation_serial": True,
        },
    }


def make_handlers(calls, order, *, experimental_gate=None):
    handlers = {}
    for stage in ALL_STAGES:
        async def handler(record, current=stage):
            calls[(record["branch_id"], current)] += 1
            if current == "experimental" and experimental_gate is not None:
                await experimental_gate.wait()
            await asyncio.sleep(0)
            result = dict(record)
            result.setdefault("visited", [])
            result["visited"] = list(result["visited"]) + [current]
            if current == "decision":
                result["decision_evaluation_status"] = "completed"
                result["experimental_evaluation_status"] = "pending"
                result["score_rate"] = 0.5
                result["branch_status"] = "boundary_candidate"
            if current == "experimental":
                result["experimental_evaluation_status"] = "completed"
            order.append((record["branch_id"], current))
            return result
        handlers[stage] = handler
    return handlers


def test_decision_advances_search_before_experimental_completion(tmp_path):
    calls = Counter()
    order = []
    decisions = []
    completed = []
    gate = asyncio.Event()

    async def run():
        pipeline = BranchPipeline(
            handlers=make_handlers(calls, order, experimental_gate=gate),
            checkpoint_dir=tmp_path / "checkpoints",
            on_decision=lambda record: _append_async(decisions, record),
            on_complete=lambda record: _append_async(completed, record),
            worker_counts={stage: 2 for stage in ALL_STAGES},
            queue_size=2,
        )
        task = asyncio.create_task(pipeline.run([branch("p::O10")]))
        for _ in range(100):
            if decisions:
                break
            await asyncio.sleep(0.001)
        assert decisions
        assert not completed
        gate.set()
        summary = await task
        return summary

    summary = asyncio.run(run())
    assert completed[0]["experimental_evaluation_status"] == "completed"
    assert summary["decision_evaluations_completed"] == 1
    assert summary["branches_completed"] == 1


async def _append_async(target, record):
    target.append(record)


def test_recovery_skips_every_confirmed_stage_and_keeps_branch_identity(tmp_path):
    calls = Counter()
    order = []
    decisions = []
    completed = []
    record = branch("p::O11")
    store = BranchCheckpointStore(tmp_path / "checkpoints")
    checkpoint_record = dict(record)
    checkpoint_record["visited"] = ["generation", "validation", "reference_answer"]
    store.write(record["branch_id"], "generation", checkpoint_record)
    store.write(record["branch_id"], "validation", checkpoint_record)
    store.write(record["branch_id"], "reference_answer", checkpoint_record)

    pipeline = BranchPipeline(
        handlers=make_handlers(calls, order),
        checkpoint_dir=tmp_path / "checkpoints",
        on_decision=lambda row: _append_async(decisions, row),
        on_complete=lambda row: _append_async(completed, row),
    )
    asyncio.run(pipeline.run([record]))

    assert calls[("p::O11", "generation")] == 0
    assert calls[("p::O11", "validation")] == 0
    assert calls[("p::O11", "reference_answer")] == 0
    assert calls[("p::O11", "rubric")] == 1
    assert calls[("p::O11", "decision")] == 1
    assert completed[0]["branch_id"] == "p::O11"

    second_calls = Counter()
    replayed_decisions = []
    replayed_completions = []
    second_pipeline = BranchPipeline(
        handlers=make_handlers(second_calls, []),
        checkpoint_dir=tmp_path / "checkpoints",
        on_decision=lambda row: _append_async(replayed_decisions, row),
        on_complete=lambda row: _append_async(replayed_completions, row),
    )
    asyncio.run(second_pipeline.run([record]))
    assert sum(second_calls.values()) == 0
    assert replayed_decisions[0]["branch_id"] == "p::O11"
    assert replayed_completions[0]["branch_id"] == "p::O11"


def test_sibling_generation_is_serial_and_ordered_while_downstream_overlaps(tmp_path):
    calls = Counter()
    order = []
    completed = []
    handlers = make_handlers(calls, order)
    pipeline = BranchPipeline(
        handlers=handlers,
        checkpoint_dir=tmp_path / "checkpoints",
        on_decision=lambda row: _append_async([], row),
        on_complete=lambda row: _append_async(completed, row),
        worker_counts={stage: 3 for stage in ALL_STAGES},
        queue_size=3,
    )
    rows = [
        branch("p::O10", sequence=1),
        branch("p::O11", sequence=2),
        branch("p::O12", sequence=3),
    ]
    asyncio.run(pipeline.run(rows))

    generation_order = [
        branch_id for branch_id, stage in order if stage == "generation"
    ]
    assert generation_order == ["p::O10", "p::O11", "p::O12"]
    assert len(completed) == 3


def test_failed_generation_does_not_deadlock_later_sibling(tmp_path):
    calls = Counter()
    order = []
    completed = []
    errors = []
    handlers = make_handlers(calls, order)
    normal_generation = handlers["generation"]

    async def fail_first(record):
        if record["branch_id"] == "p::O10":
            raise RuntimeError("synthetic generation failure")
        return await normal_generation(record)

    handlers["generation"] = fail_first
    pipeline = BranchPipeline(
        handlers=handlers,
        checkpoint_dir=tmp_path / "checkpoints",
        on_decision=lambda row: _append_async([], row),
        on_complete=lambda row: _append_async(completed, row),
        on_error=lambda row: _append_async(errors, row),
        worker_counts={stage: 2 for stage in ALL_STAGES},
        queue_size=2,
    )

    summary = asyncio.run(
        pipeline.run(
            [
                branch("p::O10", sequence=1),
                branch("p::O11", sequence=2),
            ]
        )
    )

    assert errors[0]["branch_id"] == "p::O10"
    assert completed[0]["branch_id"] == "p::O11"
    assert summary["branches_completed"] == 1
    assert summary["model_error_rate"] == 0.5


def test_dynamic_submit_can_fill_a_window_after_first_decision(tmp_path):
    calls = Counter()
    decisions = []
    completed = []

    async def run():
        pipeline = BranchPipeline(
            handlers=make_handlers(calls, []),
            checkpoint_dir=tmp_path / "checkpoints",
            on_decision=lambda row: _append_async(decisions, row),
            on_complete=lambda row: _append_async(completed, row),
            worker_counts={stage: 2 for stage in ALL_STAGES},
            queue_size=2,
        )
        await pipeline.start()
        await pipeline.submit([branch("p::O10", sequence=1)])
        outcome = await pipeline.wait_for_outcome()
        assert outcome["branch_id"] == "p::O10"
        await pipeline.submit([branch("p::O11", sequence=2)])
        second_outcome = await pipeline.wait_for_outcome()
        assert second_outcome["branch_id"] == "p::O11"
        return await pipeline.finish()

    summary = asyncio.run(run())
    assert len(decisions) == 2
    assert len(completed) == 2
    assert summary["decision_evaluations_completed"] == 2


def test_experimental_failure_is_explicit_and_does_not_erase_decision(tmp_path):
    calls = Counter()
    decisions = []
    completed = []
    handlers = make_handlers(calls, [])

    async def fail_experimental(record):
        raise RuntimeError("synthetic GPT failure")

    handlers["experimental"] = fail_experimental
    pipeline = BranchPipeline(
        handlers=handlers,
        checkpoint_dir=tmp_path / "checkpoints",
        on_decision=lambda row: _append_async(decisions, row),
        on_complete=lambda row: _append_async(completed, row),
    )

    summary = asyncio.run(pipeline.run([branch("p::O12")]))

    assert decisions[0]["decision_evaluation_status"] == "completed"
    assert completed[0]["experimental_evaluation_status"] == "failed"
    assert "experimental_error" in completed[0]
    assert summary["model_error_rate"] == 1.0


def test_completed_branch_retains_only_one_full_checkpoint(tmp_path):
    calls = Counter()
    checkpoint_root = tmp_path / "checkpoints"
    pipeline = BranchPipeline(
        handlers=make_handlers(calls, []),
        checkpoint_dir=checkpoint_root,
        on_decision=lambda row: _append_async([], row),
        on_complete=lambda row: _append_async([], row),
    )

    asyncio.run(pipeline.run([branch("p::compact")]))

    store = BranchCheckpointStore(checkpoint_root)
    branch_dir = checkpoint_root / store.branch_key("p::compact")
    assert [path.name for path in branch_dir.iterdir()] == ["final.json"]
    assert store.read("p::compact", "final")["branch_id"] == "p::compact"

    replay_calls = Counter()
    replayed = BranchPipeline(
        handlers=make_handlers(replay_calls, []),
        checkpoint_dir=checkpoint_root,
        on_decision=lambda row: _append_async([], row),
        on_complete=lambda row: _append_async([], row),
    )
    asyncio.run(replayed.run([branch("p::compact")]))
    assert sum(replay_calls.values()) == 0
    assert [path.name for path in branch_dir.iterdir()] == ["final.json"]


def test_failed_branch_discards_superseded_stage_checkpoints(tmp_path):
    calls = Counter()
    handlers = make_handlers(calls, [])

    async def fail_validation(record):
        raise RuntimeError("synthetic validation failure")

    handlers["validation"] = fail_validation
    checkpoint_root = tmp_path / "checkpoints"
    pipeline = BranchPipeline(
        handlers=handlers,
        checkpoint_dir=checkpoint_root,
        on_decision=lambda row: _append_async([], row),
        on_complete=lambda row: _append_async([], row),
        on_error=lambda row: _append_async([], row),
    )

    asyncio.run(pipeline.run([branch("p::failed")]))

    store = BranchCheckpointStore(checkpoint_root)
    branch_dir = checkpoint_root / store.branch_key("p::failed")
    assert [path.name for path in branch_dir.iterdir()] == ["branch_error.json"]


def test_full_checkpoint_retention_remains_available_for_debugging(tmp_path):
    checkpoint_root = tmp_path / "checkpoints"
    pipeline = BranchPipeline(
        handlers=make_handlers(Counter(), []),
        checkpoint_dir=checkpoint_root,
        on_decision=lambda row: _append_async([], row),
        on_complete=lambda row: _append_async([], row),
        compact_checkpoints=False,
    )

    asyncio.run(pipeline.run([branch("p::full")]))

    store = BranchCheckpointStore(checkpoint_root)
    branch_dir = checkpoint_root / store.branch_key("p::full")
    assert {path.name for path in branch_dir.iterdir()} == {
        *(f"{stage}.json" for stage in ALL_STAGES),
        "decision_consumed.json",
        "final.json",
    }


def test_compact_checkpoint_storage_eliminates_stage_multiplier(tmp_path):
    async def run(root, *, compact):
        pipeline = BranchPipeline(
            handlers=make_handlers(Counter(), []),
            checkpoint_dir=root,
            on_decision=lambda row: _append_async([], row),
            on_complete=lambda row: _append_async([], row),
            compact_checkpoints=compact,
        )
        large = branch(f"p::{'compact' if compact else 'full'}")
        large["scoring_result"] = {"answer": "x" * 100_000}
        await pipeline.run([large])

    compact_root = tmp_path / "compact"
    full_root = tmp_path / "full"
    asyncio.run(run(compact_root, compact=True))
    asyncio.run(run(full_root, compact=False))

    compact_bytes = sum(path.stat().st_size for path in compact_root.rglob("*.json"))
    full_bytes = sum(path.stat().st_size for path in full_root.rglob("*.json"))
    assert compact_bytes * 5 < full_bytes
