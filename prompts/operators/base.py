import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence


@dataclass(frozen=True)
class OperatorPromptSpec:
    operator_id: str
    name: str
    ability_axis: str
    goal: str
    required_question_shape: str
    avoid: str
    default_evaluation_focus: Sequence[str]
    reasoning_object: str = ""
    content_transformation: str = ""
    invariants: Sequence[str] = ()
    competition_structure: str = ""
    preserved_parent_obligations: Sequence[str] = ()
    required_reasoning_tasks: Sequence[str] = ()
    semantic_axes: Sequence[Dict[str, Any]] = ()
    scene_content_seeds: Optional[Dict[str, str]] = None
    target_error_taxonomy: Sequence[str] = ()
    excluded_error_taxonomy: Sequence[str] = ()
    forbidden_shortcuts: Sequence[str] = ()
    adjacent_operator_boundaries: Sequence[str] = ()
    positive_controls: Sequence[str] = ()
    conclusion_invariant_negative_controls: Sequence[str] = ()
    adjacent_operator_controls: Sequence[str] = ()
    surface_swap_controls: Sequence[str] = ()
    hidden_role_balance_controls: Sequence[str] = ()
    allowed_answer_shapes: Sequence[str] = ()
    forbidden_answer_shapes: Sequence[str] = ()
    generates_question: bool = True


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def build_prompt(
    spec: OperatorPromptSpec,
    *,
    prompt: str,
    reference_answer: str,
    candidate_answer: str,
    rubric: Any,
    sample_profile: Dict[str, Any],
    overscore_diagnosis: Dict[str, Any],
    evolution_state: Dict[str, Any],
    operator_route: Dict[str, Any],
) -> str:
    input_payload = {
        "sample_profile": sample_profile,
        "overscore_diagnosis": overscore_diagnosis,
        "evolution_state": evolution_state,
        "operator_route": operator_route,
    }
    content_spec = {
        "reasoning_object": spec.reasoning_object,
        "content_transformation": spec.content_transformation,
        "invariants": list(spec.invariants),
        "competition_structure": spec.competition_structure,
        "preserved_parent_obligations": list(spec.preserved_parent_obligations),
        "required_reasoning_tasks": list(spec.required_reasoning_tasks),
        "semantic_axes": list(spec.semantic_axes),
        "scene_content_seeds": dict(spec.scene_content_seeds or {}),
        "target_error_taxonomy": list(spec.target_error_taxonomy),
        "excluded_error_taxonomy": list(spec.excluded_error_taxonomy),
        "forbidden_shortcuts": list(spec.forbidden_shortcuts),
        "adjacent_operator_boundaries": list(spec.adjacent_operator_boundaries),
        "positive_controls": list(spec.positive_controls),
        "conclusion_invariant_negative_controls": list(spec.conclusion_invariant_negative_controls),
        "adjacent_operator_controls": list(spec.adjacent_operator_controls),
        "surface_swap_controls": list(spec.surface_swap_controls),
        "hidden_role_balance_controls": list(spec.hidden_role_balance_controls),
        "allowed_answer_shapes": list(spec.allowed_answer_shapes),
        "forbidden_answer_shapes": list(spec.forbidden_answer_shapes),
    }
    return f"""
角色
你是一位 question evolution 题目生成专家。本轮只能执行指定 operator：{spec.operator_id}（{spec.name}）。

Operator 目标
{spec.goal}

要求题型
{spec.required_question_shape}

避免
{spec.avoid}

内部内容规格（只用于构造题目，不得把字段名、角色名、控制说明或预期方向复制到题面）
{_json_block(content_spec)}

必守边界
- 只生成一道完整、可独立作答的新题。
- 新题只围绕当前 operator 家族内的一个清晰判断；允许该算子的多个语义轴在同一判断中自然耦合，但不得靠题长、任务数、选项数、反事实数、表格或复杂编号制造难度。
- 不修改 rubric，不生成评分标准，不把 expected_evaluation_focus 写进 rubric。
- 不引入题干外事实；如必须比较候选事实，题面要给足比较依据。
- 只执行指定 operator，不自行切换 operator；适用性门控、资格状态和发布校验不属于本内容 Prompt。
- 严格执行 content_transformation 和 invariants；不得把其他算子的表面题型换名后冒充当前算子。
- 干扰解释必须能解释部分事实，不能靠主体、时段、标签、语气、来源权威性或信息量差距被直接排除。
- 保留 preserved_parent_obligations；required_reasoning_tasks 由回答者自行完成，不得预填为题面步骤或作答提纲。
- 题面只提出一个自然业务判断和开放式依据要求；不得使用“逐项说明”“分别列出”“先……再……”或内部维度名称拆解答案。
- 不显示 operator 名称、能力轴、目标错误、内部事实角色、预期方向、决定性事实角色或“唯一改变”等执行说明。
- 正控制、结论不变负控制、相邻近邻、表面交换和隐藏角色平衡均为内部构造约束，不得变成题面标签。

输入画像与路由
{_json_block(input_payload)}

原题
{prompt}

参考答案
{reference_answer}

候选答案
{candidate_answer}

现有评分标准（只用于理解原题，不得改写）
{_json_block(rubric)}

输出
返回合法 JSON 对象，不要输出 Markdown 或额外解释：
{{
  "evolved_prompt": "升级后的新题目，必须完整、可独立作答，并严格符合当前 operator。",
  "evolution_strategy": "说明本 operator 压测的核心推理对象、耦合了哪些同族语义轴、制造了哪些接近但不等价的竞争判断，以及如何避免提示答案。",
  "ability_axis": "{spec.ability_axis}",
  "target_subclaim": "本题压测的最小子判断或关键层级",
  "boundary_hypothesis": "一句话说明预期能力边界",
  "expected_qwen_failure": "一句话说明弱模型最可能犯的错",
  "expected_evaluation_focus": {_json_block(list(spec.default_evaluation_focus))},
  "notes_for_reference": "参考答案是否需要轻量补充；如基本适用则写基本适用"
}}
""".strip()
