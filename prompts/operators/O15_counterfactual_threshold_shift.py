from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O15_counterfactual_threshold_shift",
    name="比较量与结论门槛",
    ability_axis="single_quantity_threshold_effect",
    goal=(
        "在其他事实和门槛保持不变时，只改变一个核心事实，判断它如何影响同一个被比较的语义量，"
        "并把结论限制在题面给定的层级和门槛范围内。"
    ),
    reasoning_object="一个变化事实、一个语义比较量、固定门槛或无阈值偏序，以及同层级目标结论。",
    required_question_shape=(
        "围绕且只围绕事件可能性、证据支持度、命题成立度、行动门槛裕量或事实认定门槛裕量之一；"
        "题面只问事实变化后同一业务判断如何调整及依据，不给方向标签或跨层任务分解。"
    ),
    content_transformation=(
        "只替换或改变一个核心事实，其他事实、比较量、目标命题、结论层级和规则门槛保持不变；"
        "没有明确阈值时只表达支持度偏序，不强制整体翻转。"
    ),
    invariants=("其他事实不变", "被比较量不变", "目标命题不变", "结论层级不变", "给定门槛不变"),
    competition_structure="变化事实对同一语义量产生可解释影响，错误方向也具有局部解释力，不能靠评价词或标签排除。",
    preserved_parent_obligations=(
        "保留父题对核心事实和目标结论的连接",
        "控制事实支持、事实认定和行动处置之间的结论边界",
    ),
    required_reasoning_tasks=("changed_fact_id", "comparison_quantity", "direction_or_order", "conclusion_layer_effect"),
    target_error_taxonomy=(
        "comparison_quantity_mixed",
        "threshold_itself_changed",
        "unsupported_direction_flip",
        "cross_layer_effect_overclaimed",
    ),
    excluded_error_taxonomy=("two_explicit_rule_mapping", "required_link_failure"),
    forbidden_shortcuts=(
        "不得同时比较多个语义量，不得把事实变化写成规则阈值改变，不得在无明确阈值时宣称整体必然翻转，"
        "不得把保持/增强/减弱/翻转标签直接写入题面。"
    ),
    adjacent_boundaries="涉及两套明示规则归 O17；必要连接被破坏归 O13；证据、认定与处置跨层映射不归本算子。",
    content_controls=(
        "正控制：单事实变化使同一比较量发生可判定迁移",
        "结论不变负控制：变化不足以跨越既定门槛",
        "无阈值控制：只要求支持增加、减少或不增的偏序",
        "表面交换控制：交换前后版本顺序后语义方向同步反转而归因不变",
    ),
    allowed_answer_shape="围绕单一比较量给出变化后的自然业务判断及事实到门槛或偏序的依据。",
    forbidden_answer_shape="同时拆分异常、处置和定性三层；多变量反事实；无阈值整体翻转；题面预填改判链。",
    default_evaluation_focus=(
        "是否始终使用同一个比较量和结论层级",
        "是否保持规则门槛不变",
        "无明确阈值时是否只给出有依据的偏序而非强制翻转",
    ),
)
