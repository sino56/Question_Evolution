from .base import OperatorPromptSpec


SPEC = OperatorPromptSpec(
    operator_id="O12_conjunctive_necessity",
    name="独立性与共同必要性",
    ability_axis="independent_joint_necessity",
    goal=(
        "判断两项事实是否各自提供独立贡献，又是否只有共同出现时才闭合同一目标命题，"
        "避免把单调增益误当成共同必要性。"
    ),
    reasoning_object="内部语义对照 Vx、Vy、Vxy，以及同一目标命题下 X/Y 的独立贡献和联合闭合关系。",
    required_question_shape=(
        "以未标注且可比的业务场景呈现只有 X、只有 Y、X+Y 三种事实组合；"
        "题面不出现内部变量名，只问各场景能否支持同一目标判断及依据。"
    ),
    content_transformation=(
        "保持主体、时段、目标命题、信息粒度和表面结构可比，只改变 X/Y 的组合；"
        "同时支持联合闭合成立和不成立两类内容控制。"
    ),
    invariants=("主体不变", "时间范围不变", "目标命题不变", "信息粒度不变", "表面结构可比"),
    competition_structure="X 与 Y 单独均有部分解释力且互不蕴含；Vxy 是否闭合不能由信息量更多这一表面特征决定。",
    preserved_parent_obligations=(
        "识别父题事实各自对目标命题的贡献",
        "控制结论不超过事实组合实际支持范围",
    ),
    required_reasoning_tasks=(
        "assess_x_independent_contribution",
        "assess_y_independent_contribution",
        "assess_xy_joint_closure",
    ),
    target_error_taxonomy=(
        "monotonic_gain_mistaken_as_joint_necessity",
        "x_y_independence_confused",
        "joint_closure_overclaimed",
    ),
    excluded_error_taxonomy=("general_minimal_sufficient_set_discovery", "rule_threshold_mapping"),
    forbidden_shortcuts=(
        "题面不得出现 X/Y、Vx/Vy/Vxy、共同必要、联合闭合或方向标签；"
        "不得让 X+Y 版本因篇幅或权威措辞天然成为正确项。"
    ),
    adjacent_boundaries="需要从未预设的事实集合中发现最小充分集时归 O10；涉及两套明示规则时归 O17。",
    content_controls=(
        "正控制：X 与 Y 独立且 Vxy 联合闭合",
        "结论不变负控制：Vxy 仍不闭合，防止学习 X+Y 必然增强",
        "近邻控制：一般集合发现归 O10",
        "表面交换控制：交换三个场景顺序不改变语义答案",
    ),
    allowed_answer_shape="对三个自然业务场景的统一判断及自行构造的独立贡献、联合关系说明。",
    forbidden_answer_shape="在题面预标 X/Y 或共同必要标签、把三场景拆成固定作答提纲、只按信息量判定。",
    default_evaluation_focus=(
        "是否识别 X 与 Y 的独立贡献",
        "是否正确判断 Vxy 的联合闭合关系",
        "是否避免把信息增加直接等同于共同必要",
    ),
)
