from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O14_information_closure",
    name="信息闭包边界",
    ability_axis="information_closure",
    goal=(
        "作为内容原则，检查生成题面是否只使用已给事实，不补造中间状态、统计口径或业务阈值；"
        "O14 不设计独立题面，其运行校验留待方案第二部分。"
    ),
    required_question_shape=(
        "不生成独立题面；只保留“题面事实必须闭包、不得补造中间状态、统计口径或业务阈值”的内容身份。"
    ),
    avoid=(
        "不要把 O14 改写成信息闭包知识问答、隐含前提辨认题或两个结论的生成题型；"
        "不要在第一部分新增 validator、硬拒绝、资格状态或发布决策。"
    ),
    default_evaluation_focus=(
        "题面事实是否来自已给材料",
        "是否补造中间状态、统计口径或业务阈值",
        "是否把经验常识当作题面已发生事实",
    ),
    reasoning_object="生成题面的事实闭包",
    content_transformation="不执行题目内容变换；仅定义所有生成算子共同遵守的信息闭包原则",
    invariants=(
        "题面事实只能来自输入材料",
        "不得补造中间状态、统计口径或业务阈值",
        "经验、示例和建议不得写成已发生事实",
    ),
    competition_structure="不适用；O14 不是独立生成算子",
    preserved_parent_obligations=(
        "保留父题只依据题面事实作答的边界",
    ),
    required_reasoning_tasks=(),
    target_error_taxonomy=(
        "fabricated_intermediate_state",
        "invented_statistical_scope",
        "invented_business_threshold",
        "external_knowledge_as_observed_fact",
    ),
    excluded_error_taxonomy=(
        "independent_question_generation",
        "candidate_release_decision",
        "operator_qualification",
    ),
    forbidden_shortcuts=(
        "生成独立的信息闭包辨认题",
        "把 validator 规则或 findings 写入题面",
    ),
    adjacent_operator_boundaries=(
        "O14 只约束信息来源，不替代 O10-O13、O15-O18 的独有推理对象",
    ),
    positive_controls=(),
    conclusion_invariant_negative_controls=(),
    adjacent_operator_controls=(),
    surface_swap_controls=(),
    hidden_role_balance_controls=(),
    semantic_economy=(
        "O14 不生成独立题面；该契约只声明题面事实必须来自允许的可观察材料。",
        "信息闭包不承担语义冗余、答案提示或题面泄漏的门禁职责。",
    ),
    prompt_recipe_version="semantic_economy_normal_v1",
    allowed_answer_shapes=(),
    forbidden_answer_shapes=(),
    generates_question=False,
)
