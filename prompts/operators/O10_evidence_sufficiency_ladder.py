from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O10_evidence_sufficiency_ladder",
    name="最小充分事实集",
    ability_axis="minimal_sufficient_fact_set",
    goal=(
        "固定同一目标命题，从多个互不蕴含的可观察事实中识别形成证明力跃迁的最小充分集合，"
        "而不是看到一条更强或更显眼的线索就判断结论增强。"
    ),
    required_question_shape=(
        "围绕同一自然业务判断，同时呈现最小充分集合成员、具有部分解释力的相关事实和保持不变事实；"
        "只询问现有材料能否支持该判断及依据，不公开最小集合或成员角色。"
    ),
    avoid=(
        "不要使用固定层级模板、强弱排序或保持/增强/减弱/翻转标签；不要让某一事实成为唯一显眼的决定性事实；"
        "不要在题面声明最小集合、唯一改变、必要成员或逐步消融任务。"
    ),
    default_evaluation_focus=(
        "是否识别形成闭合关系的最小充分事实集合",
        "是否区分必要集合成员与仅有相关性或部分解释力的事实",
        "是否说明集合成员之间的事实连接而非依赖单条强线索",
    ),
    reasoning_object="同一目标命题下由多个互不蕴含事实构成的最小充分集合",
    question_construction="以同粒度原子观察事实组织材料，只问业务主张是否成立及依据，不标示事实组合或事实角色。",
    content_transformation=(
        "保留父题目标命题，组织若干同粒度事实，其中多个成员共同形成证明力跃迁；"
        "加入有部分解释力但不属于最小集合的相关事实，并在内部执行逐成员消融。"
    ),
    invariants=(
        "主体、时段、目标命题和结论层级保持不变",
        "最小集合成员彼此不构成改写或直接蕴含",
        "任一必要成员单独出现都不能决定答案",
        "相关干扰事实与集合成员的表面粒度保持可比",
    ),
    competition_structure=(
        "相关干扰事实能够解释部分现象，每个集合成员也只承担部分证明义务；"
        "只有回答者自行组织集合内部连接后才能发现闭合关系，不能凭信息量或措辞强度排除干扰。"
    ),
    preserved_parent_obligations=(
        "保留父题从多项事实组织证据关系的义务",
        "保留父题控制结论边界并解释为什么材料足以或不足的义务",
    ),
    required_reasoning_tasks=(
        "自行识别最小充分集合的成员",
        "说明集合成员如何共同闭合目标命题",
        "区分必要成员与只具相关性或部分解释力的事实",
        "理解移除任一必要成员后闭合关系为何不能保持",
    ),
    target_error_taxonomy=(
        "missing_minimal_set_member",
        "relevant_fact_treated_as_sufficient",
        "single_salient_clue_substitution",
        "missing_set_connection",
    ),
    excluded_error_taxonomy=(
        "predefined_xy_joint_necessity",
        "single_fact_direction_shift",
        "required_link_failure",
    ),
    forbidden_shortcuts=(
        "让正确集合成员独占主体、时段、否定词或高信息量",
        "直接标注必要事实、相关事实或最小充分集合",
        "要求做形式化排序或固定层级选择",
        "把成员消融步骤写入题面",
    ),
    adjacent_operator_boundaries=(
        "预先给定 X/Y 并判断各自独立和共同必要时归 O12",
        "识别会破坏既有必要连接的事实及局部/整体后果时归 O13",
        "单事实变化对同一量的方向或门槛影响归 O15",
    ),
    positive_controls=(
        "多个互不蕴含事实共同形成最小充分集合",
        "移除集合中任一成员后目标命题不再闭合",
    ),
    conclusion_invariant_negative_controls=(
        "增加相关事实但最小充分集合和结论不变",
        "增加冗余改写事实但不产生新的证明力跃迁",
    ),
    adjacent_operator_controls=(
        "预设 X/Y/X+Y 语义对照、应转交 O12 的近邻",
        "单个事实破坏原连接、应转交 O13 的近邻",
    ),
    surface_swap_controls=(
        "交换事实和场景版本的呈现顺序不改变最小集合",
        "等价改写来源名称和事实措辞不改变语义答案",
    ),
    hidden_role_balance_controls=(
        "集合成员和相关干扰事实使用相近语义负载、句式和观察粒度",
        "正确集合不独占完整因果链或权威来源",
    ),
    semantic_economy=(
        "共享背景和目标命题只出现一次；候选事实只写各自新增的证据关系。",
        "保留判断充分性所需的最小事实组合，不枚举同向属性或完整充分证据链。",
        "不得让正确集合独占完整解释、事实并集或更长的证据总结。",
    ),
    prompt_recipe_version="semantic_economy_normal_v1",
    allowed_answer_shapes=(
        "自然业务判断加开放式事实依据",
        "在论证中自行指出哪些事实组合形成充分支持",
    ),
    forbidden_answer_shapes=(
        "A/B 二选一或形式化集合排序",
        "按题面预给的必要/相关角色逐项作答",
        "只回答增强、减弱或翻转方向",
    ),
)
