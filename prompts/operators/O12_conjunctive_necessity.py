from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O12_conjunctive_necessity",
    name="独立性与共同必要性",
    ability_axis="independent_and_joint_necessity",
    goal=(
        "判断两个预先确定的事实条件是否各自提供不可替代的独立贡献，"
        "并且只有共同出现时才闭合同一目标命题，而不是把单调增益误当共同必要性。"
    ),
    required_question_shape=(
        "围绕一项自然业务判断呈现未标注、可比的观察材料，并要求说明依据；"
        "内部可用不同事实组合构造控制样本，但题面只保留一个中性任务，不出现 X/Y/Vx/Vy/Vxy 或联合闭合标签。"
    ),
    avoid=(
        "不要把题目写成强线索与弱线索排序，不要公开共同必要或待补条件清单；"
        "不要让 X+Y 场景因题长、信息量或肯定语气明显优于单条件场景。"
    ),
    default_evaluation_focus=(
        "是否识别 X 与 Y 各自独立且不可互相替代",
        "是否验证仅 X 和仅 Y 都不足、X+Y 才能闭合目标命题",
        "是否避免把信息增加或单条强线索误当共同必要关系",
    ),
    reasoning_object="预先确定的两个独立事实条件及其仅 X、仅 Y、X+Y 语义组合",
    question_construction="将两个条件及其组合效果自然混入可比观察材料，不公开 X/Y/X+Y 标签或共同满足提示。",
    content_transformation=(
        "保持目标命题不变，构造仅含 X、仅含 Y 和同时含 X+Y 的可比场景；"
        "同时准备联合闭合成立与不成立的内容控制，避免形成 X+Y 固定增强策略。"
    ),
    invariants=(
        "三个场景的主体、时段、目标命题和结论层级保持一致",
        "三个场景的信息粒度和表面结构保持可比",
        "X 与 Y 不是彼此改写、直接蕴含或同一事实的强弱版本",
        "除 X/Y 组合外不改变其他决定性事实",
    ),
    competition_structure=(
        "仅 X 和仅 Y 都能解释部分事实并支持较低层判断，X+Y 也不能靠信息量更大而显得必然正确；"
        "回答者必须识别两条件的独立贡献和共同闭合关系。"
    ),
    preserved_parent_obligations=(
        "保留父题组织多项事实以支撑同一目标判断的义务",
        "保留父题说明单项证据为何不足以及组合为何充分或仍不足的义务",
    ),
    required_reasoning_tasks=(
        "分别判断仅 X 和仅 Y 对目标命题的独立贡献",
        "判断 X 与 Y 是否不可互相替代",
        "判断 X+Y 是否共同闭合目标命题",
    ),
    target_error_taxonomy=(
        "single_clue_substitutes_joint_requirement",
        "monotonic_gain_mistaken_for_joint_necessity",
        "independence_not_established",
        "joint_closure_not_established",
    ),
    excluded_error_taxonomy=(
        "general_minimal_sufficient_set_discovery",
        "single_fact_threshold_shift",
        "dual_rule_scope_mapping",
    ),
    forbidden_shortcuts=(
        "公开 X/Y/Vx/Vy/Vxy 或共同必要标签",
        "让联合场景明显更长、更完整或更肯定",
        "在题面预填各条件贡献和联合闭合结论",
        "把任务写成形式化真值表填空",
    ),
    adjacent_operator_boundaries=(
        "从未预设的多事实中发现一般最小充分集合归 O10",
        "单个事实变化后的量或门槛迁移归 O15",
        "两套业务规则的适用范围映射归 O17",
    ),
    positive_controls=(
        "仅 X 与仅 Y 都不足而 X+Y 共同闭合目标命题",
        "X 与 Y 各自贡献不同且不可相互替代",
    ),
    conclusion_invariant_negative_controls=(
        "X+Y 仍不足以闭合目标命题",
        "X 或 Y 其中一项实际冗余，不能判为共同必要",
    ),
    adjacent_operator_controls=(
        "需要从三个以上事实中发现未知最小集合的 O10 近邻",
        "只改变单事实并比较支持方向的 O15 近邻",
    ),
    surface_swap_controls=(
        "交换 X/Y 对应事实和场景顺序不改变联合关系",
        "交换场景名称和呈现位置不改变语义答案",
    ),
    hidden_role_balance_controls=(
        "仅 X、仅 Y、X+Y 场景使用相近语义负载和句式",
        "联合场景不独占权威来源、极端词或完整因果链",
    ),
    semantic_economy=(
        "将共享场景、主体、时段和目标命题上提为公共题干一次，版本只呈现 X、Y 或其组合的差异事实。",
        "保留合取关系所需的最小事实，不复述公共背景或给联合版本补充完整解释。",
        "不得以事实并集、答案总结或明显更丰富的版本提示正确组合。",
    ),
    prompt_recipe_version="semantic_economy_normal_v1",
    allowed_answer_shapes=(
        "对自然业务场景作开放式判断并说明事实组合依据",
        "在回答中自行说明两项事实各自作用及组合关系",
    ),
    forbidden_answer_shapes=(
        "按 X/Y/Vxy 标签逐格填写",
        "固定 A/B 二选一或层级排序",
        "只因联合场景信息更多就判定增强",
    ),
)
