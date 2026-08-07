"""Content prompt specification for O27_cross_layer_conclusion_calibration."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O27_cross_layer_conclusion_calibration",
    name="跨层结论校准",
    ability_axis="cross_layer_conclusion_calibration",
    goal="沿观测、支持、事实、可写结论与行动层传递证据效力，控制局部变化能否跨层改变最终表述。",
    required_question_shape="给出证据与业务规则，要求作出一个整体业务判断并说明依据。",
    avoid="不要把层级名称和答案模板写进题面；不要要求分层列出。",
    evaluation_focus=("证据效力是否跨层越级", "局部失效是否被正确传播", "结论措辞与行动是否匹配"),
    reasoning_object="从观测到业务行动的结论传导链",
    question_construction="给出原始规则、证据事实和待判断业务主张，不询问最高支持、结论边界或为何不能推出。",
    transformation="把原题单层判断扩展为跨层效力传导，使局部支持变化可能限制但不必然翻转终局结论。",
    invariants=("各层所需规则由题面提供", "局部证据变化的影响范围可判断", "最终表述不得强于最弱必要层"),
    competition="竞争结论共享事实基础，但在可写强度、保留措辞或行动门槛上不同。",
    parent_obligations=("保留原题事实判断", "保留原题业务行动或表述边界"),
    reasoning_tasks=("区分观测与事实", "传递支持效力", "校准可写结论", "核对行动门槛"),
    semantic_axes=(
        _axis("support_to_fact", "判断支持材料能否升级为事实", ("support_fact_collapse",), "支持不充分时只能保留事实判断", ("支持条件", "事实门槛")),
        _axis("fact_to_statement", "把事实强度映射到可写措辞", ("statement_overclaim",), "措辞不得超过事实确认程度", ("事实结论", "表述规则")),
        _axis("statement_to_action", "区分可写结论与可执行动作", ("action_layer_jump",), "满足表述条件不自动满足行动条件", ("行动规则", "门槛事实")),
    ),
    target_errors=("evidence_drop_treated_as_claim_false", "local_link_failure_treated_as_global_reversal", "suspicion_promoted_to_fact", "fact_support_promoted_to_action_threshold", "layer_specific_rule_ignored"),
    excluded_errors=("仅比较事实与行动两套门槛", "纯多阶段事件链", "只找最小否决事实"),
    shortcuts=("把支持材料直接写成事实", "局部证据失效就翻转所有层", "用保守措辞掩盖错误传导"),
    boundaries=("O17 聚焦事实与行动两套规则", "O13 聚焦最小失效连接", "O33 聚焦跨模态材料的支持上界"),
    positive_controls=("改变必要层的证据效力后，下游允许结论应相应改变",),
    negative_controls=("改变不参与最终结论的旁支支持时，终局不应改变",),
    adjacent_controls=("明确层间业务规则，避免题目退化为信息闭包问题",),
    surface_controls=("措辞强弱、层级顺序和段落位置不得直接提示正确答案",),
    balance_controls=("正确与过强结论轮换承担更专业、更保守或更简短的表面形式",),
    semantic_economy=(
            "保留跨层判断所需的事实张力和竞争证据，不把层级映射写成题面提示。",
            "共享观察背景只写一次，候选判断只呈现导致层级差异的事实。",
            "题面不得询问最高支持、结论边界或哪些内容不能直接推出；这些仅供答案键处理。",
        ),
)
