from typing import Any, Dict


def contract_fields_for_prompt(prompt: str) -> Dict[str, Any]:
    if "指定 operator：O15_counterfactual_threshold_shift" in prompt:
        return {
            "target_claim": {"claim_id": "C1", "text": "目标业务判断"},
            "conclusion_layer": "fact_claim",
            "surface_fact_ids": ["F_changed"],
            "applied_transforms": ["replace_single_fact"],
            "operator_payload": {
                "changed_fact_id": "F_changed",
                "comparison_quantity": "evidence_support",
                "direction_or_order": "decreased",
                "conclusion_layer_effect": "support_decreased",
                "threshold_given": False,
            },
            "surface_leakage_risks": {
                "option_only": False,
                "fact_ablated": False,
                "surface_swapped": False,
                "parent_obligation_drift": False,
                "cross_operator_isomorphism": False,
            },
            "answer_contract": {
                "answer_key": {
                    "direction_or_order": "decreased",
                    "conclusion_layer_effect": "support_decreased",
                },
                "decisive_fact_ids": ["F_changed"],
                "rubric_assertions": ["保持比较量一致", "无阈值时不强制整体翻转"],
            },
        }
    return {
        "target_claim": {"claim_id": "C1", "text": "目标业务判断"},
        "conclusion_layer": "overall_claim",
        "surface_fact_ids": ["F_selected", "F_support"],
        "applied_transforms": ["add_review_fact"],
        "operator_payload": {
            "selected_fact_id": "F_selected",
            "broken_link_id": "L1",
            "claim_level_effect": "local_link_broken_overall_supported",
            "alternative_support_fact_ids": ["F_support"],
        },
        "surface_leakage_risks": {
            "option_only": False,
            "fact_ablated": False,
            "surface_swapped": False,
            "parent_obligation_drift": False,
            "cross_operator_isomorphism": False,
        },
        "answer_contract": {
            "answer_key": {
                "selected_fact_id": "F_selected",
                "claim_level_effect": "local_link_broken_overall_supported",
            },
            "decisive_fact_ids": ["F_selected", "F_support"],
            "rubric_assertions": ["识别必要连接", "区分局部连接失效与整体结论"],
        },
    }
