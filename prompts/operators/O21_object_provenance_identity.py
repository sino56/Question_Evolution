"""Content prompt specification for O21_object_provenance_identity."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O21_object_provenance_identity",
    name="对象来源与同一性追踪",
    ability_axis="object_provenance_identity",
    goal="在转移、遮挡、重现和竞争来源条件下判断当前对象是否仍可绑定到原对象或特定来源。",
    required_question_shape="自然描述对象流转与可见性变化，要求判断来源或同一性主张是否成立并说明依据。",
    avoid="不要靠唯一编号直接给答案；不要把对象来源写成显式追踪表。",
    evaluation_focus=("来源链是否闭合", "对象同一性是否过度推定", "竞争来源是否被排除"),
    reasoning_object="对象的来源—转移—重现链",
    transformation="为原题对象加入可解释的转移缺口与竞争来源，使结论依赖来源链而非外观相似。",
    invariants=("对象特征与转移事实均来自题面", "竞争来源具有局部可解释性", "同一性结论强度与链路完整性匹配"),
    competition="同外观、近时段或共位置的对象分别来自不同来源链，只有完整链支持同一性。",
    parent_obligations=("保留原题对象相关业务结论", "保留原题对身份确认强度的要求"),
    reasoning_tasks=("追踪保管或转移链", "识别遮挡区间", "比较竞争来源", "校准同一性结论"),
    semantic_axes=(
        _axis("provenance_chain", "恢复对象来源与流转", ("source_chain_break_ignored",), "来源缺口限制可确认程度", ("来源事件", "转移记录")),
        _axis("identity_persistence", "判断遮挡或重现前后是否仍为同一对象", ("appearance_identity_substitution",), "相似外观不是同一性充分条件", ("稳定特征", "连续性证据")),
        _axis("competing_source", "比较可到达当前位置的其他来源", ("competing_source_ignored",), "未排除竞争来源时不得作唯一归属", ("竞争来源", "到达条件")),
    ),
    target_errors=("appearance_only_identity", "transfer_gap_ignored", "competing_source_ignored", "person_binding_substituted_for_object_binding", "occlusion_filled_by_assumption"),
    excluded_errors=("主体角色方向错误", "观察质量本身不可靠", "纯路径可达性"),
    shortcuts=("把外观相似当唯一身份", "用临近时间替代连续转移", "忽略可行竞争来源"),
    boundaries=("O19 追踪主体角色绑定", "O20 追踪事件状态链", "O29 裁决相互冲突的实体身份线索"),
    positive_controls=("补足关键转移证据后，同一性结论可以增强",),
    negative_controls=("改变无识别力的颜色或描述顺序时，结论不应改变",),
    adjacent_controls=("保持主体关系简单，让来源与对象连续性承担核心难度",),
    surface_controls=("外观显著度、命名与叙述篇幅不能与真实来源绑定",),
    balance_controls=("正确来源与竞争来源轮换承担更早出现、更多描述和更近位置",),
    semantic_economy=(
            "只保留对象来源、关键转移、遮挡和竞争来源所需的连续线索。",
            "稳定对象特征在公共题干出现一次，候选来源只写新增转移差异。",
            "不得把完整来源链或同一性结论作为某个候选的额外解释。",
        ),
)
