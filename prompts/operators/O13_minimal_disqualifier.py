from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O13_minimal_disqualifier",
    name="边界诱发式最小推翻事实",
    ability_axis="同一判断内的层级改变事实识别",
    goal=(
        "测试模型能否在多个都影响同一原评价的近似新增事实中，识别哪个真正改变结论层级，"
        "而不是把只改变置信度或处置优先级的事实当成推翻事实。"
    ),
    required_question_shape=(
        "先给一个原评价或原报告表述，再给 2-3 个同样贴近原判断的新增事实；"
        "要求判断原评价是否仍可保留、需要下调到哪一层，而不是显式问哪个最会迫使下调。"
    ),
    avoid=(
        "不要要求列完整推理路径；不要让一个候选明显同人同地、其他候选明显外围；"
        "不要把\"直接推翻/削弱/外围/无关\"等答案标签写进题面。"
    ),
    default_evaluation_focus=(
        "是否识别真正改变原评价层级的最小事实",
        "是否区分结论层级改变与置信度或处置优先级改变",
        "是否避免被同样贴近但非决定性的事实带偏",
    ),
)
