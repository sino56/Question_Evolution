from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O17_action_vs_fact_threshold",
    name="双规则边界映射",
    ability_axis="dual_rule_scope_mapping",
    goal=(
        "基于题面明示的两套业务规则，区分各规则的适用对象、阈值和结论范围，"
        "并把当前事实分别映射到处置触发条件与事实定性条件。"
    ),
    required_question_shape=(
        "题面完整给出两套规则文本、版本、适用对象和阈值，并设置边界值、两规则部分匹配或事实/行动层级交叉；"
        "只询问当前材料下应作出何种业务判断及依据。"
    ),
    avoid=(
        "不要省略规则而要求模型用常识补齐，不要把题目写成明显的规则-条件查表；"
        "不要分别点名事实判断和处置子任务，也不要用动作层/性质层标签替回答者完成映射。"
    ),
    default_evaluation_focus=(
        "是否识别两套规则各自适用对象、版本和阈值",
        "是否把当前事实分别映射到正确规则范围",
        "是否区分处置可以触发与事实定性已经成立",
    ),
    reasoning_object="题面明示的两套业务规则、各自适用对象和阈值，以及当前事实与规则的映射",
    question_construction="分别给出职责不同的原始规则、版本、对象字段和当前事实，不说明当前事实已适用哪条规则。",
    content_transformation=(
        "保留当前事实，加入两套完整且部分重叠的业务规则；"
        "通过边界值、部分匹配或层级交叉制造需要辨认规则范围的竞争判断。"
    ),
    invariants=(
        "两套规则文本、版本、适用对象和阈值均在题面中明确给出",
        "当前事实在比较过程中保持不变",
        "规则本身的阈值和适用范围不因事实变化而改变",
        "事实结论与处置结论保持不同声明层级",
    ),
    competition_structure=(
        "当前事实对两套规则都具有部分匹配，使两个业务判断表面上都合理；"
        "正确判断依赖规则对象、边界值和结论范围的具体映射，而不是关键词查表。"
    ),
    preserved_parent_obligations=(
        "保留父题根据完整事实作出业务判断并说明依据的义务",
        "保留父题区分证据支持、事实认定与行动处置边界的义务",
    ),
    required_reasoning_tasks=(
        "识别两套规则分别约束的对象和结论",
        "将当前事实与每套规则的条件及边界逐一对应",
        "说明处置触发与事实定性是否分别成立",
    ),
    target_error_taxonomy=(
        "rule_subject_scope_confusion",
        "rule_version_or_threshold_mismatch",
        "action_trigger_treated_as_fact_finding",
        "fact_threshold_treated_as_action_rule",
    ),
    excluded_error_taxonomy=(
        "single_rule_threshold_shift",
        "unobserved_state_attribution",
        "external_rule_recall",
    ),
    forbidden_shortcuts=(
        "依赖题外法规、警务常识或未给规则",
        "把两套规则简化为明显的关键词查表",
        "在题面标注动作层、性质层或正确规则",
        "把规则映射拆成预填步骤",
    ),
    adjacent_operator_boundaries=(
        "单一规则内一个事实变化对同一量的影响归 O15",
        "只依据端点和时序约束判断不可见假设归 O11",
        "题面未明示第二套规则时不得用 O17 补造规则",
    ),
    positive_controls=(
        "当前事实触发处置规则但尚未满足事实定性规则",
        "当前事实分别满足两套规则但产生不同层级结论",
    ),
    conclusion_invariant_negative_controls=(
        "边界事实变化后仍位于两套规则原有范围内，结论不变",
        "一套规则不适用但另一套规则结论保持不变",
    ),
    adjacent_operator_controls=(
        "只有单一规则和单事实变化、应转交 O15 的近邻",
        "只有端点与时序约束、应转交 O11 的近邻",
    ),
    surface_swap_controls=(
        "交换规则 A/B 的呈现顺序和名称不改变适用关系",
        "交换业务判断的表面顺序不改变规则映射答案",
    ),
    hidden_role_balance_controls=(
        "两套规则使用相近语义负载、具体度和权威表述",
        "正确规则不独占精确数字、强制语气或完整条件链",
    ),
    semantic_economy=(
        "两套完整且必要的规则可保留，但共同事实、版本和对象只写一次。",
        "只呈现完成规则映射所需的条件和边界，不重复规则释义、适用步骤或查表提示。",
        "不得以某条规则的更长解释或答案总结提示应选择的结论层级。",
    ),
    prompt_recipe_version="semantic_economy_normal_v1",
    allowed_answer_shapes=(
        "自然业务判断加基于明示规则和当前事实的开放式依据",
        "在论证中自行说明规则对象、范围和结论层级",
    ),
    forbidden_answer_shapes=(
        "固定双门槛或动作层/性质层标签化作答",
        "按题面预给映射表逐项填空",
        "引用题面外规则完成判断",
    ),
)
