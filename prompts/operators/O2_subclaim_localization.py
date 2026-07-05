from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O2_subclaim_localization",
    name="子结论边界定位",
    ability_axis="目标子判断定位",
    goal="压测模型能否发现目标结论中尚未被题干事实稳定支持的局部边界，而不是沿提示拆层作答。",
    required_question_shape="围绕目标结论提出一个局部成立性判断，要求回答现有事实支持到哪里、仍缺哪一个事实连接；不要显式要求指出哪一层已成立或未成立。",
    avoid="不要要求先把结论拆成多个子判断；不要把答案变成教学式分层分析；不要只问最少还缺什么。",
    default_evaluation_focus=(
        "是否定位到尚未稳定成立的局部结论",
        "是否区分现有事实支持范围与缺口",
        "是否把缺口绑定到题干事实而非泛化补证",
    ),
)
