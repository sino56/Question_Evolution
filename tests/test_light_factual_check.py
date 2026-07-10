import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


from light_factual_check import build_light_factual_check, generate_report, process_records


def make_record(prompt, old_prompt):
    return {
        "sample_id": "lfc",
        "prompt": prompt,
        "question_evolved": True,
        "meta_info": {"prompt_old": old_prompt},
    }


def test_numeric_fact_addition_is_fatal():
    record = make_record(
        "候选题要求判断嫌疑人在 30分钟 内是否完成转移。",
        "原题要求判断嫌疑人是否完成转移。",
    )

    check = build_light_factual_check(record)

    assert check["passed"] is False
    assert check["fatal_errors"]
    assert "numeric_fact_added_or_conflicted" in check["risk_tags"]


def test_text_level_reversal_is_fatal():
    record = make_record(
        "可以补充新材料后再判断结论是否成立。",
        "不得补充新材料，只能依据现有题干判断结论是否成立。",
    )

    check = build_light_factual_check(record)

    assert check["passed"] is False
    assert any("反转" in error for error in check["fatal_errors"])
    assert "text_level_reversal" in check["risk_tags"]


def test_warning_does_not_fail_check():
    record = make_record(
        "仅限依据现有材料，判断结论措辞应如何限定。",
        "判断结论是否成立。",
    )

    check = build_light_factual_check(record)

    assert check["passed"] is True
    assert check["fatal_errors"] == []
    assert check["warnings"]


def test_process_records_and_report_counts():
    records = [
        make_record("候选题新增 5分钟 条件。", "原题没有时间条件。"),
        make_record("仅限现有材料判断。", "判断结论是否成立。"),
    ]
    checked = process_records(records)
    report = generate_report(checked)

    assert report["light_factual_fatal_count"] == 1
    assert report["light_factual_warning_count"] == 1


if __name__ == "__main__":
    test_numeric_fact_addition_is_fatal()
    test_text_level_reversal_is_fatal()
    test_warning_does_not_fail_check()
    test_process_records_and_report_counts()
    print("light factual check checks passed")
