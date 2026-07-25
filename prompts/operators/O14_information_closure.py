from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O14_information_closure",
    name="全局信息闭包校验",
    ability_axis="information_closure_validation",
    goal="对所有候选执行信息闭包检查，不生成独立题面。",
    reasoning_object="候选题面事实、可用 fact ID、事实类型和授权变换。",
    required_question_shape="validation_only：不得构造独立题面。",
    content_transformation="不执行内容变换，只核对候选是否越出事实账本和 allowed_transform。",
    invariants=("事实账本不变", "授权变换集合不变", "目标命题不变"),
    competition_structure="不适用；O14 只产生 validation findings。",
    preserved_parent_obligations=("保持父题所有可追溯事实边界",),
    required_reasoning_tasks=("map_surface_facts_to_fact_ids", "check_authorized_transforms"),
    target_error_taxonomy=(
        "unmapped_surface_fact",
        "invented_intermediate_state",
        "invented_threshold_or_baseline",
        "forbidden_fact_type_promoted",
        "unauthorized_transform",
    ),
    excluded_error_taxonomy=("question_generation_failure",),
    forbidden_shortcuts="不得以 O14 名义生成候选题，也不得把概率性泄漏风险升级为无证据硬拒绝。",
    adjacent_boundaries="O14 是所有生成算子的全局 validator，不与生成算子竞争归因。",
    content_controls=(
        "正控制：未授权事实或变换产生确定性 finding",
        "结论不变负控制：所有事实均可映射且变换获授权时通过",
        "历史回放控制：derived/example/suggestion 不得伪装成已发生事实",
    ),
    allowed_answer_shape="仅输出结构化 validation findings。",
    forbidden_answer_shape="任何独立 evolved_prompt 或候选题记录。",
    default_evaluation_focus=(
        "题面事实是否映射到 fact ID",
        "是否补造中间状态、口径或阈值",
        "是否使用禁止事实类型或未授权变换",
    ),
    generates_question=False,
)
