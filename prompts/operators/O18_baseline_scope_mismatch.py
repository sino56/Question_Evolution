from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O18_baseline_scope_mismatch",
    name="基线纳入口径与异常性",
    ability_axis="comparable_baseline_inclusion_scope",
    goal=(
        "在两个同域但纳入口径不同的候选基线中，选择与当前事件可比的基线，"
        "并判断同一观测值的异常性是否随适用基线变化。"
    ),
    required_question_shape=(
        "题面给出两个同领域候选基线的纳入标准和足够的统计摘要，同时给出保持不变的当前观测；"
        "只询问当前观测应如何解释及依据，不提示口径错配、正确基线或先比较再判断的顺序。"
    ),
    avoid=(
        "不要依赖“更专业、更权威”或来源名称选择基线，不要只给口径标签而缺少判断所需摘要；"
        "不要把多步误差传播、置信区间或阈值计算混入本算子。"
    ),
    default_evaluation_focus=(
        "是否识别当前事件与候选基线的纳入口径可比性",
        "是否在保持观测值不变时选择适用基线",
        "是否正确判断更换适用基线后异常性改变或保持不变",
    ),
    reasoning_object="同域候选基线的样本纳入口径、统计摘要、当前事件和保持不变的观测值",
    content_transformation=(
        "保持当前观测值不变，引入两个同域但纳入标准不同的候选基线及其充分摘要；"
        "让异常性差异只来自可比样本范围，而非来源权威性或观测变化。"
    ),
    invariants=(
        "当前事件、目标异常判断和观测值保持不变",
        "两个候选基线属于同一业务领域",
        "两个基线的纳入标准和判断所需摘要均在题面中给出",
        "异常性变化只能由适用基线的纳入口径导致",
    ),
    competition_structure=(
        "两个基线都与当前事件表面相关并具有解释力，差异集中在是否纳入与当前事件同类的样本；"
        "错误基线不能因来源明显无关或摘要明显粗糙而直接排除。"
    ),
    preserved_parent_obligations=(
        "保留父题根据题面数据解释当前观测的义务",
        "保留父题说明比较对象为什么可比及结论边界的义务",
    ),
    required_reasoning_tasks=(
        "识别当前事件应满足的基线纳入条件",
        "比较两个候选基线与当前事件的可比性",
        "在观测值不变时判断适用基线改变对异常性的影响",
    ),
    target_error_taxonomy=(
        "baseline_inclusion_scope_ignored",
        "source_authority_substitutes_comparability",
        "observation_change_confused_with_baseline_change",
        "anomaly_direction_assumed",
    ),
    excluded_error_taxonomy=(
        "multi_step_error_propagation",
        "confidence_interval_calculation",
        "quantitative_threshold_estimation",
        "competing_causal_explanation",
    ),
    forbidden_shortcuts=(
        "用专业、权威或官方等标签暗示正确基线",
        "只给来源名称或口径标签而不给统计摘要",
        "让一个基线明显跨领域或与当前事件无关",
        "提示先比较口径再判断异常性的作答顺序",
    ),
    adjacent_operator_boundaries=(
        "比较因果解释对事实覆盖与残差归 O16",
        "单事实变化影响同一量或固定门槛归 O15",
        "多步误差传播和置信区间计算应进入定量阈值 family",
    ),
    positive_controls=(
        "选择可比基线后同一观测的异常性发生改变",
        "两个基线表面同域但只有一个纳入口径覆盖当前事件",
    ),
    conclusion_invariant_negative_controls=(
        "更换为另一适用基线后异常性结论仍不变",
        "两个基线纳入口径不同但当前观测在两者中均保持同一异常位置",
    ),
    adjacent_operator_controls=(
        "比较两个解释覆盖关系、应转交 O16 的近邻",
        "观测本身变化而基线不变、应转交 O15 的近邻",
    ),
    surface_swap_controls=(
        "交换基线 A/B 的呈现顺序和来源名称不改变可比性",
        "等价改写纳入标准和摘要不改变异常性答案",
    ),
    hidden_role_balance_controls=(
        "两个基线使用相近长度、统计粒度和来源可信度",
        "正确基线不独占更精确数字或更完整摘要",
    ),
    allowed_answer_shapes=(
        "自然解释当前观测并开放说明基线可比性依据",
        "在回答中自行说明适用口径及异常性后果",
    ),
    forbidden_answer_shapes=(
        "按题面预给的先后步骤作答",
        "仅凭来源名称或权威标签选择基线",
        "扩展为置信区间或多步误差传播计算",
    ),
)
