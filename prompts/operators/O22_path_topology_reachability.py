"""Content prompt specification for O22_path_topology_reachability."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O22_path_topology_reachability",
    name="路径拓扑联合可达性",
    ability_axis="path_topology_joint_reachability",
    goal="联合检查路径拓扑、端点、时间窗口与通行约束，判断候选路径是否真正可达。",
    required_question_shape="给出自然场景中的候选路径与时空约束，要求判断关键主张是否可行并说明决定性约束。",
    avoid="不要变成复杂图算法或按边逐项计算；不要用路径数量制造难度。",
    evaluation_focus=("拓扑是否连通", "时间窗是否兼容", "端点和方向约束是否同时满足"),
    reasoning_object="受时空与方向约束的路径拓扑",
    transformation="把单一时间或位置线索扩展为需要联合满足拓扑、端点和窗口的可达性判断。",
    invariants=("题面提供判断所需的路径与窗口事实", "候选路径至少满足部分约束", "结论取决于约束联合而非单一显著边"),
    competition="竞争路径在距离、时间或连通性上各有优势，但只有联合满足者可达。",
    parent_obligations=("保留原题时空主张", "保留原题不可观察区间的边界"),
    reasoning_tasks=("恢复拓扑连接", "核对方向和端点", "叠加时间窗口", "判断联合可达性"),
    semantic_axes=(
        _axis("topology", "检查节点与边是否真实连通", ("false_connectivity",), "表面邻近不等于拓扑连通", ("节点关系", "边方向")),
        _axis("window_compatibility", "检查通行与观测窗口是否兼容", ("window_ignored",), "路径存在不代表窗口内可完成", ("时间窗", "耗时或开放条件")),
        _axis("endpoint_binding", "核对路径端点与目标主体或地点", ("endpoint_mismatch",), "到达相邻端点不等于到达目标", ("端点标识", "主体位置")),
    ),
    target_errors=("topology_ignored", "unreachable_path_selected", "travel_window_violation", "endpoint_identity_mixed", "single_path_assumed_without_exclusion"),
    excluded_errors=("纯粹未观测状态补设", "多阶段状态断点", "数值误差传播"),
    shortcuts=("按直线距离判断可达", "忽略单向或封闭边", "只验证一条时空约束"),
    boundaries=("O11 聚焦不可见状态归因", "O20 聚焦状态转移断点", "O28 聚焦跨节点多跳链整体闭合"),
    positive_controls=("改变决定性边或窗口后，可达性结论应相应改变",),
    negative_controls=("改变不在候选路径上的旁支边时，结论不应改变",),
    adjacent_controls=("避免引入额外事件因果，使判断集中于联合可达性",),
    surface_controls=("路径名称、绘制顺序和叙述长短不得暗示可行性",),
    balance_controls=("可行与不可行路径在距离、描述量和显著线索上保持平衡",),
    semantic_economy=(
            "只呈现目标可达性判断所需的节点、方向边、端点和时间窗口。",
            "共享地图背景和通行规则只写一次，不展开完整路网或全部候选路径。",
            "不得通过路径描述量、顺序或总结暗示可达结果。",
        ),
)
