from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O10_evidence_sufficiency_ladder",
    name="最小充分事实集",
    ability_axis="minimal_sufficient_fact_set",
    goal=(
        "固定同一目标命题，让回答者从多个互不蕴含的可观察事实中发现形成证明力跃迁的最小充分集合，"
        "而不是把任一相关事实或显眼强线索误当成充分事实。"
    ),
    reasoning_object="同一目标命题、互不蕴含的事实集合、最小充分成员和非成员相关事实。",
    required_question_shape=(
        "围绕一个自然业务判断呈现若干同粒度可观察事实；题面只问现有材料能否支持该判断及依据。"
        "最小事实集合、集合内部连接和必要成员不得在题面中标注或拆成步骤。"
    ),
    content_transformation=(
        "在保持目标命题和背景事实不变的前提下，引入一个集合级闭合关系：集合成员单独均不充分，"
        "移除任一必要成员后闭合失败，并混入至少一个有部分解释力但不属于最小集合的相关事实。"
    ),
    invariants=("主体不变", "时间范围不变", "目标命题不变", "结论层级不变", "事实粒度可比"),
    competition_structure=(
        "非成员事实必须能解释部分现象；最小集合成员不得因措辞、信息量或位置成为唯一显眼事实。"
    ),
    preserved_parent_obligations=(
        "组织父题中的关键事实并控制目标结论边界",
        "说明事实之间为何共同支持或仍不足以支持目标命题",
    ),
    required_reasoning_tasks=(
        "identify_minimal_sufficient_fact_ids",
        "explain_fact_set_connection",
        "separate_relevant_from_sufficient_facts",
    ),
    target_error_taxonomy=(
        "omitted_required_set_member",
        "relevant_fact_mistaken_as_sufficient",
        "missing_internal_set_connection",
    ),
    excluded_error_taxonomy=(
        "single_fact_counterfactual_direction_error",
        "predefined_x_y_joint_necessity_error",
    ),
    forbidden_shortcuts=(
        "不得展示保持/增强/减弱/翻转方向标签，不得声明唯一改变、决定性事实或最小充分集合，"
        "不得让正确事实独占否定词、极端词、完整因果链或明显更高信息量。"
    ),
    adjacent_boundaries=(
        "若任务预先给定 X/Y 并检验二者共同必要性，归 O12；若只改变一个事实观察方向，归 O15；"
        "若定位一条必要连接的破坏事实，归 O13。"
    ),
    content_controls=(
        "正控制：只有发现集合级闭合关系才能作答",
        "结论不变负控制：增加或移除非成员相关事实不改变结论",
        "成员消融控制：移除任一必要成员后闭合失败",
        "表面交换控制：交换事实顺序不改变答案与算子归因",
    ),
    allowed_answer_shape="一个自然业务判断及开放式依据，回答者自行给出最小事实集合和连接关系。",
    forbidden_answer_shape="材料强弱排序、固定方向判定、题面预填集合成员或逐步列出解题提纲。",
    default_evaluation_focus=(
        "是否识别集合级最小充分关系",
        "是否排除只有部分解释力的相关事实",
        "是否说明必要成员之间的闭合连接",
    ),
)
