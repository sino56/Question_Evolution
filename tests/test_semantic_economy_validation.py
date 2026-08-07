import asyncio
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from candidate_selection import validation_quality_score
from question_evolution import QuestionEvolutionProcessor, build_semantic_retry_payload
from validate_evolved_question import attach_validation_result, local_validation_rule_version, validate_record


def _item(prompt, *, old_prompt="原题事实。"):
    return {
        "sample_id": "semantic-case",
        "candidate_id": "semantic-case::cand_1",
        "candidate_operator": "O27_cross_layer_conclusion_calibration",
        "prompt": prompt,
        "question_evolved": True,
        "meta_info": {"prompt_old": old_prompt, "question_evolution_metadata": {}},
    }


def test_long_necessary_candidate_is_observed_not_rejected_by_character_count():
    prompt = "请根据单位换算、测量误差、阈值规则和每项数值作出判断：" + "数值A=1；数值B=2；" * 350
    result = attach_validation_result(_item(prompt), max_prompt_chars=10, semantic_economy_mode="enforce")["validation_result"]

    assert result["passed"] is True
    assert result["estimated_prompt_chars"] > 1200
    assert result["prompt_char_delta"] == len(prompt) - len("原题事实。")
    assert result["prompt_char_growth_ratio"] is not None
    assert "题长" not in str(result["reject_reason"])
    assert local_validation_rule_version(max_prompt_chars=10) == local_validation_rule_version(max_prompt_chars=9999)


def test_short_redundancy_and_surface_leaks_are_separate_semantic_failures():
    prompt = (
        "共同背景：画面显示车辆进入停车场。\n"
        "共同背景：画面显示车辆进入停车场。\n"
        "请说明现有材料最高支持什么结论。"
    )
    result = attach_validation_result(_item(prompt), semantic_economy_mode="enforce")["validation_result"]

    assert result["passed"] is False
    assert result["shared_context_repeated"] is True
    assert result["surface_leak_risk"] is True
    assert "boundary_language_leak" in result["surface_leak_type"]
    assert {"semantic_redundancy", "shared_context_repeated", "surface_leak"} <= set(result["semantic_economy_failure_types"])
    assert result["invalid_type"] == "semantic_economy_failed"
    assert "删除题面中的答案边界提示" in result["suggested_same_operator_retry_reason"]


def test_anonymized_o10_o12_historical_replay_locates_answer_expansion_and_shared_context():
    rows = [
        json.loads(line)
        for line in (ROOT / "tests" / "fixtures" / "semantic_economy_historical_replay.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    replay = {
        row["candidate_operator"]: attach_validation_result(row, semantic_economy_mode="enforce")["validation_result"]
        for row in rows
    }

    assert replay["O10_evidence_sufficiency_ladder"]["answer_hint_expansion"] is True
    assert replay["O12_conjunctive_necessity"]["shared_context_repeated"] is True
    assert all(result["estimated_prompt_chars"] < 1200 for result in replay.values())


def test_shadow_records_semantic_failure_without_changing_admission_and_off_is_explicitly_unassessed():
    prompt = "共同背景：画面显示车辆进入停车场。\n共同背景：画面显示车辆进入停车场。"
    shadow = attach_validation_result(_item(prompt), semantic_economy_mode="shadow")["validation_result"]
    off = attach_validation_result(_item(prompt), semantic_economy_mode="off")["validation_result"]

    assert shadow["passed"] is True
    assert shadow["semantic_economy_would_fail"] is True
    assert shadow["semantic_economy_mode"] == "shadow"
    assert off["passed"] is True
    assert off["semantic_economy_evaluated"] is False
    assert off["semantic_economy_risk"] == "not_evaluated"
    assert shadow["local_validation_rule_version"] != off["local_validation_rule_version"]


def test_llm_semantic_signal_respects_shadow_and_enforce_modes():
    llm = {
        "main_axis_clear": True,
        "answerable": True,
        "semantic_redundancy_dominant": True,
        "semantic_economy_risk": "high",
        "semantic_economy_reason": "第二段只是第一段的改写。",
    }
    shadow = attach_validation_result(_item("请根据事实判断。"), llm_validation=llm, semantic_economy_mode="shadow")["validation_result"]
    enforce = attach_validation_result(_item("请根据事实判断。"), llm_validation=llm, semantic_economy_mode="enforce")["validation_result"]

    assert shadow["passed"] is True
    assert shadow["semantic_economy_llm_evaluated"] is True
    assert shadow["semantic_economy_risk"] == "high"
    assert enforce["passed"] is False
    assert "semantic_redundancy" in enforce["semantic_economy_failure_types"]


def test_llm_infrastructure_failure_is_unassessed_in_shadow_and_blocks_enforce():
    failed = {"validation_infrastructure_error": "provider timeout"}
    shadow = attach_validation_result(_item("请根据事实判断。"), llm_validation=failed, semantic_economy_mode="shadow")["validation_result"]
    enforce = attach_validation_result(_item("请根据事实判断。"), llm_validation=failed, semantic_economy_mode="enforce")["validation_result"]

    assert shadow["passed"] is True
    assert shadow["semantic_economy_risk"] == "unassessed"
    assert shadow["semantic_economy_llm_status"] == "failed"
    assert enforce["passed"] is False
    assert enforce["invalid_type"] == "validation_infrastructure_error"


def test_character_observation_does_not_change_candidate_validation_quality_score():
    common = {
        "passed": True,
        "main_axis_count": 1,
        "output_tasks_count": 1,
        "candidate_options_count": 1,
        "counterfactual_count": 0,
        "external_knowledge_risk": "low",
        "format_difficulty_risk": "low",
        "repeat_pattern_risk": "low",
    }
    short = {"prompt": "短题", "validation_result": {**common, "estimated_prompt_chars": 20}}
    long = {"prompt": "长题" * 1000, "validation_result": {**common, "estimated_prompt_chars": 2000}}
    assert validation_quality_score(short) == validation_quality_score(long)


class _Response:
    def __init__(self, payload):
        self.choices = [type("Choice", (), {"message": type("Message", (), {"content": payload})()})()]


class _SemanticRetryClient:
    def __init__(self, *, always_leak=False):
        self.calls = []
        self.always_leak = always_leak

    async def chat_completions_create(self, **kwargs):
        self.calls.append(kwargs)
        leak = self.always_leak or len(self.calls) == 1
        evolved_prompt = "请判断现有材料最高支持什么结论。" if leak else "请根据题面中的画面事实作出业务判断，并说明依据。"
        return _Response(json.dumps({"evolved_prompt": evolved_prompt, "evolution_strategy": "semantic retry"}, ensure_ascii=False))


def _retry_item():
    return {
        "sample_id": "semantic-retry",
        "prompt": "画面显示甲将物品交给乙。应如何研判？",
        "reference_answer": "不能直接确认所有权，只能作为线索。",
        "candidate_answer": "直接确认所有权。",
        "rubric": [],
        "score_rate": 0.9,
        "evolution_action": "evolve_high_score_overscore",
        "operator_route": {"primary_operator": "O27_cross_layer_conclusion_calibration", "backup_operators": []},
        "sample_profile": {},
        "overscore_diagnosis": {},
    }


def test_semantic_failure_retries_same_operator_with_redacted_structured_feedback():
    client = _SemanticRetryClient()
    processor = QuestionEvolutionProcessor(client, model="test", max_concurrent=1, max_retries=0, max_validation_retries=0)
    evolved = asyncio.run(processor.evolve_with_retry(_retry_item(), operator_id="O27_cross_layer_conclusion_calibration"))

    assert len(client.calls) == 2
    retry_prompt = client.calls[1]["messages"][0]["content"]
    assert "同一 operator 的语义经济重试反馈" in retry_prompt
    assert '"operator_id": "O27_cross_layer_conclusion_calibration"' in retry_prompt
    assert "answer_boundary_ledger" not in retry_prompt
    assert "rubric_intent_ledger" not in retry_prompt
    assert "不能直接确认所有权" not in retry_prompt
    assert evolved["validation_retry"]["semantic_retry_attempts"] == 1


def test_semantic_retry_stops_after_one_same_label_retry_and_records_exhaustion():
    client = _SemanticRetryClient(always_leak=True)
    processor = QuestionEvolutionProcessor(client, model="test", max_concurrent=1, max_retries=0, max_validation_retries=0)
    evolved = asyncio.run(processor.evolve_with_retry(_retry_item(), operator_id="O27_cross_layer_conclusion_calibration"))

    assert len(client.calls) == 2
    assert evolved["retry_exhausted"] is True
    assert evolved["retry_attempts"] == 1
    assert evolved["operator_id"] == "O27_cross_layer_conclusion_calibration"
    assert evolved["_local_validation_result"]["passed"] is False


def test_retry_payload_contains_facts_but_not_answer_or_rubric_ledgers():
    item = _retry_item()
    validation = validate_record(_item("请说明现有材料最高支持什么结论。"), semantic_economy_mode="enforce")
    payload = build_semantic_retry_payload(
        item,
        validation,
        operator_id="O27_cross_layer_conclusion_calibration",
        retry_attempt=1,
        max_semantic_retry_attempts=2,
        failure_reasons=[validation["reject_reason"]],
    )
    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload["must_keep"]
    assert "answer_boundary_ledger" not in serialized
    assert "rubric_intent_ledger" not in serialized
    assert "只能作为线索" not in serialized
