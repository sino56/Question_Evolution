from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O18_baseline_scope_mismatch",
    name="基准样本范围错配",
    ability_axis="统计基线适用范围识别",
    goal=(
        "测试模型能否识别统计基线的样本范围不一致时不能直接混用，"
        "例如完整通行记录与中途停留记录不能合并为同一个正常通行基准。"
    ),
    required_question_shape=(
        "给出同一对象或同一路径的两个看似相关基线来源，二者样本范围存在细小差异；"
        "要求判断哪个业务表述更稳妥，或原统计比较是否仍可保留。"
    ),
    avoid=(
        "不要明说基准范围不一致或样本口径错配；不要要求泛泛评价统计质量；"
        "不要用明显无关的外部基线作为干扰项。"
    ),
    default_evaluation_focus=(
        "是否识别完整通行记录与中途停留记录等基线口径差异",
        "是否避免混用不同样本范围上推异常结论",
        "是否把统计比较维持在题干基线可支持的层级",
    ),
)
