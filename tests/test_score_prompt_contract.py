import sys
import types
import importlib.util
import pytest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def install_dependency_stubs():
    if importlib.util.find_spec("openai") is None:
        openai_stub = types.ModuleType("openai")
        openai_stub.AsyncOpenAI = object
        sys.modules.setdefault("openai", openai_stub)

    if importlib.util.find_spec("aiofiles") is None:
        aiofiles_stub = types.ModuleType("aiofiles")
        sys.modules.setdefault("aiofiles", aiofiles_stub)

    if importlib.util.find_spec("tqdm") is None:
        tqdm_stub = types.ModuleType("tqdm")
        tqdm_asyncio_stub = types.ModuleType("tqdm.asyncio")
        tqdm_asyncio_stub.tqdm_asyncio = object
        sys.modules.setdefault("tqdm", tqdm_stub)
        sys.modules.setdefault("tqdm.asyncio", tqdm_asyncio_stub)


def test_score_prompt_placeholder_contract_is_shared():
    install_dependency_stubs()
    from gen_rubric import build_score_prompt
    from scoring import ANSWER_PLACEHOLDER, build_scoring_prompt

    rubric = [{"title": "核心判断", "description": "答对核心判断。", "weight": 10}]
    score_prompt = build_score_prompt({"prompt": "测试题目"}, rubric)
    rendered = build_scoring_prompt(score_prompt, "候选答案")

    assert ANSWER_PLACEHOLDER == "<<<待评答案>>"
    assert ANSWER_PLACEHOLDER in score_prompt
    assert "<<<待评答案>>>" not in score_prompt
    assert ANSWER_PLACEHOLDER not in rendered
    assert "候选答案" in rendered


def test_item_score_title_matching_tolerates_quote_style_drift():
    install_dependency_stubs()
    from scoring import normalize_item_scores

    rubric = [
        {
            "title": "法律研判深度与“排除合理怀疑”原则",
            "description": "测试标题标点漂移。",
            "weight": 10,
        }
    ]
    item_scores = [
        {
            "title": "法律研判深度与‘排除合理怀疑’原则",
            "awarded": 8,
            "brief_reason": "同一 rubric 标题，仅引号样式不同。",
        }
    ]

    normalized_scores, total_awarded = normalize_item_scores(item_scores, rubric)

    assert total_awarded == 8
    assert normalized_scores[0]["title"] == "法律研判深度与“排除合理怀疑”原则"


def test_rubric_alignment_must_match_frozen_answer_contract():
    install_dependency_stubs()
    from gen_rubric import (
        build_user_prompt,
        validate_rubric_answer_contract_alignment,
    )

    answer_contract = {
        "answer_contract_hash": "a" * 64,
        "target_claim": {"claim_id": "C1"},
        "conclusion_layer": "overall_claim",
        "answer_key": {"claim_level_effect": "local_link_broken_overall_supported"},
        "decisive_fact_ids": ["F2", "F3"],
    }
    prompt = build_user_prompt(
        "问题",
        ["参考答案"],
        answer_contract=answer_contract,
        scorer_mapping={"rubric_fields": ["claim_level_effect"]},
    )
    assert "冻结答案契约" in prompt
    assert answer_contract["answer_contract_hash"] in prompt

    aligned = {
        field: answer_contract[field]
        for field in (
            "answer_contract_hash",
            "target_claim",
            "conclusion_layer",
            "answer_key",
            "decisive_fact_ids",
        )
    }
    assert validate_rubric_answer_contract_alignment(aligned, answer_contract)["status"] == "aligned"

    conflicting = dict(aligned)
    conflicting["conclusion_layer"] = "local_link"
    with pytest.raises(ValueError, match="conclusion_layer"):
        validate_rubric_answer_contract_alignment(conflicting, answer_contract)


if __name__ == "__main__":
    test_score_prompt_placeholder_contract_is_shared()
    test_item_score_title_matching_tolerates_quote_style_drift()
    test_rubric_alignment_must_match_frozen_answer_contract()
    print("score prompt placeholder contract checks passed")
