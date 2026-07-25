from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O16_close_alternative_normalization",
    name="相近解释的覆盖与残差",
    ability_axis="close_explanation_coverage_residual",
    goal=(
        "判断一个真实有竞争力的相近正常解释能否覆盖核心异常，还是只解释外围事实，"
        "并通过一个可观察 discriminator 区分两个假设。"
    ),
    reasoning_object="目标解释、一个相近正常解释、共享核心事实、外围事实、覆盖/冲突/残差矩阵和 discriminator。",
    required_question_shape=(
        "只引入一个相近正常解释，将决定性观察自然混入多个同粒度观察中；"
        "题面只问现有材料最支持什么结论及依据，不点名覆盖、冲突、残差或 discriminator。"
    ),
    content_transformation=(
        "在内部构造两个假设对事实的覆盖矩阵，使双方共享足够多核心事实；"
        "决定性分歧集中在一个可观察 discriminator，删除它后两个解释都可行且无法稳定排序。"
    ),
    invariants=("目标命题不变", "核心观测不变", "事实粒度可比", "只引入一个替代解释"),
    competition_structure="正常解释必须覆盖足够多共享事实；目标解释也应留下外围残差，不能形成一边完整、一边明显失败的信息量差距。",
    preserved_parent_obligations=(
        "组织父题核心与外围事实",
        "比较竞争解释对同一事实集合的覆盖、冲突与残差",
    ),
    required_reasoning_tasks=("compare_hypothesis_coverage", "identify_discriminator_fact_id", "explain_residuals"),
    target_error_taxonomy=(
        "peripheral_coverage_mistaken_as_core_coverage",
        "discriminator_ignored",
        "normal_explanation_overgeneralized",
    ),
    excluded_error_taxonomy=("multiple_hypothesis_ranking", "single_quantity_threshold_effect"),
    forbidden_shortcuts=(
        "不得使用正常/异常、正确/错误等评价标签，不得让 discriminator 直接重述目标命题或成为显眼否定，"
        "不得加入多个替代解释或把覆盖矩阵写成题面提纲。"
    ),
    adjacent_boundaries="单事实变化对比较量的影响归 O15；多个假设排序不归 O16；必要连接破坏归 O13。",
    content_controls=(
        "正控制：正常解释只覆盖外围事实，discriminator 保留核心异常",
        "结论不变负控制：正常解释覆盖核心异常后目标解释不再占优",
        "discriminator 消融控制：删除后两解释均可行且无法稳定排序",
        "表面交换控制：交换假设名称和顺序不改变语义答案",
    ),
    allowed_answer_shape="对现有材料最支持结论的开放式判断，并自行说明两解释的覆盖、冲突和关键分叉。",
    forbidden_answer_shape="显式问正常解释是否排除风险、给出多个假设排序、题面点名覆盖残差或 discriminator。",
    default_evaluation_focus=(
        "是否比较两个解释对核心与外围事实的真实覆盖",
        "是否识别可观察 discriminator",
        "是否避免把外围解释力误当成核心异常已被覆盖",
    ),
)
