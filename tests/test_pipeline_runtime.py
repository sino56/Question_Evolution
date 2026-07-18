import asyncio
import gzip
import json
from pathlib import Path

import pytest

from pipeline_runtime import (
    ArtifactConflictError,
    AtomicJsonlStageWriter,
    FairRequestPool,
    StageJsonError,
    StageMetrics,
    TraceStore,
    bounded_async_map,
    iter_json_records,
    passthrough_reuse_errors,
    process_rss_bytes,
    validate_published_artifact,
)


def _write_jsonl(path: Path, records):
    path.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )


def test_json_reader_supports_arrays_and_reports_jsonl_line(tmp_path):
    array_path = tmp_path / "legacy.json"
    array_path.write_text(json.dumps([{"sample_id": "a"}, {"sample_id": "b"}]), encoding="utf-8")
    assert [item["sample_id"] for item in iter_json_records(str(array_path), stage="profile")] == ["a", "b"]

    bad_path = tmp_path / "bad.jsonl"
    bad_path.write_text('{"sample_id":"a"}\n{bad}\n', encoding="utf-8")
    with pytest.raises(StageJsonError, match=r"\[profile\].*bad\.jsonl:2"):
        list(iter_json_records(str(bad_path), stage="profile"))


def test_atomic_writer_publishes_manifest_and_refuses_unverified_overwrite(tmp_path):
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    _write_jsonl(source, [{"sample_id": "a"}, {"sample_id": "b"}])
    metrics = StageMetrics("unit")
    writer = AtomicJsonlStageWriter(
        str(output),
        stage="unit",
        input_path=str(source),
        config={"model": "fixed"},
        metrics=metrics,
        flush_records=1,
    )
    writer.add_group("a", [{"sample_id": "a"}])
    writer.add_group("b", [{"sample_id": "b"}])
    writer.publish()

    valid, reason = validate_published_artifact(
        str(output), stage="unit", input_path=str(source), config={"model": "fixed"}
    )
    assert (valid, reason) == (True, "ok")
    assert not Path(str(output) + ".partial").exists()
    assert not Path(str(output) + ".checkpoint.jsonl").exists()

    Path(str(output) + ".manifest.json").unlink()
    with pytest.raises(ArtifactConflictError, match="refusing to overwrite"):
        AtomicJsonlStageWriter(str(output), stage="unit", input_path=str(source))


def test_checkpoint_recovery_truncates_unconfirmed_tail_and_skips_group(tmp_path):
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    _write_jsonl(source, [{"sample_id": "a"}, {"sample_id": "b"}])
    writer = AtomicJsonlStageWriter(
        str(output), stage="resume", input_path=str(source), flush_records=1
    )
    writer.add_group("a", [{"sample_id": "a"}])
    writer.close()
    partial = Path(str(output) + ".partial")
    with partial.open("ab") as target:
        target.write(b'{"unconfirmed":true}\n')

    resumed_metrics = StageMetrics("resume")
    resumed = AtomicJsonlStageWriter(
        str(output), stage="resume", input_path=str(source), metrics=resumed_metrics, flush_records=1
    )
    assert resumed.processed_keys == {"a"}
    assert resumed_metrics.output_records == 1
    assert resumed_metrics.output_bytes == partial.stat().st_size
    assert resumed.add_group("a", [{"sample_id": "a", "duplicate": True}]) is False
    resumed.add_group("b", [{"sample_id": "b"}])
    resumed.publish()
    assert list(iter_json_records(str(output), stage="resume")) == [
        {"sample_id": "a"},
        {"sample_id": "b"},
    ]


def test_validation_finishes_atomic_publish_when_only_manifest_rename_was_interrupted(tmp_path):
    source = tmp_path / "input.jsonl"
    output = tmp_path / "output.jsonl"
    _write_jsonl(source, [{"sample_id": "a"}])
    writer = AtomicJsonlStageWriter(str(output), stage="recover-publish", input_path=str(source))
    writer.add_group("a", [{"sample_id": "a"}])
    writer.publish()
    manifest = Path(str(output) + ".manifest.json")
    pending = Path(str(manifest) + ".tmp")
    manifest.replace(pending)

    valid, reason = validate_published_artifact(
        str(output), stage="recover-publish", input_path=str(source)
    )
    assert (valid, reason) == (True, "ok")
    assert manifest.exists()
    assert not pending.exists()


def test_bounded_async_map_limits_workers_and_uses_single_result_callback():
    async def scenario():
        active = 0
        peak = 0
        results = []
        metrics = StageMetrics("bounded")

        async def worker(value):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.001)
            active -= 1
            return value * 2

        async def on_result(sequence, original, value):
            results.append((sequence, original, value))

        await bounded_async_map(
            range(30), worker, concurrency=3, queue_size=4, on_result=on_result, metrics=metrics
        )
        assert peak <= 3
        assert sorted(results) == [(index, index, index * 2) for index in range(30)]
        assert metrics.input_queue_peak <= 4
        assert metrics.output_queue_peak <= 4

    asyncio.run(scenario())


def test_bounded_async_map_can_restore_input_order():
    async def scenario():
        results = []

        async def worker(value):
            await asyncio.sleep((5 - value) * 0.001)
            return value

        async def on_result(sequence, original, value):
            results.append((sequence, original, value))

        await bounded_async_map(
            range(5),
            worker,
            concurrency=5,
            on_result=on_result,
            ordered_results=True,
        )
        assert results == [(index, index, index) for index in range(5)]

    asyncio.run(scenario())


def test_ordered_map_bounds_reorder_buffer_behind_slow_first_record():
    async def scenario():
        yielded = 0
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        def records():
            nonlocal yielded
            for value in range(20):
                yielded += 1
                yield value

        async def worker(value):
            if value == 0:
                first_started.set()
                await release_first.wait()
            return value

        async def on_result(sequence, original, value):
            return None

        task = asyncio.create_task(
            bounded_async_map(
                records(),
                worker,
                concurrency=2,
                queue_size=4,
                ordered_results=True,
                on_result=on_result,
            )
        )
        await first_started.wait()
        await asyncio.sleep(0.02)
        assert yielded <= 5
        release_first.set()
        await task

    asyncio.run(scenario())


def test_fair_request_pool_enforces_limit_and_rotates_samples():
    async def scenario():
        pool = FairRequestPool(1, "service")
        order = []

        async def call(sample, index):
            async with pool.request(sample):
                order.append((sample, index))
                await asyncio.sleep(0.001)

        await asyncio.gather(
            call("a", 1), call("a", 2), call("b", 1), call("b", 2)
        )
        assert pool.peak_active == 1
        assert [sample for sample, _ in order[:3]] == ["a", "b", "a"]

    asyncio.run(scenario())


def test_fair_request_pool_fills_initial_slots_across_samples():
    async def scenario():
        pool = FairRequestPool(3, "service")
        order = []
        release = asyncio.Event()

        async def call(sample, index):
            async with pool.request(sample):
                order.append((sample, index))
                if len(order) >= 3:
                    release.set()
                await release.wait()

        await asyncio.wait_for(
            asyncio.gather(
                call("a", 1),
                call("a", 2),
                call("a", 3),
                call("b", 1),
                call("c", 1),
            ),
            timeout=1,
        )
        assert pool.peak_active == 3
        assert [sample for sample, _ in order[:3]] == ["a", "b", "c"]

    asyncio.run(scenario())


def test_process_rss_bytes_is_available_on_current_platform():
    assert process_rss_bytes() > 0


def test_trace_store_writes_compressed_auditable_entries(tmp_path):
    store = TraceStore("profile")
    trace_id = store.add(record_key="sample-1", raw_text="raw response", trace_kind="model_response")
    path = tmp_path / "profile.traces.jsonl.gz"
    _, count = store.write(str(path))
    assert count == 1
    with gzip.open(path, "rt", encoding="utf-8") as source:
        entry = json.loads(source.readline())
    assert entry["trace_id"] == trace_id
    assert entry["raw_text"] == "raw response"
    assert len(entry["content_sha256"]) == 64


def test_passthrough_reuse_validation_requires_all_expensive_artifacts():
    record = {"question_evolved": False, "meta_info": {"references": ["answer"]}}
    assert passthrough_reuse_errors(record) == [
        "rubric",
        "score_prompt",
        "scoring_result",
        "score_rate",
    ]
