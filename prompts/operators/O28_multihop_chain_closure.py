"""Content prompt specification for O28_multihop_chain_closure."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O28_multihop_chain_closure",
    name="多跳链路闭合",
    ability_axis="multihop_chain_closure",
    goal="跨阶段、节点、实体和路径约束检查一条业务链是否整体闭合，而非被局部连通片段误导。",
    required_question_shape="用自然业务材料呈现跨节点链路，要求判断最终主张是否成立并说明依据。",
    avoid="不要用节点数量、显式链路表或固定跳数制造难度；不要提示逐跳验证。",
    evaluation_focus=("跨节点绑定是否连续", "局部链是否被误当完整链", "端点与终局要求是否闭合"),
    reasoning_object="跨实体、事件与路径条件的多跳链",
    transformation="把原题局部证据扩展为需跨节点承接的整体链，并加入局部成立但全链不闭合的近邻。",
    invariants=("每个必要跳转都有题面事实", "局部链具有真实解释力", "整体结论只能由全链闭合支持"),
    competition="竞争判断分别依赖局部高置信片段和跨节点完整链。",
    parent_obligations=("保留原题终局业务主张", "保留原题实体与时空约束"),
    reasoning_tasks=("连接跨节点事实", "保持实体与状态连续", "核对路径和端点", "判断整体闭合"),
    semantic_axes=(
        _axis("cross_node_binding", "保持相邻跳之间实体与状态承接", ("hop_binding_break",), "任一必要承接失效限制整体链", ("节点事实", "承接标识")),
        _axis("local_global_closure", "区分局部闭合与终局闭合", ("partial_chain_as_full",), "局部完整不得替代全链完整", ("局部路径", "终局条件")),
        _axis("joint_constraints", "联合应用时空与路径条件", ("constraint_fragmentation",), "分散满足不等于同一链联合满足", ("路径条件", "时间和端点")),
    ),
    target_errors=("cross_stage_link_omitted", "entity_binding_broken_across_hops", "partial_chain_treated_as_closed", "path_time_constraint_ignored", "missing_hop_filled_by_assumption"),
    excluded_errors=("单一事件断点", "单纯路径拓扑", "实体角色图"),
    shortcuts=("拼接来自不同实体的局部链", "从终局反推缺失跳转", "只核对最显著的中间节点"),
    boundaries=("O20 重点是状态链断点", "O22 重点是路径联合可达", "O11 重点是不可观测状态"),
    positive_controls=("补足唯一缺失的必要承接后，整体闭合判断应改变",),
    negative_controls=("改变不在主链上的旁支节点时，结论不应改变",),
    adjacent_controls=("保持单个跳转简单，让跨跳绑定承担难度",),
    surface_controls=("节点顺序、篇幅与命名不能映射到链路效力",),
    balance_controls=("完整与不完整链轮换拥有更顺畅叙事、更少节点和更显著终点",),
    semantic_economy=(
            "只保留目标主张依赖的多跳链子图和必要连接，不展开完整路径集合。",
            "共同节点和已知事实只出现一次，每跳只补充新的承接关系。",
            "不得用链路总结或显式闭合提示替回答者完成判断。",
        ),
)
