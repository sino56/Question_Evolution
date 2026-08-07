"""Content prompt specification for O29_entity_identity_conflict_resolution."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O29_entity_identity_conflict_resolution",
    name="实体同一性冲突消解",
    ability_axis="entity_identity_conflict_resolution",
    goal="在身份连续、局部匹配和冲突绑定线索并存时，裁决哪些记录或行为属于同一实体。",
    required_question_shape="自然呈现相互支持又冲突的身份线索，要求作出一个归属判断并说明依据。",
    avoid="不要依赖唯一标识符或外部身份知识；不要把身份线索逐项分类给回答者。",
    evaluation_focus=("冲突线索是否被显式权衡", "局部匹配是否被过度推广", "归属强度是否校准"),
    reasoning_object="具有冲突边的实体身份图",
    transformation="把原题确定身份改造成局部相似、连续性证据和排他冲突共同作用的身份裁决。",
    invariants=("冲突与连续性事实均在题面", "局部相似不能自动压过排他冲突", "归属强度随证据完整度变化"),
    competition="候选身份各自拥有匹配线索，但在连续性或排他冲突上不同。",
    parent_obligations=("保留原题实体相关结论", "保留原题对身份确认的业务门槛"),
    reasoning_tasks=("聚合身份线索", "识别排他冲突", "检查局部与全程连续性", "校准归属"),
    semantic_axes=(
        _axis("identity_continuity", "检查跨时段身份连续性", ("continuity_gap_ignored",), "局部一致不能填补关键连续性缺口", ("时段记录", "连续线索")),
        _axis("conflict_resolution", "权衡支持线索与排他冲突", ("conflict_suppressed",), "未解释的排他冲突限制唯一归属", ("支持线索", "冲突线索")),
        _axis("binding_scope", "限制局部绑定的适用范围", ("local_binding_globalized",), "局部片段归属不能覆盖全程", ("局部事件", "全程主张")),
    ),
    target_errors=("appearance_only_identity", "conflicting_binding_ignored", "transfer_gap_ignored", "local_identity_overextended", "person_binding_substituted_for_object_binding"),
    excluded_errors=("主体行为角色方向", "物品来源流转", "观测质量可靠性"),
    shortcuts=("按最多匹配线索投票", "忽略排他时间冲突", "把同位置当同实体"),
    boundaries=("O19 绑定主体与行为角色", "O21 追踪对象来源", "O23 判断观测本身是否可靠"),
    positive_controls=("解除决定性身份冲突后，唯一归属结论可以增强",),
    negative_controls=("增加不具排他性的表面相似时，归属不应自动增强",),
    adjacent_controls=("使各观测来源本身可靠，避免滑向 O23",),
    surface_controls=("名称、位置、出现顺序和描述量不得与正确身份绑定",),
    balance_controls=("正确身份轮换拥有较少线索、更晚出现或较弱表面相似",),
    semantic_economy=(
            "共享实体画像只定义一次，各冲突线索只保留判定身份所需的差异。",
            "保留互相竞争且局部合理的身份事实，不展示完整排除表。",
            "不得让正确身份独占谨慎、核查或更完整的解释性语言。",
        ),
)
