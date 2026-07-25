from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O18_baseline_scope_mismatch",
    name="基线口径和异常性",
    ability_axis="baseline_inclusion_scope_anomaly",
    goal=(
        "在两个同域但纳入口径不同的候选基线中，选择与当前事件可比的基线，"
        "并判断同一观测值的异常性是否随适用基线改变。"
    ),
    reasoning_object="两个基线的来源、纳入标准和统计摘要，一个固定观测值，以及目标异常性命题。",
    required_question_shape=(
        "题面提供两个同域基线的可比较摘要和当前固定观测，只问该观测应如何解释及依据；"
        "不提示口径错配、正确基线或先比较再判断的顺序。"
    ),
    content_transformation=(
        "保持观测值和目标异常命题不变，只改变候选基线的样本纳入口径；"
        "异常性差异必须来自可比性，而不是来源权威性标签。"
    ),
    invariants=("观测值不变", "目标异常命题不变", "领域不变", "基线摘要口径可追溯"),
    competition_structure="两个基线都属于同一领域且都有部分表面相关性，不能靠来源名称、专业程度或权威标签排除。",
    preserved_parent_obligations=(
        "解释父题观测值与比较基准的关系",
        "控制异常性结论不超过适用基线支持范围",
    ),
    required_reasoning_tasks=("select_comparable_baseline", "explain_inclusion_scope_match", "assess_anomaly_effect"),
    target_error_taxonomy=(
        "baseline_scope_mismatch",
        "source_authority_used_instead_of_comparability",
        "anomaly_change_overclaimed",
    ),
    excluded_error_taxonomy=("multi_step_uncertainty_propagation", "quantitative_threshold_calculation"),
    forbidden_shortcuts=(
        "不得提示口径错配、正确基线或作答顺序，不得只提供来源名称和口径标签，"
        "不得用更专业、更权威等来源标签制造正确答案。"
    ),
    adjacent_boundaries="多步误差传播、置信区间或阈值附近定量计算不归 O18；单事实比较量迁移归 O15。",
    content_controls=(
        "正控制：更换为适用基线后异常性结论改变",
        "结论不变负控制：适用基线变化但异常性结论仍不变",
        "近邻控制：缺任一基线摘要、纳入标准或固定观测时 not_applicable",
        "表面交换控制：交换基线名称和顺序不改变答案",
    ),
    allowed_answer_shape="对固定观测的自然解释，并自行说明基线可比性和异常性后果。",
    forbidden_answer_shape="来源权威性比较、题面明示正确口径、先比较再判断的步骤化作答、多步定量计算。",
    default_evaluation_focus=(
        "是否根据纳入口径选择可比基线",
        "是否保持同一观测值并正确判断异常性后果",
        "是否避免用来源权威性代替可比性",
    ),
)
