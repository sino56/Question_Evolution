from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O17_action_vs_fact_threshold",
    name="双规则边界判断",
    ability_axis="dual_rule_scope_mapping",
    goal=(
        "基于题面明确给出的两套业务规则，区分处置触发条件和事实定性条件的适用对象、阈值和结论层级。"
    ),
    reasoning_object="两套明示规则的文本、版本、适用对象、阈值，以及与当前事实的具体映射。",
    required_question_shape=(
        "题面完整给出两套规则和当前事实，优先使用边界值、两规则同时部分匹配或事实/行动层级交叉；"
        "只询问当前材料下应作出何种自然业务判断及依据，不拆成两个标签化子任务。"
    ),
    content_transformation=(
        "将父题事实映射到两套明示规则的不同适用对象和阈值，构造至少一个边界或部分匹配点；"
        "规则内容不得由模型常识补齐。"
    ),
    invariants=("两套规则文本不变", "规则版本不变", "当前事实不变", "规则阈值不变"),
    competition_structure="两套规则都应与当前事实有部分匹配，正确映射不能退化为明显的规则关键词查表。",
    preserved_parent_obligations=(
        "把题面事实映射到业务规则",
        "控制事实结论和行动处置的适用范围",
    ),
    required_reasoning_tasks=("map_current_facts_to_rule_a", "map_current_facts_to_rule_b", "separate_rule_scopes"),
    target_error_taxonomy=(
        "action_rule_applied_as_fact_rule",
        "fact_rule_applied_as_action_rule",
        "rule_scope_or_threshold_misread",
    ),
    excluded_error_taxonomy=("single_rule_threshold_shift", "unobserved_state_invention"),
    forbidden_shortcuts=(
        "不得用处置门槛/事实门槛、动作层/性质层等标签提示答案，不得把规则条件与结论做成一眼可见查表，"
        "不得依赖题外法律、警务或行业常识补全规则。"
    ),
    adjacent_boundaries="单一规则内事实变化归 O15；不可见区间时序一致性归 O11。",
    content_controls=(
        "正控制：两规则部分匹配但适用对象和结论层级不同",
        "结论不变负控制：边界值变化后两规则映射仍不改变当前结论",
        "近邻控制：缺任一规则文本、版本、对象或阈值时 not_applicable",
        "表面交换控制：交换规则 A/B 名称和顺序不改变答案",
    ),
    allowed_answer_shape="一个自然业务判断及两套规则与当前事实的具体映射依据。",
    forbidden_answer_shape="规则条件查表题、显式双门槛标签化作答、依靠常识补齐规则。",
    default_evaluation_focus=(
        "是否区分两套规则的适用对象",
        "是否准确映射当前事实与各自阈值",
        "是否保持事实定性和行动处置的结论边界",
    ),
)
