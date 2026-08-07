"""Content prompt specification for O33_cross_modal_support_boundary."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O33_cross_modal_support_boundary",
    name="跨模态支持边界",
    ability_axis="cross_modal_support_boundary",
    goal="对齐不同模态或来源的时间、对象和适用范围，处理冲突并给出融合材料允许的最强结论。",
    required_question_shape="自然组合视频、记录、信号、文本或检测结果，要求作出一个融合业务判断并说明依据。",
    avoid="不要按模态逐项汇报；不要把来源多或技术名称复杂等同证据更强。",
    evaluation_focus=("来源范围是否对齐", "时间与实体是否正确绑定", "融合结论是否超过共同支持边界"),
    reasoning_object="跨模态来源—时段—实体—结论支持图",
    transformation="把原题单来源证据改造成范围互补、局部冲突且需对齐后才能融合的多来源材料。",
    invariants=("各来源能力与限制由题面给出", "跨模态材料必须指向同一时段或实体才能合并", "融合结论不强于最弱必要支持"),
    competition="竞争结论分别由表面一致的多来源数量和正确对齐后的共同支持范围驱动。",
    parent_obligations=("保留原题业务事实与结论", "保留每类来源原有的证据边界"),
    reasoning_tasks=("识别来源能力范围", "对齐时间与实体", "解释冲突或互补", "校准融合结论"),
    semantic_axes=(
        _axis("source_scope", "判断每种模态能直接支持什么", ("modal_scope_overreach",), "某模态不能观察的属性不得由其背书", ("来源能力", "观测范围")),
        _axis("cross_modal_alignment", "对齐来源的时间、对象与事件", ("cross_modal_misbinding",), "未对齐材料不能简单叠加", ("时间标记", "实体或事件标记")),
        _axis("fusion_boundary", "从互补与冲突材料形成最大可支持结论", ("fusion_overclaim",), "多源一致只增强共同可见部分", ("对齐事实", "结论要求")),
    ),
    target_errors=("source_count_treated_as_strength", "scope_mismatch_ignored", "time_alignment_ignored", "cross_source_entity_link_assumed", "conflict_silently_dropped", "fusion_promoted_beyond_evidence"),
    excluded_errors=("单来源观测可靠性", "纯跨层行动门槛", "仅做数值基线融合"),
    shortcuts=("按来源数量投票", "把时间相近当事件相同", "用技术来源名称替代能力范围"),
    boundaries=("O23 判断单项观测可靠性", "O27 判断证据到行动的跨层传导", "O18 判断统计基线范围", "O14 判断题面信息闭包"),
    positive_controls=("完成关键时间或实体对齐后，融合结论可以增强",),
    negative_controls=("加入无法观察目标属性的来源时，结论不应增强",),
    adjacent_controls=("各来源单项可靠性保持可用，让跨模态对齐与融合承担核心难度",),
    surface_controls=("模态数量、专业名称、呈现顺序和篇幅不得与正确结论绑定",),
    balance_controls=("决定性来源轮换承担更弱措辞、更少信息或较晚出现的表面角色",),
    scene_content_seeds={
        "涉黄直播": "对齐信号区域与视频中的人员、设备出入，限制角色结论。",
        "笑气线索": "区分容器外观、充气动作、地点与时间记录各自支持的范围。",
        "拉车门": "对齐不同摄像头中的外观与路径观察，并在冲突时降低结论。",
        "电动车": "融合动作视频、车辆轨迹和时间同步，控制协同、身份与违法结论边界。",
    },
    semantic_economy=(
            "只呈现目标融合判断需要的各模态事实、时间/实体对齐和竞争关系。",
            "每个来源只保留新增支持，避免在结尾重复全部来源或写融合总结。",
            "题面不得提示限定范围、不能推出或答案边界；通过事实冲突让回答者自行校准。",
        ),
)
