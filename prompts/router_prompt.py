import json
from typing import Any, Dict, Optional


ROUTER_RULE_SUMMARY = """
规则路由优先级：
- evolution_state.recommended_next_methods > 相似样本成功记忆 > 诊断规则
- 盲区或不可见状态归因 -> O11_unobserved_state_attribution
- 强线索替代未闭合门槛 -> O12_conjunctive_necessity
- 新增事实改变原评价 -> O13_minimal_disqualifier
- 题外补设或信息闭包 -> O14_information_closure
- 单变量变化后的保留/撤回 -> O15_counterfactual_threshold_shift
- 相近正常解释导致过度降级 -> O16_close_alternative_normalization
- 处置触发与事实定性混淆 -> O17_action_vs_fact_threshold
- 统计基线范围或样本口径错配 -> O18_baseline_scope_mismatch
- 其他近似业务判断竞争 -> O10_evidence_sufficiency_ladder
- score_increased、重复题型或其他失败状态必须避开上一轮失败算子
""".strip()


def build_router_prompt(record: Dict[str, Any], memory_summary: Optional[Dict[str, Any]] = None) -> str:
    """Optional LLM-router prompt; production routing is deterministic in operator_router.py."""
    payload = {
        "sample_id": record.get("sample_id", record.get("index")),
        "sample_profile": record.get("sample_profile", {}),
        "overscore_diagnosis": record.get("overscore_diagnosis", {}),
        "evolution_state": record.get("evolution_state", {}),
        "evolution_action": record.get("evolution_action"),
        "score_rate": record.get("score_rate"),
        "memory_summary": memory_summary or {},
    }
    return f"""
# 角色
你是 question evolution 的算子路由器。你只负责选择 operator，不生成新题，不修改 rubric，不推荐评分规则。

# 路由规则
{ROUTER_RULE_SUMMARY}

# 输入
{json.dumps(payload, ensure_ascii=False, indent=2)}

# 输出
返回合法 JSON 对象，不要输出 Markdown：
{{
  "operator_route": {{
    "primary_operator": "O10_evidence_sufficiency_ladder",
    "backup_operators": ["O17_action_vs_fact_threshold"],
    "avoid_operators": [],
    "routing_reason": "简要说明为什么选择该 operator",
    "is_high_value_sample": true,
    "should_use_local_tree_search": false
  }}
}}
""".strip()
