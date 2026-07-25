from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O14_information_closure",
    name="信息闭包边界",
    ability_axis="题干事实闭包内外区分",
    goal=(
        "测试模型能否只在题干事实闭包内推理，拒绝把经验常识、行业惯例或未给出的中间状态补进结论。"
    ),
    required_question_shape=(
        "给出两个都看似可从题干推出的近似结论，要求判断哪个结论仍不能仅凭题面推出；"
        "题面不直接提示\"题外前提\"或\"隐含补设\"。"
    ),
    avoid=(
        "不要显式问哪一步引入题外前提；不要把所有常识都一概排除；"
        "不要将题目改成提示模型背诵信息闭包原则。"
    ),
    default_evaluation_focus=(
        "是否识别依赖隐含补设的近似结论",
        "是否保留题干内可推出的较低层判断",
        "是否拒绝用经验常识补齐题面缺口",
    ),
)
