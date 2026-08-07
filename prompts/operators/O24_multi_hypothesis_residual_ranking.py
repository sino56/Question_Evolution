"""Content prompt specification for O24_multi_hypothesis_residual_ranking."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O24_multi_hypothesis_residual_ranking",
    name="多假设残差排序",
    ability_axis="multi_hypothesis_residual_ranking",
    goal="比较候选解释对事实的覆盖、冲突、残差与额外假设成本，给出校准后的解释排序。",
    required_question_shape="自然呈现可竞争的业务解释与事实，要求作出一个业务判断并说明依据。",
    avoid="不要要求固定数量的假设或形式化打分表；不要让某个解释靠标签或篇幅明显胜出。",
    evaluation_focus=("事实覆盖是否完整", "关键冲突是否被识别", "残差与额外假设成本是否进入排序"),
    reasoning_object="候选假设—事实覆盖与残差关系",
    transformation="把原题单一解释改造成各自覆盖部分事实、留下不同关键残差的竞争解释。",
    invariants=("候选解释均有真实支持", "材料包含有区分力的残差", "排序依据来自题面而非外部先验"),
    competition="解释之间在覆盖面、关键冲突和补充假设成本上交叉占优。",
    parent_obligations=("保留原题关键现象", "保留原题结论的不确定性边界"),
    reasoning_tasks=("匹配解释与事实", "识别关键冲突", "评估残差", "控制额外假设成本"),
    semantic_axes=(
        _axis("coverage", "比较解释能自然覆盖的事实", ("salient_fact_only",), "覆盖显著事实不等于覆盖关键事实", ("事实集合", "解释机制")),
        _axis("conflict_residual", "定位解释无法容纳的关键事实", ("critical_residual_ignored",), "未解决关键残差限制排序", ("冲突事实", "残差影响")),
        _axis("assumption_cost", "识别维持解释所需的额外补设", ("assumption_cost_ignored",), "依赖更多未给定补设的解释应降权", ("题面事实", "必要补设")),
    ),
    target_errors=("single_fact_dominance", "coverage_conflict_tradeoff_ignored", "residuals_not_compared", "extra_assumption_cost_ignored", "plausibility_label_substituted_for_evidence"),
    excluded_errors=("下一步观测选择", "观察可靠性冲突", "接近替代解释仅导致正常化"),
    shortcuts=("按解释名称先验排序", "只数支持事实", "忽略解释所需的题外补设"),
    boundaries=("O16 关注接近替代解释是否使异常正常化", "O30 选择未来判别观测", "O23 先判断观测是否可靠"),
    positive_controls=("加入能区分关键残差的事实后，解释排序应可能改变",),
    negative_controls=("增加被各解释同样覆盖的事实时，排序不应改变",),
    adjacent_controls=("所有事实先视为可靠，避免把难度转移到 O23",),
    surface_controls=("假设名称、顺序、篇幅和专业程度不得与正确排序绑定",),
    balance_controls=("优势解释轮换承担更简短、更复杂或较晚出现的表面角色",),
    semantic_economy=(
            "共同事实只在题干出现一次，各假设只说明各自覆盖差异与残差线索。",
            "保留决定排序的最小竞争关系，不展示覆盖矩阵、完整解释清单或排名步骤。",
            "不得让某一假设独占完整事实并集或结论总结。",
        ),
)
