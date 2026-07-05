from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O4_near_level_ranking",
    name="近似依据边界辨析",
    ability_axis="判据内外与证据层级区分",
    goal="让模型区分题干内可用依据、相似但不足以推出结论的信息，以及题外推断。",
    required_question_shape="提供少量相近事实或理由的判断场景，要求说明哪一项能直接支撑目标结论、哪一项只能作为线索；不要要求形式化排序或分层。",
    avoid="不要做简单可用/不可用二分；不要引入题外专业标准；不要使用大表格或显式排名模板。",
    default_evaluation_focus=(
        "是否区分题干内依据和题干外信息",
        "是否说明相近项之间的可用性差异",
        "是否排除相关但不足以推出结论的信息",
    ),
)
