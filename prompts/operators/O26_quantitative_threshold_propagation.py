"""Content prompt specification for O26_quantitative_threshold_propagation."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O26_quantitative_threshold_propagation",
    name="定量阈值与误差传播",
    ability_axis="quantitative_threshold_error_propagation",
    goal="依据题面给定的公式、单位、区间和误差关系，判断结果区间是否跨越业务阈值及结论能否保持。",
    required_question_shape="给出业务量、转换或误差范围以及明确关系，要求作出一个阈值判断并说明依据。",
    avoid="不要依赖题外公式或复杂计算量；不要把固定数字数量当作难度。",
    evaluation_focus=("单位与公式是否正确", "误差区间是否传播", "阈值结论是否校准"),
    reasoning_object="带单位和不确定区间的定量推导链",
    question_construction="给出公式、单位、区间、误差关系和阈值，不提供中间计算或阈值比较结果。",
    transformation="把原题点估计改造成需要传播区间、容差或转换误差的阈值判断。",
    invariants=("所需公式由题面给出或属直接算术", "单位可由题面统一", "结论依赖区间与阈值关系而非计算繁琐度"),
    competition="竞争判断的点估计相近，但在误差方向、单位或阈值跨越上不同。",
    parent_obligations=("保留原题业务阈值含义", "保留原题数值证据与结论方向"),
    reasoning_tasks=("统一单位", "按给定关系传播误差", "形成结果区间", "比较区间与阈值"),
    semantic_axes=(
        _axis("unit_formula", "选择题面给定的正确单位与关系", ("unit_conversion_error",), "单位未统一的数值不可直接入阈值", ("单位定义", "给定公式")),
        _axis("error_propagation", "传播输入误差或容差到结果", ("point_estimate_substitution",), "点估计不能替代结果区间", ("输入区间", "传播关系")),
        _axis("threshold_crossing", "判断区间相对阈值的位置", ("threshold_crossing_ignored",), "跨阈值时不得给无保留的单向结论", ("结果区间", "业务阈值")),
    ),
    target_errors=("point_estimate_only", "uncertainty_not_propagated", "unit_conversion_error", "threshold_crossing_overstated", "interval_overlap_ignored"),
    excluded_errors=("程序步骤映射错误", "单变量反事实门槛迁移", "基线范围错配"),
    shortcuts=("只算中心值", "忽略最不利误差方向", "按数值大小猜单位"),
    boundaries=("O18 检查基线口径", "O15 检查单变量变化后的定性门槛", "O25 检查程序参照与映射"),
    positive_controls=("收窄决定性误差使区间不再跨阈值时，结论应可增强",),
    negative_controls=("改变不进入公式的旁支数值时，结论不应改变",),
    adjacent_controls=("题面给足计算关系，避免外部知识或程序不变量成为核心",),
    surface_controls=("数值精度、位数、排列和单位熟悉度不得泄露结论",),
    balance_controls=("阈值两侧样例轮换拥有更整齐数值、更短计算链和更熟悉单位",),
    semantic_economy=(
            "保留单位换算、误差传播、规则阈值和结论判断实际依赖的全部数值与定义。",
            "删除不参与计算或阈值关系的装饰数据，但不得因题面较长删去必要输入。",
            "不得给出中间计算、完整公式推导或预填阈值比较结果。",
        ),
)
