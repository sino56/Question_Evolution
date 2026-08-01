"""Router-only hard-slot summaries for question-evolution operators.

These cards make applicability visible to the LLM Router.  They intentionally
do not evaluate records in Python, change operator generation prompts, or
block a branch at runtime.  The Router still makes the semantic decision from
the complete sample input; this module only prevents it from mistaking a
surface theme for the structure required to construct a valid question.
"""

from __future__ import annotations

from typing import Any, Dict


ROUTING_CARD_GATES: Dict[str, Dict[str, Any]] = {
    "O10_evidence_sufficiency_ladder": {
        "required_slots": (
            "同一目标命题",
            "两个或以上互不蕴含的可观察事实",
            "事实之间可共同闭合目标命题的连接",
            "至少一个可比但不足以闭合的相关事实",
        ),
        "reject_if_missing": "缺少可组合的多事实连接，或只有单条显眼线索时，不得用一般最小充分集合替代。",
    },
    "O11_unobserved_state_attribution": {
        "required_slots": (
            "不可见区间前的可见入口及时间",
            "预期出口窗口与实际出口或缺席信息",
            "可判断的路径、方向或速度约束",
            "面对相同端点约束的候选解释",
        ),
        "reject_if_missing": "缺少任一端点时间窗、路径约束或候选比较时，不能因盲区、消失或再出现等词选择本算子。",
    },
    "O12_conjunctive_necessity": {
        "required_slots": (
            "同一目标命题",
            "两个彼此独立且不可互相替代的事实条件",
            "仅有第一条件、仅有第二条件和二者并存的可比事实组合",
        ),
        "reject_if_missing": "未给出两个独立条件及其可比组合时，不得把一般多事实题或单调信息增加当成共同必要性。",
    },
    "O13_minimal_disqualifier": {
        "required_slots": (
            "目标命题及其可追踪的必要事实连接",
            "与该连接同类且可比的复核事实",
            "能检验连接是否被破坏的具体冲突或缺口",
            "局部连接后果与整体命题后果的区分空间",
        ),
        "reject_if_missing": "没有可识别的必要连接或复核事实时，不能把一般的不确定性、降置信或事件断点当成最小破坏项。",
    },
    "O14_information_closure": {
        "required_slots": (
            "题面完整列明的可用事实范围",
            "可识别的、不得补造的中间状态、口径或阈值",
        ),
        "reject_if_missing": "O14 不生成独立问题；它只保留信息闭包内容身份，不能作为可执行路由候选。",
    },
    "O15_counterfactual_threshold_shift": {
        "required_slots": (
            "唯一且明确的变化事实",
            "保持不变的比较背景与目标命题",
            "唯一被比较的语义量和结论层级",
            "若要求跨阈值判断，则题面明示固定阈值及适用范围",
        ),
        "reject_if_missing": "同时变化多个关键事实、比较量不唯一，或要判断门槛却未给门槛时，不得选择本算子。",
    },
    "O16_close_alternative_normalization": {
        "required_slots": (
            "目标解释",
            "一个能解释部分核心或共享事实的相近替代解释",
            "两种解释各自的事实覆盖或残差",
            "可观察但不预标答案的区分事实",
        ),
        "reject_if_missing": "没有真实竞争的单一替代解释和可比较残差时，不能把一般疑点或多假设排序路由到本算子。",
    },
    "O17_action_vs_fact_threshold": {
        "required_slots": (
            "两套题面明示的业务规则",
            "每套规则的版本、适用对象与阈值或条件",
            "当前事实与两套规则之间可区分的映射",
            "事实结论与处置结论的不同范围",
        ),
        "reject_if_missing": "缺少第二套明示规则、适用范围或阈值时，不得靠常识补写规则后选择本算子。",
    },
    "O18_baseline_scope_mismatch": {
        "required_slots": (
            "保持不变的当前观测与当前事件",
            "两个同域候选基线",
            "每个基线的纳入标准",
            "判断异常性所需的统计摘要",
        ),
        "reject_if_missing": "没有两个可比较基线、纳入口径或必要摘要时，不得将来源名称差异误作基线范围问题。",
    },
    "O19_multi_entity_role_binding": {
        "required_slots": (
            "至少两个可竞争实体",
            "跨时间或观测节点的局部身份、角色或行为线索",
            "可区分实体绑定的节点差异或定向动作事实",
            "待判断的实体—角色—行为归属主张",
        ),
        "reject_if_missing": "只有多人共现而无竞争实体、节点绑定线索或定向行为时，不得选择本算子。",
    },
    "O20_multistage_event_breakpoint": {
        "required_slots": (
            "两个或以上明确阶段或状态转移",
            "阶段间必要连接及其业务后果",
            "可定位为断点的具体阶段事实",
            "整体链是否成立的目标主张",
        ),
        "reject_if_missing": "缺少阶段顺序、必要转移或断点后果时，不得把一般多事实关系或完整多跳网络当成阶段断点。",
    },
    "O21_object_provenance_identity": {
        "required_slots": (
            "待追踪对象及可识别特征",
            "来源、转移、遮挡或重现的可观察链",
            "至少一个具有局部解释力的竞争来源或同一性解释",
            "与链路完整度相匹配的来源或同一性主张",
        ),
        "reject_if_missing": "未给对象特征、流转链或竞争来源时，不能仅因出现物品、车辆或重现词选择本算子。",
    },
    "O22_path_topology_reachability": {
        "required_slots": (
            "路径图中的节点与可通行边",
            "方向、封闭或其他边通行限制",
            "起点、终点及其时间窗口",
            "可与约束联合比较的候选路径或可达性主张",
        ),
        "reject_if_missing": "缺少节点、边、端点或时间窗时，不得用路径、路线或经过等表面词替代拓扑可达性。",
    },
    "O23_observation_reliability_conflict": {
        "required_slots": (
            "待使用的具体观测",
            "观测的可见性、清晰度、来源一致性或质量限制",
            "相互冲突或可比较的来源信息",
            "该观测拟支持的事实或结论范围",
        ),
        "reject_if_missing": "没有可判断的质量限制或来源冲突时，不能把一般的结论层级越界误作观测可靠性问题。",
    },
    "O24_multi_hypothesis_residual_ranking": {
        "required_slots": (
            "三个或以上真实竞争的解释",
            "可比较的事实覆盖与冲突",
            "至少一个具有区分力的残差",
            "可判断额外假设成本或结论排序的题面依据",
        ),
        "reject_if_missing": "只有一个替代解释时应考虑 O16；缺少多假设、残差或排序依据时不得选择本算子。",
    },
    "O25_procedural_invariant_frame": {
        "required_slots": (
            "可追踪的程序步骤",
            "记录映射、单位或参照系信息",
            "步骤依赖或可比性条件",
            "待判断的程序结果或比较结论",
        ),
        "reject_if_missing": "没有题面提供的程序、参照或映射事实时，不能靠外部流程常识补成程序不变量问题。",
    },
    "O26_quantitative_threshold_propagation": {
        "required_slots": (
            "题面给出的量、单位和直接计算关系",
            "可传播的不确定区间、误差或范围",
            "明确的业务阈值",
            "结果区间与阈值的判断主张",
        ),
        "reject_if_missing": "缺少公式或直接关系、单位、区间或阈值时，不得把定性门槛讨论伪装成定量传播。",
    },
    "O27_cross_layer_conclusion_calibration": {
        "required_slots": (
            "可区分的观测或证据支持",
            "从事实认定到可写结论或行动的目标链",
            "各结论层所需的题面规则或限制",
            "当前答案越级或越强表述的具体失败机制",
        ),
        "reject_if_missing": "没有结论层级、题面边界或明确越级失败时，不得仅因观测存在局限就选择跨层校准。",
    },
    "O28_multihop_chain_closure": {
        "required_slots": (
            "跨实体、阶段、节点或路径的两个以上必要跳转",
            "每个必要跳转的题面事实",
            "链条端点及其完成义务",
            "整体闭合而非局部连通的目标主张",
        ),
        "reject_if_missing": "单一阶段断点可由 O20 表达时不得升级；缺少完整多跳义务或必要跳转时不得选择本算子。",
    },
    "O29_entity_identity_conflict_resolution": {
        "required_slots": (
            "待判同一性的实体或记录",
            "支持身份连续性的局部线索",
            "能排他或冲突的绑定线索",
            "随证据完整度变化的归属主张",
        ),
        "reject_if_missing": "没有支持与排他冲突并存的身份线索时，不得把一般角色绑定或多人出现路由到本算子。",
    },
    "O30_active_discriminative_observation": {
        "required_slots": (
            "当前无法区分的竞争解释",
            "可执行的候选下一步观测",
            "不同观测结果对解释排序的可判断影响",
            "观测可行性或资源边界",
        ),
        "reject_if_missing": "缺少竞争解释、可执行观测或结果差异时，不得把补充信息的一般建议当成主动判别观测。",
    },
    "O31_observation_accumulation_calibration": {
        "required_slots": (
            "两个或以上观测",
            "每项观测的来源、独立性或依赖传播关系",
            "累计证据拟支持的结论强度",
            "可区分真实新增信息与同源重复的依据",
        ),
        "reject_if_missing": "未给多次观测及其来源依赖时，不得将单条证据强弱或一般结论越级当成累积校准。",
    },
    "O32_role_graph_critical_edge": {
        "required_slots": (
            "多个主体及其有方向的关系或行为事实",
            "支撑目标结论的关系边",
            "必要边失效或可替代路径的判断依据",
            "整体角色或协同结论",
        ),
        "reject_if_missing": "只有共现、无方向关系或无关键边/替代路径时，不得选择角色图关键边。",
    },
    "O33_cross_modal_support_boundary": {
        "required_slots": (
            "两个或以上不同模态或来源的材料",
            "每个来源的时间、对象与适用范围",
            "来源之间的对齐或冲突事实",
            "融合材料可支持的目标结论边界",
        ),
        "reject_if_missing": "未给多源材料及其范围对齐/冲突时，不得把单源证据不足或一般可靠性问题路由到跨模态边界。",
    },
}


def routing_card_gate(operator_id: str) -> Dict[str, Any]:
    """Return a detached Router-card gate for a registered operator."""

    try:
        gate = ROUTING_CARD_GATES[operator_id]
    except KeyError as exc:
        raise ValueError(f"Missing routing-card gate for {operator_id}") from exc
    return {
        "required_slots": list(gate["required_slots"]),
        "reject_if_missing": str(gate["reject_if_missing"]),
    }
