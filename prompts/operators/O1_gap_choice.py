from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O1_gap_choice",
    name="事实缺口识别",
    ability_axis="独立必要条件识别",
    goal="让模型判断当前事实与目标结论之间仍缺少哪类独立必要条件，避免直接给出候选答案路径。",
    required_question_shape="围绕原题事实与目标结论提出一个单点判断问题，要求说明现有事实无法单独推出结论的关键缺口；不要显式要求在 A/B 缺口中选择。",
    avoid="不要给出两个固定候选让模型二选一；不要暴露最小前提、最小跳步等答案路径；不要把问题改成开放式补证清单。",
    default_evaluation_focus=(
        "是否识别独立必要条件缺口",
        "是否基于题干事实而非外部信息",
        "是否避免把结论线索直接暴露给答案",
    ),
)
