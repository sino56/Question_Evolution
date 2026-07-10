from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O16_close_alternative_normalization",
    name="相近正常解释归一化",
    ability_axis="正常化解释对异常强度与风险保留的影响区分",
    goal=(
        "测试模型能否识别相近正常解释只降低异常强度或调整处置优先级，"
        "而不是直接排除风险或撤回全部可疑判断。"
    ),
    required_question_shape=(
        "围绕盲区超时、异常动作、可疑停留等场景，只新增一个相近正常解释；"
        "要求比较新增事实后原风险判断哪部分仍可保留、哪部分需要降级。"
    ),
    avoid=(
        "不要把新增正常解释写成完全排除风险的事实；不要加入多个替代解释；"
        "不要直接问正常解释是否能排除风险。"
    ),
    default_evaluation_focus=(
        "是否识别相近正常解释只能降低异常强度",
        "是否避免把可疑下降误写成风险消失",
        "是否区分风险判断保留、异常强度下调与事实定性撤回",
    ),
)
