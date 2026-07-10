from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O17_action_vs_fact_threshold",
    name="处置触发与事实定性门槛",
    ability_axis="行动门槛与事实结论门槛区分",
    goal=(
        "测试模型能否区分足以触发处置的线索与足以写入事实定性的证据，"
        "尤其是在题面不给应急、事实等显性标签时仍守住报告表述边界。"
    ),
    required_question_shape=(
        "给出同一场景下两种业务报告或处置表述，二者都看似合理；"
        "要求判断哪种更稳妥，避免直接使用\"应急核查\"和\"事实定性\"标签。"
    ),
    avoid=(
        "不要明说处置门槛或事实定性门槛；不要直接问能不能事实定性；"
        "不要把正确边界写成标签式选项。"
    ),
    default_evaluation_focus=(
        "是否区分处置触发与事实定性",
        "是否避免把可处置线索写成已证实事实",
        "是否在无显性标签时选择更稳妥的报告表述",
    ),
)
