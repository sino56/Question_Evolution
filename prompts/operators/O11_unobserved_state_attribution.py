from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O11_unobserved_state_attribution",
    name="端点时序契约",
    ability_axis="endpoint_temporal_consistency",
    goal=(
        "基于可见端点、时间窗和路径约束，判断不可见区间中的候选假设是否与已给时序一致，"
        "而不是猜测盲区内真实发生了什么。"
    ),
    reasoning_object="进入端点、离开端点、时间窗、路径约束与至少两个均能解释部分端点的候选假设。",
    required_question_shape=(
        "围绕一个不可见区间给出完整端点和时间约束，只问现有材料对该区间能够支持什么结论及依据；"
        "候选假设与端点、时间窗、路径约束的映射留给回答者完成。"
    ),
    content_transformation=(
        "构造两个都解释部分端点事实的假设，并通过区间重叠、多出口或速度范围形成时序约束；"
        "不新增不可见区间内的真实事件。"
    ),
    invariants=("可见端点不变", "观测时间不变", "路径范围不变", "目标判断不变"),
    competition_structure="两个假设都必须解释至少一个端点事实，差异只来自是否同时满足时间窗与路径约束。",
    preserved_parent_obligations=(
        "使用题面事实组织时序关系",
        "区分可见事实与不可见状态推断",
    ),
    required_reasoning_tasks=(
        "map_hypotheses_to_endpoints",
        "check_time_window_consistency",
        "check_path_constraint_consistency",
    ),
    target_error_taxonomy=(
        "invented_unobserved_event",
        "endpoint_constraint_ignored",
        "temporal_consistency_misjudged",
    ),
    excluded_error_taxonomy=("general_confidence_change", "action_vs_fact_rule_mapping"),
    forbidden_shortcuts=(
        "不得用整数大小、假设名称或方向标签直接决定答案，不得要求复原盲区真相，"
        "不得把候选假设的端点映射写成题面步骤。"
    ),
    adjacent_boundaries="若核心是两套明示业务规则，归 O17；若仅改变一个事实后的支持度，归 O15。",
    content_controls=(
        "正控制：只有联合检查端点、时间窗和路径约束才能排除不一致假设",
        "结论不变负控制：调整非约束性表面细节不改变一致性",
        "近邻控制：缺少任一硬端点或时间前置时判 not_applicable",
        "表面交换控制：交换假设顺序不改变答案",
    ),
    allowed_answer_shape="对不可见区间可支持结论的开放式判断及端点时序依据。",
    forbidden_answer_shape="猜测未见事件、直接询问是否证明发生意外、按题面给定步骤逐项查表。",
    default_evaluation_focus=(
        "是否把候选假设映射到可见端点",
        "是否同时检查时间窗与路径约束",
        "是否拒绝补造不可见区间事件",
    ),
)
