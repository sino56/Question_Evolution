import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from profile_samples import ProfileProcessor, parse_profile_response
from select_evolution_candidates import (
    EVOLVE_HIGH_SCORE_OVERSCORE,
    PASS_THROUGH_OR_SCORING_NOISE,
    PROBE_MIDDLE_SCORE_BOUNDARY,
    RECONSTRUCT_LOW_SCORE_BOUNDARY,
    STOP_EVOLUTION,
    process_records,
)


def load_jsonl(path: Path):
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records


def profile_response(core, cause, target, worth=True):
    return json.dumps(
        {
            "sample_profile": {
                "core_capability": core,
                "claim_level": "可疑线索",
                "problem_shape": "候选项区分",
                "reasoning_granularity": "两步链条",
                "answer_mode_expected": "比较型",
                "easy_judgment_risk": "low",
                "external_knowledge_risk": "low",
                "complexity_expansion_risk": "medium",
            },
            "overscore_diagnosis": {
                "is_worth_evolving": worth,
                "candidate_overscore_cause": cause,
                "target_failure_mode": target,
                "why_high_score_is_suspicious": f"{cause}:{target}",
            },
        },
        ensure_ascii=False,
    )


class FakeProfileClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat_completions_create(self, **kwargs):
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("No fake profile response left.")
        return self.responses.pop(0)


def test_profile_parser_rejects_operator_recommendation():
    bad_response = json.dumps(
        {
            "sample_profile": {
                "core_capability": "证据链补强",
                "claim_level": "可疑线索",
                "problem_shape": "候选项区分",
                "reasoning_granularity": "两步链条",
                "answer_mode_expected": "比较型",
                "easy_judgment_risk": "low",
                "external_knowledge_risk": "low",
                "complexity_expansion_risk": "medium",
            },
            "overscore_diagnosis": {
                "is_worth_evolving": True,
                "candidate_overscore_cause": "漏最小关键事实",
                "target_failure_mode": "选错最关键缺口",
                "why_high_score_is_suspicious": "缺少独立必要条件定位。",
            },
            "recommended_operator": "O1_gap_choice",
        },
        ensure_ascii=False,
    )

    try:
        parse_profile_response(bad_response)
    except ValueError as exc:
        assert "operator" in str(exc)
    else:
        raise AssertionError("operator recommendation should be rejected")


def test_profile_processor_and_selector_cover_stage02_actions():
    records = load_jsonl(ROOT / "tests" / "fixtures" / "stage02_scored.jsonl")
    fake_client = FakeProfileClient(
        [
            profile_response("证据链补强", "漏最小关键事实", "选错最关键缺口", True),
            profile_response("行为模式识别", "反常线索主线切换失败", "反常线索主线切换失败", True),
            profile_response("边界判断", "评分噪声", "格式失分", False),
            profile_response("边界判断", "基础边界判断过稳", "稳定满分", False),
        ]
    )

    processor = ProfileProcessor(fake_client, model="mock-profile-model", max_concurrent=1)
    profiled = asyncio.run(processor.process_records(records))

    assert len(profiled) == 4
    for record in profiled:
        assert isinstance(record.get("sample_profile"), dict)
        assert isinstance(record.get("overscore_diagnosis"), dict)
        assert "operator_route" not in record
        assert "recommended_operator" not in record

    selected = process_records(profiled, high_score_threshold=0.8, low_score_threshold=0.6)
    actions = {record["sample_id"]: record["evolution_action"] for record in selected}

    assert actions["stage02-high-overscore"] == EVOLVE_HIGH_SCORE_OVERSCORE
    assert actions["stage02-low-boundary"] == RECONSTRUCT_LOW_SCORE_BOUNDARY
    assert actions["stage02-scoring-noise"] == PASS_THROUGH_OR_SCORING_NOISE
    assert actions["stage02-stop"] == STOP_EVOLUTION

    for record in selected:
        assert record["evolution_action_reason"]
        assert "operator_used" not in record


def test_profile_file_stage_publishes_trace_sidecar_and_manifest(tmp_path):
    records = load_jsonl(ROOT / "tests" / "fixtures" / "stage02_scored.jsonl")[:2]
    input_path = tmp_path / "input.jsonl"
    output_path = tmp_path / "profiled.jsonl"
    input_path.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records),
        encoding="utf-8",
    )
    fake_client = FakeProfileClient(
        [
            profile_response("证据链补强", "漏最小关键事实", "选错最关键缺口", True),
            profile_response("行为模式识别", "主线切换失败", "错过主线", True),
        ]
    )
    processor = ProfileProcessor(fake_client, model="mock-profile-model", max_concurrent=2)
    asyncio.run(processor.process_file(str(input_path), str(output_path)))

    published = load_jsonl(output_path)
    assert len(published) == 2
    assert all("profile_raw_response" not in item["profile_metadata"] for item in published)
    assert all(item["profile_metadata"]["profile_raw_response_trace_id"] for item in published)
    assert Path(str(output_path) + ".profile_traces.jsonl.gz").exists()
    manifest = json.loads(Path(str(output_path) + ".manifest.json").read_text(encoding="utf-8"))
    assert manifest["stage"] == "profile_samples"
    assert manifest["artifact"]["record_count"] == 2
    assert manifest["sidecars"][0]["record_count"] == 2


def test_middle_score_and_cross_round_recommendation_continue_evolution():
    base = {
        "sample_profile": {"core_capability": "边界判断"},
        "overscore_diagnosis": {
            "is_worth_evolving": True,
            "candidate_overscore_cause": "正常解释导致风险判断过度撤回",
            "target_failure_mode": "异常强度下降被误写为风险消失",
        },
        "score_rate": 0.7,
    }
    middle = process_records([base])[0]
    assert middle["evolution_action"] == PROBE_MIDDLE_SCORE_BOUNDARY

    reroute = dict(base)
    reroute["overscore_diagnosis"] = {
        "is_worth_evolving": False,
        "candidate_overscore_cause": "基础边界判断过稳",
        "target_failure_mode": "稳定满分",
    }
    reroute["evolution_state"] = {
        "stop_status": "rollback_and_reroute",
        "recommended_next_methods": ["O16_close_alternative_normalization"],
    }
    selected = process_records([reroute])[0]
    assert selected["evolution_action"] == PROBE_MIDDLE_SCORE_BOUNDARY


if __name__ == "__main__":
    test_profile_parser_rejects_operator_recommendation()
    test_profile_processor_and_selector_cover_stage02_actions()
    test_middle_score_and_cross_round_recommendation_continue_evolution()
    print("stage02 profile and candidate selection checks passed")
