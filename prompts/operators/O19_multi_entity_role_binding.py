"""Content prompt specification for O19_multi_entity_role_binding."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O19_multi_entity_role_binding",
    name="多实体角色绑定",
    ability_axis="multi_entity_role_binding",
    goal="在多实体、多时段和角色交换条件下，判断证据究竟绑定到哪个主体、什么角色与哪一段行为。",
    required_question_shape="给出可自然混淆的实体—行为关系，要求作出一个整体业务判断并说明最关键的绑定依据。",
    avoid="不要靠姓名相似、信息缺失或显式编号制造混淆；不要把实体表和角色答案直接列给回答者。",
    evaluation_focus=("实体是否保持区分", "角色方向是否正确", "局部证据是否被错误推广"),
    reasoning_object="带时间与行为方向的实体—角色关系图",
    question_construction="分散呈现多个实体在不同时段的局部动作，不预先命名实施者、协助者或受益者。",
    transformation="把原题的单主体证据改造成可交换但不可合并的实体—角色图，并让结论依赖正确绑定。",
    invariants=("原题核心业务判断保持不变", "每项关键行为都能由题面定位到实体与时段", "干扰实体能解释部分而非全部事实"),
    competition="竞争判断共享显著行为，但在执行者、受益者、协同行为或时间归属上不同。",
    parent_obligations=("保留原题关键证据义务", "保留原结论所需的证据强度边界"),
    reasoning_tasks=("追踪实体连续性", "恢复角色方向", "把局部行为约束到对应主体", "判断交换是否改变结论"),
    semantic_axes=(
        _axis("entity_identity", "保持主体身份连续且不合并相似实体", ("entity_merge",), "结论只归于被完整绑定的实体", ("时段标记", "可区分的连续线索")),
        _axis("role_direction", "区分实施、协助、承受与受益角色", ("role_direction_error",), "角色方向不等价于共同出现", ("行为方向线索", "关系上下文")),
        _axis("swap_tracking", "追踪角色交换前后的证据归属", ("swap_ignored",), "交换后的证据不得回填到交换前主体", ("交换事件", "交换前后连续信息")),
    ),
    target_errors=("entity_merge", "entity_swap_ignored", "role_direction_reversed", "local_binding_overgeneralized"),
    excluded_errors=("单纯缺少外部知识", "只比较证据充分层级", "仅判断观察本身是否可靠"),
    shortcuts=("按出场顺序默认角色", "把共同出现当作共同行为", "用单个显著动作替代全程绑定"),
    boundaries=("O10 关注证据充分性而非实体绑定", "O21 关注对象来源与同一性而非主体角色图", "O29 关注冲突身份线索的裁决"),
    positive_controls=("仅改变关键行为的实体归属时，合理结论应随之改变",),
    negative_controls=("交换非关键角色或无关实体标签时，结论不应改变",),
    adjacent_controls=("保持证据强度相同，仅让实体绑定成为决定因素",),
    surface_controls=("替换姓名、服装、位置等表面线索不得直接提示正确绑定",),
    balance_controls=("正确与干扰实体轮换承担先出现、信息多和措辞显著的角色",),
    semantic_economy=(
            "共享实体画像、时段和背景只定义一次；后续只写改变角色绑定的行为差异。",
            "保留判定所需的实体—角色—时段关系，不展开完整出场表或角色关系矩阵。",
            "不得用某一实体独占更完整叙述或总结句暗示正确绑定。",
        ),
)
