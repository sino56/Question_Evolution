from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O8_double_threshold_claim",
    name="性质门槛辨析",
    ability_axis="双门槛结论识别",
    goal="压测模型能否区分显眼动作已经发生与关键性质是否成立，而不把门槛直接拆给模型。",
    required_question_shape="围绕同一目标结论设置动作事实与性质判断并存的单点问题，要求说明现有事实是否足以推出关键性质；不要显式拆成两个门槛。",
    avoid="不要只追问动作是否发生；不要把性质判断藏在 rubric；不要扩展成多结论综合题。",
    default_evaluation_focus=(
        "是否区分动作发生与性质成立",
        "是否指出真正决定性质的缺口",
        "是否避免被显眼动作替代关键性质判断",
    ),
)
