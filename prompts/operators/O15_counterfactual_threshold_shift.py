from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O15_counterfactual_threshold_shift",
    name="反事实门槛迁移",
    ability_axis="单变量变化后的保留与撤回边界",
    goal=(
        "只改变一个核心事实，观察模型能否说明原风险判断哪一部分仍可保留、哪一部分必须降级或撤回，"
        "而不是整体保留或整体推翻。"
    ),
    required_question_shape=(
        "明确只新增或替换一个题干事实，要求比较变化前后同一风险或业务判断的可支持层级；"
        "重点观察异常强度、处置触发和事实定性三者是否被分别处理。"
    ),
    avoid=(
        "不要加入多变量反事实；不要提供完整改判链条；不要诱导模型整体撤回风险判断或整体保留原判断。"
    ),
    default_evaluation_focus=(
        "是否只根据单一变量变化重排结论层级",
        "是否识别原判断中应保留与应撤回的部分",
        "是否避免整体保留或整体推翻风险判断",
    ),
)
