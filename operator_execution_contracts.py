"""Mode-aware material contracts for O10--O33.

This module is deliberately declarative.  It describes the materials and
offline controls an operator needs; it neither renders a question nor decides
whether a routed candidate may run.  Runtime routing and candidate disposition
remain the responsibility of their existing stages.
"""

from dataclasses import dataclass
from typing import Dict, Sequence


SOURCE_FAITHFUL = "source_faithful"
CONTROLLED_SYNTHESIS = "controlled_synthesis"
CONTROLLED_HYPOTHETICAL_CASE = "controlled_hypothetical_case"
HYPOTHETICAL_ADAPTATION = "hypothetical_adaptation_from_source"
ALL_GENERATION_MODES = (
    SOURCE_FAITHFUL,
    CONTROLLED_HYPOTHETICAL_CASE,
    CONTROLLED_SYNTHESIS,
    HYPOTHETICAL_ADAPTATION,
)

NON_SYNTHESIZABLE_EXTERNAL_MATERIAL = (
    "真实法规或专业阈值",
    "真实机构或业务规则",
    "真实外部记录或案件事实",
    "真实业务结论",
)


@dataclass(frozen=True)
class OperatorExecutionContract:
    operator_id: str
    supported_modes: Sequence[str]
    primary_axis: str
    allowed_auxiliary_axes: Sequence[str]
    required_slots: Sequence[str]
    synthesizable_slots: Sequence[str]
    non_synthesizable_slots: Sequence[str]
    neutral_task_intent: str
    required_checks: Sequence[str]
    requires_live_competitor: bool
    generates_question: bool


def _contract(
    operator_id: str,
    primary_axis: str,
    required_slots: Sequence[str],
    synthesizable_slots: Sequence[str],
    neutral_task_intent: str,
    required_checks: Sequence[str],
    *,
    auxiliary_axes: Sequence[str] = (),
    requires_live_competitor: bool = False,
    generates_question: bool = True,
) -> OperatorExecutionContract:
    return OperatorExecutionContract(
        operator_id=operator_id,
        supported_modes=ALL_GENERATION_MODES,
        primary_axis=primary_axis,
        allowed_auxiliary_axes=tuple(auxiliary_axes),
        required_slots=tuple(required_slots),
        synthesizable_slots=tuple(synthesizable_slots),
        non_synthesizable_slots=NON_SYNTHESIZABLE_EXTERNAL_MATERIAL,
        neutral_task_intent=neutral_task_intent,
        required_checks=("information_closure", "surface_leakage", *required_checks),
        requires_live_competitor=requires_live_competitor,
        generates_question=generates_question,
    )


OPERATOR_EXECUTION_CONTRACTS: Dict[str, OperatorExecutionContract] = {
    "O10_evidence_sufficiency_ladder": _contract(
        "O10_evidence_sufficiency_ladder", "最小充分事实关系",
        ("目标主张", "互不蕴含的观察事实", "相关干扰事实"),
        ("题内观察记录", "题内实体和动作"),
        "根据材料判断目标业务主张是否成立，并说明依据。",
        ("decisive_fact_ablation", "irrelevant_fact_ablation", "name_or_order_swap"),
    ),
    "O11_unobserved_state_attribution": _contract(
        "O11_unobserved_state_attribution", "端点时序联合一致性",
        ("可见端点", "时间窗", "路径或速度约束"),
        ("题内端点观察", "题内路径参数", "题内局部通行时间"),
        "根据记录判断候选解释是否与已给端点和条件相符，并说明依据。",
        ("joint_constraint_check", "name_or_order_swap"),
        auxiliary_axes=("时间窗", "路径约束"), requires_live_competitor=True,
    ),
    "O12_conjunctive_necessity": _contract(
        "O12_conjunctive_necessity", "独立贡献与合取关系",
        ("目标主张", "两个独立条件", "可比观察场景"),
        ("题内观察条件", "题内案例实体"),
        "根据材料判断当前业务主张是否成立，并说明依据。",
        ("decisive_fact_ablation", "name_or_order_swap"),
        auxiliary_axes=("证据充分性",),
    ),
    "O13_minimal_disqualifier": _contract(
        "O13_minimal_disqualifier", "必要连接的局部失效",
        ("目标主张", "相关复核事实", "连接承接关系"),
        ("题内复核观察", "题内局部行为"),
        "结合复核材料判断原业务主张是否仍成立，并说明依据。",
        ("decisive_fact_ablation", "irrelevant_fact_ablation", "name_or_order_swap"),
    ),
    "O14_information_closure": _contract(
        "O14_information_closure", "信息闭包校验",
        ("公开事实", "事实来源或受控假设标记", "题内规则"), (),
        "不生成题面；逐项记录事实来源缺口。",
        ("fact_source_trace", "illegal_rule_check"), generates_question=False,
    ),
    "O15_counterfactual_threshold_shift": _contract(
        "O15_counterfactual_threshold_shift", "单变量门槛变化",
        ("目标判断", "单一变化变量", "固定规则或阈值"),
        ("题内局部参数", "题内单一观察变化"),
        "根据材料判断当前业务主张是否成立，并说明依据。",
        ("single_variable_control", "order_swap"),
    ),
    "O16_close_alternative_normalization": _contract(
        "O16_close_alternative_normalization", "相近解释的覆盖与残差",
        ("目标解释", "相近竞争解释", "区分事实"),
        ("题内竞争观察", "题内实体和动作"),
        "根据材料判断哪种解释更符合当前记录，并说明依据。",
        ("live_competitor", "decisive_fact_ablation", "name_or_order_swap"),
        requires_live_competitor=True,
    ),
    "O17_action_vs_fact_threshold": _contract(
        "O17_action_vs_fact_threshold", "双规则范围映射",
        ("两条原始规则", "适用对象", "题内事实", "规则版本或局部声明"),
        ("题内局部规则", "题内局部参数"),
        "依据给定规则和材料作出一个整体业务判断，并说明依据。",
        ("rule_source_check", "rule_name_or_order_swap"),
        auxiliary_axes=("规则版本", "适用对象"),
    ),
    "O18_baseline_scope_mismatch": _contract(
        "O18_baseline_scope_mismatch", "基线纳入口径",
        ("原始纳入条件", "统计摘要", "当前观测"),
        ("题内假设基线", "题内统计参数"),
        "依据给定口径和材料判断当前业务情况，并说明依据。",
        ("baseline_name_or_order_swap", "irrelevant_fact_ablation"),
    ),
    "O19_multi_entity_role_binding": _contract(
        "O19_multi_entity_role_binding", "多实体角色绑定",
        ("多个实体", "分散局部行为", "时间或关系线索"),
        ("题内实体", "题内局部动作", "题内观察记录"),
        "根据各实体的记录作出一个业务判断，并说明依据。",
        ("entity_name_swap", "decisive_fact_ablation"),
        requires_live_competitor=True,
    ),
    "O20_multistage_event_breakpoint": _contract(
        "O20_multistage_event_breakpoint", "多阶段状态承接",
        ("事件观察", "状态变化", "目标业务主张"),
        ("题内事件记录", "题内阶段状态"),
        "根据流程记录判断目标业务主张是否成立，并说明依据。",
        ("decisive_fact_ablation", "irrelevant_fact_ablation", "order_swap"),
    ),
    "O21_object_provenance_identity": _contract(
        "O21_object_provenance_identity", "对象来源与同一性",
        ("对象观察", "转移或遮挡记录", "竞争来源"),
        ("题内对象", "题内转移观察", "题内外观记录"),
        "根据对象记录判断相关业务主张是否成立，并说明依据。",
        ("decisive_fact_ablation", "name_or_order_swap", "live_competitor"),
        requires_live_competitor=True,
    ),
    "O22_path_topology_reachability": _contract(
        "O22_path_topology_reachability", "路径拓扑联合可达",
        ("方向连接", "通行约束", "端点观察", "时间窗"),
        ("题内假设路径", "题内通行时间", "题内端点记录"),
        "根据给定路径和记录判断目标是否可能到达指定位置，并说明依据。",
        ("decisive_fact_ablation", "irrelevant_fact_ablation", "node_name_swap"),
        auxiliary_axes=("时间窗", "通行限制"), requires_live_competitor=True,
    ),
    "O23_observation_reliability_conflict": _contract(
        "O23_observation_reliability_conflict", "观测可靠性冲突",
        ("观测条件", "来源生成关系", "实际可见字段"),
        ("题内观测记录", "题内采集条件"),
        "根据观测条件和材料作出业务判断，并说明依据。",
        ("condition_ablation", "source_name_swap"),
    ),
    "O24_multi_hypothesis_residual_ranking": _contract(
        "O24_multi_hypothesis_residual_ranking", "多假设残差比较",
        ("多个竞争解释", "共同事实", "区分事实"),
        ("题内解释", "题内观察记录"),
        "根据材料判断当前哪种解释更符合记录，并说明依据。",
        ("live_competitor", "decisive_fact_ablation", "name_or_order_swap"),
        requires_live_competitor=True,
    ),
    "O25_procedural_invariant_frame": _contract(
        "O25_procedural_invariant_frame", "程序不变量与参照系",
        ("流程步骤", "字段或单位", "参照变化", "记录映射"),
        ("题内程序", "题内单位和局部参数"),
        "根据给定程序和记录判断当前结果是否有效，并说明依据。",
        ("decisive_fact_ablation", "format_invariance", "order_swap"),
    ),
    "O26_quantitative_threshold_propagation": _contract(
        "O26_quantitative_threshold_propagation", "定量阈值与误差传播",
        ("公式", "单位", "输入区间", "误差关系", "阈值"),
        ("题内数值", "题内公式", "题内局部阈值"),
        "依据给定数值和关系作出一个阈值判断，并说明依据。",
        ("calculation_check", "irrelevant_number_ablation", "unit_name_swap"),
    ),
    "O27_cross_layer_conclusion_calibration": _contract(
        "O27_cross_layer_conclusion_calibration", "跨层结论校准",
        ("原始规则或题内条件", "观察事实", "目标业务判断"),
        ("题内观察记录", "题内局部规则"),
        "根据给定规则和材料作出一个整体业务判断，并说明依据。",
        ("decisive_fact_ablation", "irrelevant_fact_ablation", "order_swap"),
    ),
    "O28_multihop_chain_closure": _contract(
        "O28_multihop_chain_closure", "多跳链路闭合",
        ("跨节点观察", "实体或状态承接", "终局主张"),
        ("题内节点", "题内观察记录", "题内路径条件"),
        "根据跨节点材料判断目标业务主张是否成立，并说明依据。",
        ("decisive_fact_ablation", "irrelevant_fact_ablation", "node_name_or_order_swap"),
    ),
    "O29_entity_identity_conflict_resolution": _contract(
        "O29_entity_identity_conflict_resolution", "实体同一性冲突消解",
        ("支持线索", "连续线索", "冲突线索", "候选实体"),
        ("题内实体", "题内观察线索"),
        "根据身份线索作出一个业务判断，并说明依据。",
        ("decisive_fact_ablation", "name_or_order_swap", "live_competitor"),
        requires_live_competitor=True,
    ),
    "O30_active_discriminative_observation": _contract(
        "O30_active_discriminative_observation", "主动判别观测选择",
        ("竞争解释", "可执行观测", "资源或场景条件"),
        ("题内观测选项", "题内资源约束"),
        "根据当前材料选择下一项应获取的观察，并说明依据。",
        ("live_competitor", "outcome_blindness", "order_swap"),
        requires_live_competitor=True,
    ),
    "O31_observation_accumulation_calibration": _contract(
        "O31_observation_accumulation_calibration", "观测累积校准",
        ("原始来源", "生成或转录关系", "新增特征"),
        ("题内观测", "题内来源关系"),
        "根据观测记录作出一个业务判断，并说明依据。",
        ("decisive_fact_ablation", "same_source_repeat_control", "order_swap"),
    ),
    "O32_role_graph_critical_edge": _contract(
        "O32_role_graph_critical_edge", "角色关系关键边",
        ("实体", "方向性行为", "关系承接"),
        ("题内实体", "题内局部行为"),
        "根据各实体的行为记录作出一个业务判断，并说明依据。",
        ("decisive_fact_ablation", "irrelevant_edge_ablation", "name_or_order_swap"),
    ),
    "O33_cross_modal_support_boundary": _contract(
        "O33_cross_modal_support_boundary", "跨模态对齐与融合",
        ("来源字段", "时间对齐", "实体对齐", "采集范围"),
        ("题内多源记录", "题内对齐标记"),
        "根据不同来源的材料作出一个融合业务判断，并说明依据。",
        ("decisive_fact_ablation", "irrelevant_source_ablation", "name_or_order_swap"),
        requires_live_competitor=True,
    ),
}


def get_execution_contract(operator_id: str) -> OperatorExecutionContract:
    try:
        return OPERATOR_EXECUTION_CONTRACTS[operator_id]
    except KeyError as exc:
        raise ValueError(f"Unknown operator execution contract: {operator_id}") from exc
