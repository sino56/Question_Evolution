"""Content prompt specification for O31_observation_accumulation_calibration."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O31_observation_accumulation_calibration",
    name="观测可靠度与累积校准",
    ability_axis="observation_accumulation_calibration",
    goal="区分独立增量、同源重复与依赖传播，校准多次观测累积后证据究竟增强了多少。",
    required_question_shape="自然呈现连续或多来源观测，要求作出一个业务判断并说明依据。",
    avoid="不要虚构精确概率或固定观测次数；不要把重复次数直接当置信度。",
    evaluation_focus=("观测是否独立", "重复是否提供增量", "累积后的结论强度是否校准"),
    reasoning_object="具有来源依赖的观测累积关系",
    transformation="把原题单次观测扩展为独立、重复和派生观测混合的证据累积。",
    invariants=("来源依赖可由题面判断", "不要求未给定的概率模型", "结论强度只随真实新增信息提升"),
    competition="观测集合在数量上接近或相反，但独立信息增量不同。",
    parent_obligations=("保留原题观测结论", "保留原题证据强度边界"),
    reasoning_tasks=("识别观测来源", "判断依赖或重复", "提取独立增量", "校准累计结论"),
    semantic_axes=(
        _axis("source_dependency", "判断观测是否来自同一底层来源", ("dependent_as_independent",), "同源派生不能重复计权", ("来源链", "生成关系")),
        _axis("incremental_information", "识别新观测带来的新增区分信息", ("repeat_as_increment",), "重复同一特征不等于新增支持", ("观测内容", "已有信息")),
        _axis("accumulated_strength", "把真实增量映射到结论强度", ("accumulation_overclaim",), "无精确模型时只作有依据的定性校准", ("独立增量", "结论门槛")),
    ),
    target_errors=("repetition_counted_as_independence", "shared_source_dependency_ignored", "low_quality_repeat_overweighted", "new_feature_increment_ignored", "cumulative_support_promoted_to_certainty"),
    excluded_errors=("单次观测可靠性", "未来观测选择", "定量误差传播"),
    shortcuts=("按观测条数加权", "把不同表述当独立来源", "虚构概率提升"),
    boundaries=("O23 判断单项观测是否可靠", "O30 选择下一项判别观测", "O26 处理给定的定量误差关系"),
    positive_controls=("加入真正独立且有区分力的观测后，结论强度应可提升",),
    negative_controls=("复制、转述或同源派生观测时，结论强度不应提升",),
    adjacent_controls=("避免要求数值概率，保持在可验证的依赖与增量判断",),
    surface_controls=("观测数量、来源名称和重复措辞不得直接提示独立性",),
    balance_controls=("高增量集合轮换拥有更少条目、更短描述或更晚出现",),
    scene_content_seeds={
        "夜间连续拉车门": "比较同角度低质重复画面与新增侧面清晰画面的信息增量。",
        "仓储搬运": "区分同一摄像头重复观测与不同点位形成的独立链路证据。",
        "望风协同": "识别同一连续片段中的多次同步出现并非多份独立支持。",
        "居民楼出入": "累积行为模式支持，但不越级确认角色或违法性质。",
    },
    semantic_economy=(
            "共享画面内容只写一次，各观测只补充来源依赖、视角或新增特征。",
            "保留判断独立性和累积价值所需的来源关系，不复制观察全文或统计表。",
            "不得用总结句暗示哪些观测可累计或不能累计。",
        ),
)
