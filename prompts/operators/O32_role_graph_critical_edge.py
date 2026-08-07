"""Content prompt specification for O32_role_graph_critical_edge."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O32_role_graph_critical_edge",
    name="角色关系图最小边",
    ability_axis="role_graph_critical_edge",
    goal="在主体关系图中识别支撑业务结论的必要关系边、方向及可替代路径。",
    required_question_shape="自然描述主体间协同、控制或传递关系，要求作出一个整体业务判断并说明依据。",
    avoid="不要直接画图、标关键边或要求逐边分析；不要用角色数量制造难度。",
    evaluation_focus=("关系边方向是否正确", "关键边是否必要", "替代路径是否真正等价"),
    reasoning_object="带方向与功能语义的主体关系图",
    transformation="把原题主体列表改造成关系边效力不同、存在近似替代路径的角色图。",
    invariants=("关键关系均有题面行为依据", "共同出现不自动形成关系边", "整体结论依赖必要边或等价替代路径"),
    competition="竞争关系图共享多数节点与边，只在关键边方向、功能或替代效力上不同。",
    parent_obligations=("保留原题主体与业务结论", "保留原题关键协同行为"),
    reasoning_tasks=("恢复关系边", "判断边方向与功能", "识别必要边", "检查替代路径"),
    semantic_axes=(
        _axis("edge_semantics", "从行为事实恢复关系边的功能", ("cooccurrence_as_edge",), "共同出现不等于控制、协助或传递", ("行为事实", "关系语义")),
        _axis("edge_direction", "判断关系从谁指向谁", ("edge_direction_reversed",), "方向错误会改变角色与效力", ("实施者", "承受或受益者")),
        _axis("criticality_alternative", "判断边是否必要及替代路径是否等价", ("false_alternative_path",), "表面绕行不等于功能替代", ("目标功能", "候选路径")),
    ),
    target_errors=("critical_edge_missed", "noncritical_edge_treated_as_necessary", "role_direction_reversed", "cooccurrence_promoted_to_coordination"),
    excluded_errors=("实体身份冲突", "对象来源", "事件状态链"),
    shortcuts=("按中心节点猜关键边", "把共现当协同", "忽略边的方向与功能"),
    boundaries=("O19 侧重实体—行为角色绑定", "O13 侧重单个最小失效连接", "O29 侧重身份线索冲突"),
    positive_controls=("删除或反转真正必要边时，整体结论应改变",),
    negative_controls=("改变冗余或无关边时，整体结论不应改变",),
    adjacent_controls=("实体身份保持清晰，避免将关系错误混同身份错误",),
    surface_controls=("节点中心度、出场顺序、关系措辞强度不得泄露关键边",),
    balance_controls=("关键边轮换位于中心或边缘、早或晚、显式或间接的表面位置",),
    semantic_economy=(
            "只呈现目标角色结论依赖的角色节点和关键关系边，不展开完整关系图。",
            "共享角色背景只写一次，候选关系只说明方向或必要连接的差异。",
            "不得以关系描述量、节点编号或结论总结暴露关键边。",
        ),
)
