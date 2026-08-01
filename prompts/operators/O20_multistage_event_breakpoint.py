"""Content prompt specification for O20_multistage_event_breakpoint."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O20_multistage_event_breakpoint",
    name="多阶段事件链断点",
    ability_axis="multistage_event_chain_breakpoint",
    goal="要求模型在多阶段事件或状态转移链中找到真正阻断整体结论的断点及其后果。",
    required_question_shape="以自然叙事给出跨阶段状态变化，要求判断整体链是否成立并解释决定性断点。",
    avoid="不要用显式流程题、节点编号或提示“找断点”；不要把阶段数量本身当作难度。",
    evaluation_focus=("状态转移是否连续", "断点定位是否正确", "局部成立是否被误当整体成立"),
    reasoning_object="事件—状态转移图及其必要连接",
    transformation="把单步判断扩展为具有局部成立与关键失效并存的事件链。",
    invariants=("每个关键转移都有题面依据", "局部链能迷惑但不足以闭合整体结论", "断点与业务后果存在明确关系"),
    competition="竞争判断分别由局部顺畅链和完整闭合链支持，表面接近但整体效力不同。",
    parent_obligations=("保留原题终局结论", "保留原题关键事实的时序约束"),
    reasoning_tasks=("恢复事件顺序", "检查状态转移条件", "定位首个实质断点", "判断断点后的结论可否继续传递"),
    semantic_axes=(
        _axis("stage_continuity", "检查相邻阶段状态是否能合法承接", ("transition_assumed",), "缺失承接不得由结果反推", ("阶段状态", "承接条件")),
        _axis("breakpoint_effect", "区分形式缺口与足以阻断结论的实质断点", ("wrong_breakpoint",), "只有破坏必要连接的断点改变整体结论", ("必要连接", "后续依赖")),
        _axis("local_global", "区分局部片段成立与全链闭合", ("local_chain_overgeneralized",), "局部成功不能替代完整链路", ("局部链", "终局要求")),
    ),
    target_errors=("wrong_breakpoint", "state_transition_skipped", "temporal_edge_reversed", "partial_chain_treated_as_complete", "missing_node_filled_by_assumption"),
    excluded_errors=("仅做路径可达性", "只找最小新增否决事实", "纯信息闭包缺口"),
    shortcuts=("从终局倒推中间状态", "把最后出现的异常当断点", "忽略断点后的依赖传播"),
    boundaries=("O13 是最小失效事实，不要求恢复完整多阶段链", "O28 强调多跳链整体闭合", "O22 强调路径拓扑与窗口"),
    positive_controls=("修复实质断点后，整体链结论应能够重新成立",),
    negative_controls=("改变不参与必要连接的旁支事件时，整体结论不应改变",),
    adjacent_controls=("保留同一事件事实，让推理负担落在状态转移而非路径枚举",),
    surface_controls=("阶段名称、叙述顺序和篇幅不得泄露断点位置",),
    balance_controls=("断点在链中位置、显著程度及前后信息量保持均衡变化",),
    semantic_economy=(
            "仅呈现目标结论依赖的阶段状态和必要承接，不枚举完整流程或所有旁支。",
            "共享起点与终局只写一次，各阶段只保留改变承接关系的差异。",
            "不得用总结句、阶段编号或更长说明标出实质断点。",
        ),
)
