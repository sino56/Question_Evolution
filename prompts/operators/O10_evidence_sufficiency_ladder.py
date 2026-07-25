from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O10_evidence_sufficiency_ladder",
    name="近似判断竞争",
    ability_axis="同主体同场景下的证据层级微差识别",
    goal=(
        "让模型在同主体、同场景、同判断目标的多个近似结论之间识别哪一个最稳妥、哪一个因单一微小变量变化而越过证据层级，"
        "而不是做显性的材料强弱排序。"
    ),
    required_question_shape=(
        "给出 2-3 个同主体、同场景、同目标的候选处置或结论表述，要求判断哪一个最稳妥、"
        "哪一个越界；三个候选之间只能有一个微小事实变量改变证据层级。"
    ),
    avoid=(
        "不要把题目写成固定层级模板；不要直接暴露直接依据、低层依据、方向错误等答案标签；"
        "不要让 B 选项因主体、场景、时长或判断目标唯一匹配而显著强于其他选项。"
    ),
    default_evaluation_focus=(
        "是否在同主体同场景同目标候选中识别证据层级微差",
        "是否区分直接依据、低层依据与方向错误",
        "是否避免因近似判断表述相似而上推结论",
    ),
)
