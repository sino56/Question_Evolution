import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple


NUMERIC_FACT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"\d+(?:\.\d+)?"
    r"\s*(?:年|月|日|时|小时|分钟|秒|元|万元|公里|千米|米|厘米|毫米|%|％|比例|倍|天|岁|kg|KG|cm|mm|m)"
)
QUOTED_ENTITY_PATTERN = re.compile(r"[“\"'《]([^“”\"'《》]{2,24})[”\"'》]")
NEGATIVE_PERMISSION_PATTERN = re.compile(r"(不得|不能|不允许|禁止)([^。；，,;]{1,18})")

DETERMINISTIC_RESULT_TERMS = (
    "已经证明",
    "已经证实",
    "已查明",
    "足以认定",
    "可以认定",
    "能够认定",
    "形成闭环",
    "排他成立",
    "结论成立",
)
UNCERTAINTY_TERMS = ("是否", "能否", "不足", "缺少", "不能直接", "尚不能", "仍需", "判断")
NARROWING_TERMS = ("仅限", "只考虑", "限定为", "仅依据", "只依据", "在不考虑")
MATERIAL_LIMIT_TERMS = ("不得补充", "不能补充", "不引入题外", "只能依据", "仅凭现有")
CONCLUSION_WORDING_TERMS = ("是否稳妥", "能否认定", "应如何限定", "支持到什么程度", "能支持到哪一步")
KEY_RESTRICTION_TERMS = ("不得", "不能", "必须", "只能", "仅", "不允许", "禁止")


def load_json_or_jsonl(input_path: str) -> List[Dict[str, Any]]:
    with open(input_path, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return []
    if content.startswith("["):
        data = json.loads(content)
        if not isinstance(data, list):
            raise ValueError("JSON input must be an array")
        return data
    return [json.loads(line) for line in content.splitlines() if line.strip()]


def write_jsonl(records: Iterable[Dict[str, Any]], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_json(data: Dict[str, Any], output_path: str) -> None:
    output_dir = os.path.dirname(os.path.abspath(output_path))
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def _clean_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def get_original_prompt(item: Dict[str, Any]) -> str:
    meta_info = item.get("meta_info")
    if isinstance(meta_info, dict):
        old_prompt = meta_info.get("prompt_old")
        if isinstance(old_prompt, str) and old_prompt.strip():
            return old_prompt.strip()
    return _clean_text(item.get("prompt"))


def _unique(values: Iterable[str]) -> List[str]:
    result: List[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in result:
            result.append(text)
    return result


def _numeric_facts(text: str) -> List[str]:
    return _unique(match.group(0).replace(" ", "") for match in NUMERIC_FACT_PATTERN.finditer(text))


def _quoted_entities(text: str) -> List[str]:
    return _unique(match.group(1).strip() for match in QUOTED_ENTITY_PATTERN.finditer(text))


def _has_any(text: str, terms: Sequence[str]) -> bool:
    return any(term in text for term in terms)


def _permission_reversals(original_prompt: str, evolved_prompt: str) -> List[str]:
    reversals: List[str] = []
    for match in NEGATIVE_PERMISSION_PATTERN.finditer(original_prompt):
        action = match.group(2).strip(" ：:，,；;。")
        if not action:
            continue
        action_core = action[:12]
        if not action_core:
            continue
        if re.search(rf"(可以|允许|可)\s*{re.escape(action_core)}", evolved_prompt):
            reversals.append(f"原题限制被反转为允许：{action_core}")
    return reversals


def build_light_factual_check(item: Dict[str, Any]) -> Dict[str, Any]:
    if item.get("question_evolved") is False:
        return {
            "passed": True,
            "fatal_errors": [],
            "warnings": [],
            "risk_tags": [],
            "check_version": "light_factual_check_v1",
            "skipped": True,
            "skip_reason": "pass-through original",
        }

    original_prompt = get_original_prompt(item)
    evolved_prompt = _clean_text(item.get("prompt"))
    fatal_errors: List[str] = []
    warnings: List[str] = []
    risk_tags: List[str] = []

    original_numbers = set(_numeric_facts(original_prompt))
    evolved_numbers = set(_numeric_facts(evolved_prompt))
    added_numbers = sorted(evolved_numbers - original_numbers)
    if added_numbers:
        fatal_errors.append("候选题新增或修改了高置信数值/时间/金额/比例：" + "、".join(added_numbers[:6]))
        risk_tags.append("numeric_fact_added_or_conflicted")

    original_entities = set(_quoted_entities(original_prompt))
    evolved_entities = set(_quoted_entities(evolved_prompt))
    added_entities = sorted(evolved_entities - original_entities)
    removed_entities = sorted(original_entities - evolved_entities)
    if original_entities and added_entities and removed_entities:
        fatal_errors.append(
            "候选题疑似替换了原题明确实体：" + "、".join(removed_entities[:3]) + " -> " + "、".join(added_entities[:3])
        )
        risk_tags.append("explicit_entity_replaced")

    reversals = _permission_reversals(original_prompt, evolved_prompt)
    if reversals:
        fatal_errors.extend(reversals)
        risk_tags.append("text_level_reversal")

    if _has_any(evolved_prompt, DETERMINISTIC_RESULT_TERMS) and not _has_any(original_prompt, DETERMINISTIC_RESULT_TERMS):
        if _has_any(original_prompt, UNCERTAINTY_TERMS):
            fatal_errors.append("候选题加入了原题没有的确定性证据结果或结论成立表述。")
            risk_tags.append("new_deterministic_evidence_result")

    if _has_any(evolved_prompt, NARROWING_TERMS) and not _has_any(original_prompt, NARROWING_TERMS):
        warnings.append("候选题对场景或材料范围做了收窄，需在排序中轻微降权。")
        risk_tags.append("scenario_narrowing_warning")
    if _has_any(evolved_prompt, MATERIAL_LIMIT_TERMS) and not _has_any(original_prompt, MATERIAL_LIMIT_TERMS):
        warnings.append("候选题新增了可用材料限制，需确认没有改变可回答范围。")
        risk_tags.append("material_limit_warning")
    if _has_any(evolved_prompt, CONCLUSION_WORDING_TERMS) and not _has_any(original_prompt, CONCLUSION_WORDING_TERMS):
        warnings.append("候选题改变了结论措辞或强度表达，需轻微降权观察。")
        risk_tags.append("conclusion_wording_warning")
    if _has_any(original_prompt, KEY_RESTRICTION_TERMS) and not _has_any(evolved_prompt, KEY_RESTRICTION_TERMS):
        warnings.append("候选题删除了原题中的限制性表达，可能改变边界。")
        risk_tags.append("deleted_key_restriction_warning")

    return {
        "passed": not fatal_errors,
        "fatal_errors": fatal_errors,
        "warnings": warnings,
        "risk_tags": _unique(risk_tags),
        "check_version": "light_factual_check_v1",
    }


def attach_light_factual_check(item: Dict[str, Any]) -> Dict[str, Any]:
    result = dict(item)
    result["light_factual_check"] = build_light_factual_check(item)
    return result


def process_records(records: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [attach_light_factual_check(record) for record in records]


def generate_report(records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    fatal_count = 0
    warning_count = 0
    risk_distribution: Counter = Counter()
    for record in records:
        check = record.get("light_factual_check")
        check = check if isinstance(check, dict) else {}
        fatal_errors = check.get("fatal_errors")
        warnings = check.get("warnings")
        if isinstance(fatal_errors, list) and fatal_errors:
            fatal_count += 1
        if isinstance(warnings, list) and warnings:
            warning_count += 1
        for tag in check.get("risk_tags", []) if isinstance(check.get("risk_tags"), list) else []:
            risk_distribution[_clean_text(tag)] += 1
    return {
        "total_candidates": len(records),
        "light_factual_fatal_count": fatal_count,
        "light_factual_warning_count": warning_count,
        "light_factual_risk_tag_distribution": dict(sorted(risk_distribution.items())),
    }


def default_report_output(output_path: str) -> str:
    path = Path(output_path)
    if path.suffix:
        return str(path.with_name(f"{path.stem}.light_factual_report.json"))
    return f"{output_path}.light_factual_report.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Attach a lightweight factual consistency check to evolved candidates.")
    parser.add_argument("--input", required=True, help="Input validated candidate JSON/JSONL path.")
    parser.add_argument("--output", required=True, help="Output JSONL path with light_factual_check attached.")
    parser.add_argument("--report-output", default=None, help="Optional JSON report path.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_json_or_jsonl(args.input)
    checked = process_records(records)
    write_jsonl(checked, args.output)
    write_json(generate_report(checked), args.report_output or default_report_output(args.output))


if __name__ == "__main__":
    main()
