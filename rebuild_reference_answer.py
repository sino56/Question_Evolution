"""Rebuild and verify reference answers for selected evolved questions.

Old answers are audit material only.  This stage creates a versioned answer for
the exact final question before rubric construction; it deliberately does not
send that answer to the weak answering model.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping

from openai import AsyncOpenAI

from governance import question_version, resolve_execution_scope, scope_allows
from local_api_config import get_config_list, get_config_value
from pipeline_runtime import load_json_records, publish_records, StageMetrics
from prompts.reference_rebuild_prompt import REFERENCE_REBUILD_PROMPT


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _metadata(record: Mapping[str, Any]) -> Dict[str, Any]:
    value = record.get("meta_info")
    return dict(value) if isinstance(value, Mapping) else {}


def selected_for_rebuild(record: Mapping[str, Any]) -> bool:
    selection = record.get("candidate_selection")
    selection = selection if isinstance(selection, Mapping) else {}
    return record.get("question_evolved") is True and selection.get("selected") is True


def public_material(record: Mapping[str, Any]) -> Dict[str, Any]:
    metadata = _metadata(record).get("question_evolution_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    projection = metadata.get("public_fact_projection")
    return dict(projection) if isinstance(projection, Mapping) else {"public_fact_ledger": [], "public_rule_ledger": []}


def build_reference_prompt(record: Mapping[str, Any]) -> str:
    return (
        REFERENCE_REBUILD_PROMPT
        + "\n\n# Final question\n" + _clean(record.get("prompt"))
        + "\n\n# Public material\n" + json.dumps(public_material(record), ensure_ascii=False)
        + "\n\nReturn only the reference answer."
    )


def verify_rebuilt_reference(record: Mapping[str, Any], answer: Any) -> Dict[str, Any]:
    text = _clean(answer)
    forbidden = ("隐藏规划", "旧评分标准", "旧参考答案", "预期弱模型错误")
    issues = []
    if not text:
        issues.append("empty_reference_answer")
    if any(token in text for token in forbidden):
        issues.append("restricted_material_disclosure")
    return {
        "verified": not issues,
        "verification_method": "independent_reference_isolation_check_v1",
        "issues": issues,
        "question_version": question_version(record.get("prompt")),
    }


def attach_rebuilt_reference(record: Mapping[str, Any], answer: Any) -> Dict[str, Any]:
    result = deepcopy(dict(record))
    if not selected_for_rebuild(result):
        return result
    if not scope_allows(result, "reference_rebuild"):
        raise ValueError("execution_scope does not authorize reference rebuild")
    verification = verify_rebuilt_reference(result, answer)
    info = _metadata(result)
    info["reference_rebuild"] = {
        "reference_answer": _clean(answer),
        "reference_answer_version": verification["question_version"],
        "verification": verification,
    }
    # This is the sole active reference for the changed prompt.  The old list
    # remains in stale_references for audit but can no longer be consumed by
    # gen_rubric or scoring.
    if verification["verified"]:
        info["references"] = [_clean(answer)]
        info["active_reference_answer_version"] = verification["question_version"]
    result["meta_info"] = info
    return result


def attach_execution_scope(record: Mapping[str, Any], scope_name: str) -> Dict[str, Any]:
    result = deepcopy(dict(record))
    info = _metadata(result)
    if scope_name != "full_iteration":
        raise ValueError("formal reference rebuild requires execution_scope=full_iteration")
    info["execution_scope"] = {
        "max_stage": "full_iteration",
        "allow_reference_rebuild": True,
        "allow_model_answering": True,
        "allow_judge_scoring": True,
        "allow_effect_claim": True,
        "source": "run_loop_explicit",
    }
    result["meta_info"] = info
    return result


def active_verified_reference(record: Mapping[str, Any]) -> str:
    info = _metadata(record)
    rebuilt = info.get("reference_rebuild")
    rebuilt = rebuilt if isinstance(rebuilt, Mapping) else {}
    verification = rebuilt.get("verification")
    if not isinstance(verification, Mapping) or verification.get("verified") is not True:
        raise ValueError("changed question has no independently verified rebuilt reference answer")
    expected = question_version(record.get("prompt"))
    if rebuilt.get("reference_answer_version") != expected:
        raise ValueError("rebuilt reference-answer version does not match final question")
    return _clean(rebuilt.get("reference_answer"))


async def _rebuild_one(record: Dict[str, Any], client: AsyncOpenAI, model: str) -> Dict[str, Any]:
    if not selected_for_rebuild(record):
        return dict(record)
    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": build_reference_prompt(record)}],
        temperature=0.0,
    )
    answer = _clean(response.choices[0].message.content if response.choices else "")
    return attach_rebuilt_reference(record, answer)


def _keys(values: List[str]) -> List[str]:
    if values:
        return [value for value in values if _clean(value)]
    raw = os.getenv("REFERENCE_REBUILD_API_KEYS") or os.getenv("GPT_API_KEYS") or os.getenv("OPENAI_API_KEY") or ""
    return [part.strip() for part in raw.split(",") if part.strip()] or get_config_list("REFERENCE_REBUILD_API_KEYS", "GPT_API_KEYS", "OPENAI_API_KEY")


async def _run(records: List[Dict[str, Any]], *, base_url: str, keys: List[str], model: str) -> List[Dict[str, Any]]:
    if not any(selected_for_rebuild(record) for record in records):
        return [dict(record) for record in records]
    if not keys:
        raise ValueError("reference rebuild requires an API key for selected evolved candidates")
    client = AsyncOpenAI(api_key=keys[0], base_url=base_url or None)
    try:
        return [await _rebuild_one(record, client, model) for record in records]
    finally:
        await client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild verified references for selected evolved questions.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default=os.getenv("REFERENCE_REBUILD_MODEL") or get_config_value("REFERENCE_REBUILD_MODEL", "GPT_MODEL", default="gpt-5.4"))
    parser.add_argument("--base-url", default=os.getenv("REFERENCE_REBUILD_BASE_URL") or get_config_value("REFERENCE_REBUILD_BASE_URL", "BASE_URL", default=""))
    parser.add_argument("--api-key", action="append", default=[])
    parser.add_argument("--execution-scope", required=True, choices=["full_iteration"])
    args = parser.parse_args()
    records = [attach_execution_scope(record, args.execution_scope) for record in load_json_records(args.input, stage="rebuild_reference_answer")]
    metrics = StageMetrics("rebuild_reference_answer")
    output = asyncio.run(_run(records, base_url=args.base_url, keys=_keys(args.api_key), model=args.model))
    publish_records(output, args.output, stage="rebuild_reference_answer", input_path=args.input, config={"model": args.model}, code_paths=[__file__], metrics=metrics)


if __name__ == "__main__":
    main()
