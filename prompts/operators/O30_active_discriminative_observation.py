"""Content prompt specification for O30_active_discriminative_observation."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O30_active_discriminative_observation",
    name="主动判别观测选择",
    ability_axis="active_discriminative_observation",
    goal="在当前证据无法区分竞争解释时，选择最能改变解释排序或排除分支的下一项可行观测。",
    required_question_shape="给出当前竞争解释与可执行观测条件，要求判断下一步最有判别力的观测及理由。",
    avoid="不要要求列观测清单或固定候选数量；不要把成本最低或信息最多直接等同判别力。",
    evaluation_focus=("观测能否区分竞争解释", "结果分支是否会改变判断", "可行性与代价是否合理"),
    reasoning_object="竞争解释与未来观测结果之间的判别关系",
    transformation="把原题的静态解释判断改造成选择下一项能区分关键分支的观测。",
    invariants=("候选观测均可由场景执行", "材料包含只重复现有信息的观测", "判别价值由不同结果对解释的影响决定"),
    competition="观测之间在易得、信息量和真正区分力上交叉占优。",
    parent_obligations=("保留原题竞争解释", "保留原题当前不确定性"),
    reasoning_tasks=("识别关键分歧", "预测观测结果分支", "比较判别力", "考虑可行性与代价"),
    semantic_axes=(
        _axis("disagreement_target", "定位解释真正分歧的可观测后果", ("wrong_disagreement_target",), "观测必须触及关键分歧", ("竞争解释", "差异预测")),
        _axis("outcome_branching", "判断不同结果能否改变解释排序", ("non_discriminative_observation",), "无论结果如何都不改变判断的观测价值有限", ("候选观测", "结果分支")),
        _axis("feasibility_cost", "在判别力成立后考虑可执行性与代价", ("information_volume_shortcut",), "信息多不等于可行且有区分力", ("执行条件", "成本限制")),
    ),
    target_errors=("most_suspicious_observation_selected", "outcome_branches_ignored", "hypothesis_coverage_not_updated", "unavailable_observation_selected", "certainty_gain_overstated"),
    excluded_errors=("当前多假设排序", "观测可靠性评估", "重复观测累积"),
    shortcuts=("选择最容易得到的观测", "选择信息字段最多的观测", "直接重复当前最支持的证据"),
    boundaries=("O24 排序当前解释", "O16 判断接近替代解释", "O23 判断既有观测可靠性"),
    positive_controls=("改变解释的关键分歧后，最佳下一观测应相应变化",),
    negative_controls=("加入不会改变任何解释预测的观测时，选择不应改变",),
    adjacent_controls=("当前解释排序保持接近，让未来判别而非静态优劣决定答案",),
    surface_controls=("观测选项的顺序、成本措辞和技术程度不得泄露判别力",),
    balance_controls=("最佳观测轮换承担更高成本、更短描述或较不显眼的表面角色",),
    scene_content_seeds={
        "笑气": "区分普通往返、存放补货和二次分装，选择最有区分力的可见变化。",
        "电动车": "区分偶然共现、同行和协同，比较同步、等待、汇合或独立离场的判别力。",
        "涉黄": "区分普通住户、接送和看管关系，选择能区分重复关系节点的观测。",
        "拉车门": "区分找车、误触和逐车试探，比较连续动作、目标选择或离场路径。",
    },
    semantic_economy=(
            "保留当前竞争假设和能改变选择价值的可观测差异，不预填结果分支或更新矩阵。",
            "共享场景只写一次，候选观测只写新增信息与可行条件。",
            "不得在题面说明哪项观测最有区分力或如何更新结论。",
        ),
)
