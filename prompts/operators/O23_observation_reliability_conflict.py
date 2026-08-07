"""Content prompt specification for O23_observation_reliability_conflict."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O23_observation_reliability_conflict",
    name="观测可靠性冲突",
    ability_axis="observation_reliability_conflict",
    goal="先判断观测事实本身的可见性、清晰度、来源一致性与可靠边界，再决定其能支持什么结论。",
    required_question_shape="给出存在质量限制或来源冲突的观测，要求作出一个业务判断并说明依据。",
    avoid="不要把来源标签直接等同可靠性；不要仅比较证据数量或权威措辞。",
    evaluation_focus=("观测是否可采信", "冲突是否得到解释", "结论是否超出可靠观测范围"),
    reasoning_object="带质量与来源约束的观测事实",
    question_construction="给出距离、遮挡、采样条件、来源关系和可见字段，不以来源标签直接声明可靠或不可靠。",
    transformation="把默认可信的原题观测改造成局部可靠、局部冲突且结论边界不同的观测集合。",
    invariants=("质量限制有题面依据", "冲突来源均可能解释部分现象", "可靠性判断先于事实推断"),
    competition="竞争判断分别把观测当完整事实或受限事实，差异来自可靠边界而非结论偏好。",
    parent_obligations=("保留原题所需事实类型", "保留结论强度与证据强度的对应"),
    reasoning_tasks=("识别可见性限制", "比较来源条件", "解释冲突", "限定可采信事实"),
    semantic_axes=(
        _axis("visibility_quality", "评估遮挡、清晰度与采样条件", ("quality_limit_ignored",), "不可辨细节不得升级为确定事实", ("观测条件", "可辨范围")),
        _axis("source_conflict", "判断冲突是否由来源范围或时段差异解释", ("source_conflict_flattened",), "冲突未解释时结论应保留", ("来源范围", "时间对应")),
        _axis("reliability_to_claim", "把可靠观测映射到允许的结论层级", ("reliability_overclaim",), "局部可靠仅支持局部结论", ("可靠事实", "结论门槛")),
    ),
    target_errors=("visibility_assumed", "similarity_treated_as_identity", "quality_limit_ignored", "observation_uncertainty_skipped", "downstream_claim_overstated"),
    excluded_errors=("证据充分性层级错误", "对象同一性链缺口", "多假设残差排序"),
    shortcuts=("默认清晰画面无误", "按来源权威性直接裁决", "用多数一致替代质量分析"),
    boundaries=("O10 默认事实可用后比较充分性", "O15 改变事实值而非观测可靠性", "O31 判断观测累积后的增量"),
    positive_controls=("改善决定性观测条件或解释冲突后，结论强度可以提升",),
    negative_controls=("增加同一受限来源的重复描述时，结论不应自动增强",),
    adjacent_controls=("保持对象绑定稳定，避免滑向对象同一性问题",),
    surface_controls=("来源名称、设备品牌与专业措辞不得替代可靠性事实",),
    balance_controls=("可靠与受限来源轮换承担更详细、更早或更肯定的表述",),
    semantic_economy=(
            "保留影响观测可靠性的可见条件与竞争来源，不重复解释其结论含义。",
            "题面以事实张力组织材料，不出现最高支持、不能直接推出等边界提示。",
            "多项观测使用相近的语气和信息负载，避免谨慎项成为唯一安全项。",
        ),
)
