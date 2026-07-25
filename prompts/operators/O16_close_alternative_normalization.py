from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O16_close_alternative_normalization",
    name="相近解释覆盖与残差",
    ability_axis="competing_explanation_coverage_and_residual",
    goal=(
        "判断一个具有真实竞争力的相近解释能否覆盖核心异常，还是只解释外围事实；"
        "求解依赖双方覆盖、冲突和残差关系，而不是“正常解释出现即风险消失”。"
    ),
    required_question_shape=(
        "题面只引入一个相近解释，并把决定性观察自然混合在多个同粒度观察中；"
        "只询问现有材料最支持什么业务判断及依据，不点名覆盖、冲突、残差或 discriminator。"
    ),
    avoid=(
        "不要引入多个替代解释或做多假设排序；不要用“正常/异常”标签、显性否定或目标命题改写充当 discriminator；"
        "不要让决定性观察成为唯一显眼、唯一精确或唯一高信息量事实。"
    ),
    default_evaluation_focus=(
        "是否比较目标解释与相近解释对核心和外围事实的覆盖",
        "是否识别双方留下的冲突和未解释残差",
        "是否依据可观察 discriminator 判断替代解释能否覆盖核心异常",
    ),
    reasoning_object="一个目标解释、一个相近替代解释、双方事实覆盖与残差以及一个可观察 discriminator",
    content_transformation=(
        "在保留目标解释和核心观察的同时加入一个能覆盖多项共享事实的相近解释；"
        "让双方的决定性分歧集中在一个可观察事实，并在内部执行 discriminator 消融。"
    ),
    invariants=(
        "主体、时段、目标业务判断和观测集合保持一致",
        "只引入一个替代解释",
        "目标解释与替代解释都能覆盖部分核心或共享事实",
        "删除 discriminator 后两个解释都保持可行且无法稳定排序",
    ),
    competition_structure=(
        "替代解释覆盖足够多共享事实而具有真实竞争力，但是否能覆盖核心异常取决于一个不显眼的可观察分歧；"
        "目标解释也应留下外围残差，避免成为完美且显眼的全覆盖答案。"
    ),
    preserved_parent_obligations=(
        "保留父题综合多项观察形成业务判断的义务",
        "保留父题识别核心事实与外围事实并控制结论边界的义务",
    ),
    required_reasoning_tasks=(
        "比较两个解释各自覆盖和冲突的事实",
        "识别双方仍未解释的核心或外围残差",
        "自行发现决定双方能否区分的可观察 discriminator",
        "判断替代解释是否真正覆盖核心异常",
    ),
    target_error_taxonomy=(
        "partial_coverage_treated_as_full_explanation",
        "core_vs_peripheral_coverage_confusion",
        "residual_ignored",
        "discriminator_missed",
        "normal_explanation_implies_no_risk",
    ),
    excluded_error_taxonomy=(
        "multi_hypothesis_ranking",
        "single_quantity_threshold_shift",
        "baseline_scope_selection",
    ),
    forbidden_shortcuts=(
        "使用正常/异常、正确/错误等评价标签",
        "让 discriminator 直接重述目标命题或显性否定替代解释",
        "让决定性观察成为唯一具体或唯一高信息量事实",
        "要求回答者按覆盖矩阵逐项填表",
    ),
    adjacent_operator_boundaries=(
        "单事实变化只影响同一量或门槛时归 O15",
        "两个候选基线的纳入口径比较归 O18",
        "三个及以上解释的排序应进入新的多假设 family",
    ),
    positive_controls=(
        "相近解释覆盖外围和部分共享事实，但无法覆盖核心异常",
        "相近解释能够覆盖核心异常，原目标解释不再占优",
    ),
    conclusion_invariant_negative_controls=(
        "加入相近解释后，discriminator 仍支持原业务判断不变",
        "替代解释只改变外围说明，不改变核心异常判断",
    ),
    adjacent_operator_controls=(
        "只改变单事实并比较支持度方向的 O15 近邻",
        "比较基线纳入口径而非因果解释的 O18 近邻",
    ),
    surface_swap_controls=(
        "交换两个解释的呈现顺序和名称不改变答案",
        "交换共享事实与外围事实的表面位置不改变覆盖关系",
    ),
    hidden_role_balance_controls=(
        "双方使用相近长度、具体度和因果完整度",
        "discriminator 与其他同粒度观察自然混合",
    ),
    allowed_answer_shapes=(
        "自然业务判断加开放式解释依据",
        "在论证中自行说明两个解释的覆盖、残差和关键分歧",
    ),
    forbidden_answer_shapes=(
        "按预给覆盖/冲突/残差字段逐项填写",
        "多个假设形式化排序",
        "仅因出现正常解释就撤回全部异常判断",
    ),
)
