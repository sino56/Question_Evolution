import json
from typing import Any, Dict


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def build_difficulty_gain_prompt(item: Dict[str, Any]) -> str:
    meta_info = item.get("meta_info") if isinstance(item.get("meta_info"), dict) else {}
    metadata = meta_info.get("question_evolution_metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    context = {
        "sample_id": item.get("sample_id", item.get("index")),
        "original_prompt": meta_info.get("prompt_old") or item.get("prompt"),
        "evolved_prompt": item.get("prompt"),
        "reference_answer": (
            (meta_info.get("references") or [""])[0]
            if isinstance(meta_info.get("references"), list) and meta_info.get("references")
            else ""
        ),
        "stale_rubric": meta_info.get("stale_rubric") or item.get("rubric") or [],
        "representative_round0_answer": item.get("representative_round0_answer") or {},
        "round0_score_summary": item.get("round0_score_summary") or {},
        "sample_profile": item.get("sample_profile") or {},
        "overscore_diagnosis": item.get("overscore_diagnosis") or {},
        "operator_route": item.get("operator_route") or {},
        "candidate_operator": item.get("candidate_operator") or metadata.get("operator_used"),
        "question_evolution_metadata": metadata,
        "candidate_generation": item.get("candidate_generation") or {},
        "validation_result": item.get("validation_result") or {},
    }

    return f"""你是候选题难度收益验证器。你的任务不是评价题目是否更详细，而是判断候选题在被正式采纳前，是否真的比原题更有压测价值。

请严格比较 original_prompt 与 evolved_prompt，并结合参考答案、round0 代表性弱模型答案、样本画像、过高得分诊断、算子路由和复杂度校验结果判断。

必须重点拦截：
1. 候选题把原本应由模型自行发现的关键缺口、正确结论方向或判断层级写进题面。
2. 候选题把推理路径脚手架化，使弱模型只需顺着题面填空。
3. 候选题只是更清楚、更长、更格式化，却没有真实竞争判断。
4. 候选题偏离原题主轴、依赖题外知识或不可稳定回答。

请只输出合法 JSON，不要输出 Markdown，不要解释 JSON 之外的内容。字段必须符合：
{{
  "difficulty_gain_label": "clear_gain | probable_gain | weak_gain | no_gain | leakage_or_simplification | format_complexity_only | axis_shift | unanswerable_or_external | needs_manual_review",
  "dimension_scores": {{
    "axis_consistency_score": 0.0-1.0,
    "no_leakage_score": 0.0-1.0,
    "competitive_judgment_score": 0.0-1.0,
    "anti_clarity_trap_score": 0.0-1.0,
    "answerability_score": 0.0-1.0,
    "format_complexity_score": 0.0-1.0
  }},
  "risk_tags": [
    "missing_premise_named",
    "conclusion_hint_revealed",
    "option_gap_too_obvious",
    "layer_boundary_exposed",
    "answer_path_scaffolded",
    "overclarified_prompt",
    "format_difficulty_only",
    "external_fact_dependency",
    "multi_axis_overload",
    "no_real_gain"
  ],
  "leakage_analysis": "说明是否泄漏答案线索",
  "clarity_trap_analysis": "说明是否题变清楚但更容易",
  "competitive_judgment_analysis": "说明候选题制造了什么真实竞争判断；若没有则说明没有",
  "expected_qwen_failure_match": true,
  "expected_failure_point": "弱模型预计会在哪个目标 failure mode 上失分",
  "reject_reason": "",
  "recommended_action": "admit_candidate | admit_if_no_better_candidate | reject_candidate | needs_manual_review"
}}

评分准则：
- axis_consistency_score：是否保留原题核心领域、事实和能力主轴。
- no_leakage_score：是否没有点名关键缺口、正确结论方向、判断层级或强答案关键结论。
- competitive_judgment_score：是否制造了真实竞争判断，而不是泛泛要求更全面。
- anti_clarity_trap_score：是否避免把隐藏难点拆成教学式步骤。
- answerability_score：是否仅凭题干和参考材料可回答，不依赖题外知识。
- format_complexity_score：难度是否不来自表格、编号、篇幅、多任务或格式约束。

待验证上下文：
{_json_block(context)}
"""


def build_weak_probe_judgment_prompt(item: Dict[str, Any], probe_answer: str) -> str:
    meta_info = item.get("meta_info") if isinstance(item.get("meta_info"), dict) else {}
    metadata = meta_info.get("question_evolution_metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    context = {
        "sample_id": item.get("sample_id", item.get("index")),
        "original_prompt": meta_info.get("prompt_old") or item.get("prompt"),
        "evolved_prompt": item.get("prompt"),
        "reference_answer": (
            (meta_info.get("references") or [""])[0]
            if isinstance(meta_info.get("references"), list) and meta_info.get("references")
            else ""
        ),
        "stale_rubric": meta_info.get("stale_rubric") or item.get("rubric") or [],
        "representative_round0_answer": item.get("representative_round0_answer") or {},
        "round0_score_summary": item.get("round0_score_summary") or {},
        "sample_profile": item.get("sample_profile") or {},
        "overscore_diagnosis": item.get("overscore_diagnosis") or {},
        "operator_route": item.get("operator_route") or {},
        "candidate_operator": item.get("candidate_operator") or metadata.get("operator_used"),
        "question_evolution_metadata": metadata,
        "difficulty_gain_validation": item.get("difficulty_gain_validation") or {},
        "weak_probe_answer": probe_answer,
    }

    return f"""你是候选题 weak probe 判定器。弱模型已经对候选题作答一次。请判断这次作答是否暴露了候选题设计想压测的目标 failure mode。

判定重点：
1. 不要重新评价候选题题面本身，题面验证已有 difficulty_gain_validation。
2. 比较 weak_probe_answer 与参考答案、原 round0 弱模型短板、overscore_diagnosis、question_evolution_metadata.expected_qwen_failure。
3. 如果弱模型仍然犯了目标错误、遗漏目标关键边界、或把线索上推成不该有的结论，视为命中目标 failure。
4. 如果弱模型直接答中关键点、没有暴露目标 failure、或看不出候选题比原题更难，则视为未命中。

请只输出合法 JSON，不要输出 Markdown。字段必须符合：
{{
  "probe_failure_detected": true,
  "target_failure_match": true,
  "probe_judgment": "candidate likely exposes target failure | candidate is harder than original | weak probe did not expose target failure | needs_manual_review",
  "probe_reason": "一句话说明 weak probe 是否命中目标 failure"
}}

待判定上下文：
{_json_block(context)}
"""
