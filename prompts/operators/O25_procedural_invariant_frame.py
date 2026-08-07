"""Content prompt specification for O25_procedural_invariant_frame."""

from .new_operator_support import _axis, _spec


SPEC = _spec(
    operator_id="O25_procedural_invariant_frame",
    name="程序不变量与参照系一致性",
    ability_axis="procedural_invariant_reference_frame",
    goal="在记录映射、单位、参照系和步骤依赖变化中保持程序不变量，判断结果是否仍可比较或成立。",
    required_question_shape="给出自然业务流程及参照变化，要求判断结果是否有效并说明依据。",
    avoid="不要写成操作手册复述或纯单位换算；不要显式要求逐步检查。",
    evaluation_focus=("参照系是否一致", "步骤依赖是否保持", "记录与对象映射是否正确"),
    reasoning_object="程序步骤、记录映射与参照系中的不变量",
    transformation="在原题流程中加入看似合理的参照、单位、顺序或映射变化，使结论依赖不变量是否保持。",
    invariants=("题面说明必要程序事实", "表面流程完整不保证语义可比", "改变非关键表述不影响判断"),
    competition="竞争判断都能复述流程，但只有一个保持对象映射、参照系与依赖关系。",
    parent_obligations=("保留原题业务结果", "保留原流程必须满足的实质条件"),
    reasoning_tasks=("识别程序不变量", "统一参照系与单位", "检查步骤依赖", "验证记录映射"),
    semantic_axes=(
        _axis("reference_frame", "统一观察方向、坐标或统计参照", ("reference_frame_mismatch",), "参照不同的结果不能直接比较", ("参照说明", "转换关系")),
        _axis("procedure_order", "检查关键步骤的前置依赖", ("order_dependency_ignored",), "形式完成但依赖倒置时结果无效", ("步骤事件", "依赖条件")),
        _axis("record_mapping", "保持记录、对象与测量值的对应", ("record_object_misbinding",), "映射错误不能由数值合理性修复", ("对象标识", "记录链")),
    ),
    target_errors=("reference_frame_mixed", "unit_inconsistency", "order_dependency_ignored", "record_mapping_shifted", "non_comparable_measurements_compared"),
    excluded_errors=("纯证据充分性", "纯数学误差传播", "实体行为角色交换"),
    shortcuts=("只看最终数值合理", "默认同名字段可比较", "把步骤齐全当作程序有效"),
    boundaries=("O10 不检查程序参照", "O12 检查共同必要事实而非程序不变量", "O26 计算误差与阈值传播"),
    positive_controls=("恢复关键映射或参照转换后，结果有效性应改变",),
    negative_controls=("更换不影响映射的记录格式时，结论不应改变",),
    adjacent_controls=("弱化数值计算负担，让不变量而非算术决定结论",),
    surface_controls=("字段名、单位写法和步骤编号不得直接标示错误",),
    balance_controls=("有效与无效流程轮换拥有更整齐格式、更完整措辞和更熟悉单位",),
    semantic_economy=(
            "共享程序、参照系和不变条件只写一次，版本只写实际改变的步骤或输入。",
            "保留验证不变量所需的规则和状态，不复制两套完整程序或检查表。",
            "不得在题面预告哪个步骤破坏不变量。",
        ),
)
