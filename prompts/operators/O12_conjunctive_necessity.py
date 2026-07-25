from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O12_conjunctive_necessity",
    name="强线索替代未闭合门槛",
    ability_axis="强线索与未闭合结论门槛区分",
    goal=(
        "让模型在两个看似合理的处置或业务判断之间识别强线索只能支持较低层处置，"
        "不能替代尚未闭合的结论门槛。"
    ),
    required_question_shape=(
        "题干给出一个很强的可疑线索，并给出两个竞争表述：一个是较低层处置，"
        "一个是更高层业务定性；要求判断哪个表述更稳妥、哪个过界。"
    ),
    avoid=(
        "不要明说缺少均值、标准差、分级统计阈值、A/B 共同必要或待补条件清单；"
        "不要泛问还缺什么；不要同时压多个结论或多个缺口。"
    ),
    default_evaluation_focus=(
        "是否识别强线索只能触发较低层处置",
        "是否避免用强线索替代未闭合的更高层定性门槛",
        "是否把结论维持在题干事实可支持的最小层级",
    ),
)
