from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O13_minimal_disqualifier",
    name="必要连接与推翻层级",
    ability_axis="minimal_required_link_failure",
    goal=(
        "在多个与同一推理链相关的候选事实中识别会破坏必要连接的事实，并区分该连接失效"
        "对局部链条和整体目标命题的不同影响。"
    ),
    reasoning_object="原必要连接、同类候选事实、可替代支持路径，以及局部连接和整体命题两个结论层级。",
    required_question_shape=(
        "给出原业务判断和若干同观察类别、同证据类型、同主体同时段的复核事实；"
        "只询问复核后目标业务判断是否仍成立及依据，不声明必要连接、唯一破坏项或其他支持路径。"
    ),
    content_transformation=(
        "在内部为候选事实分配保持连接、降低置信度、破坏连接、连接失效但整体仍有他路支持等角色；"
        "只改变一条必要连接的有效性，不把角色标签写入题面。"
    ),
    invariants=("主体不变", "时段不变", "目标命题不变", "证据类型不变", "候选信息粒度相近"),
    competition_structure="各复核事实都与原链相关且有部分解释力，破坏项不能因语法、信息量或否定词成为唯一显眼项。",
    preserved_parent_obligations=(
        "重建父题中事实到结论的必要连接",
        "区分局部连接失效与整体结论是否仍有其他支持",
    ),
    required_reasoning_tasks=("selected_fact_id", "broken_link_id", "claim_level_effect"),
    target_error_taxonomy=(
        "missed_link_break",
        "confidence_drop_vs_link_failure_confusion",
        "unsupported_full_reversal",
    ),
    excluded_error_taxonomy=("general_confidence_change", "multi_stage_event_chain_break"),
    forbidden_shortcuts=(
        "不得显示保持/减弱/破坏/整体仍成立等角色，不得声明存在唯一破坏项，"
        "不得用明显外围事实陪跑或把完整必要链写成作答步骤。"
    ),
    adjacent_boundaries="一般置信度变化归 O15；从未预设集合中发现充分关系归 O10；多阶段状态链断点不归本算子。",
    content_controls=(
        "正控制：一项事实破坏必要连接并改变相应结论层级",
        "结论不变负控制：连接失效但整体命题由独立路径继续支持",
        "近邻控制：只降低置信度的样本归 O15",
        "表面交换控制：交换候选事实顺序不改变答案",
    ),
    allowed_answer_shape="对复核后业务判断的开放式结论，并自行指出失效连接、其他路径和结论上限。",
    forbidden_answer_shape="显式选择唯一推翻项、题面预标逻辑角色、把局部失效直接等同整体翻转。",
    default_evaluation_focus=(
        "是否识别真正破坏必要连接的复核事实",
        "是否区分连接失效与普通置信度降低",
        "是否正确限定局部连接和整体命题的影响层级",
    ),
)
