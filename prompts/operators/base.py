import json
from dataclasses import dataclass, field
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
    # This keyword-only field is deliberately required.  An operator without an
    # explicit semantic-economy contract is not allowed to silently fall back
    # to a character-count convention.
    semantic_economy: Sequence[str] = field(kw_only=True)
    prompt_recipe_version: str = "semantic_economy_surface_v1"
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
    generator_visible_context: Optional[Dict[str, Any]] = None,
) -> str:
    # The generator intentionally receives only the surface-safe material.  In
    # particular, reference answers, candidate answers, rubrics, router
    # rationales and internal reasoning tasks are not rendered here: each can
    # accidentally turn an answer boundary into a question-side hint.
    visible_context = dict(generator_visible_context or {})
    visible_context.setdefault("original_question", prompt)
    surface_contract = {
        "operator_id": spec.operator_id,
        "question_shape": spec.required_question_shape,
        "avoid": spec.avoid,
        "semantic_economy": list(spec.semantic_economy),
        "prompt_recipe_version": spec.prompt_recipe_version,
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

题面构造契约（仅用于构造，不得把字段名、控制说明或预期方向复制到题面）
{_json_block(surface_contract)}

必守边界
- 只生成一道完整、可独立作答的新题。
- 新题只围绕当前 operator 家族内的一个清晰判断；允许该算子的多个语义轴在同一判断中自然耦合，但不得靠题长、任务数、选项数、反事实数、表格或复杂编号制造难度。
- 题面每个独立句段必须承担共享背景、可观察事实、决定性关系、竞争关系或自然提问之一；删除后不影响可回答性、竞争结构或结论边界的内容不得保留。
- 共享主体、时段、目标命题与不变背景只出现一次；版本、场景或选项只写各自的语义差异，不用重复句或字数对齐制造平衡。
- 题面只能呈现可观察事实、必要背景、竞争事实和自然业务任务；答案边界、评分意图、内部推理任务、预期错误与完整推理链只能留在题面之外。
- 不引入题干外事实；如必须比较候选事实，题面要给足比较依据。
- 只执行指定 operator，不自行切换 operator；适用性门控、资格状态和发布校验不属于本内容 Prompt。
- 干扰解释必须能解释部分事实，不能靠主体、时段、标签、语气、来源权威性或信息量差距被直接排除。
- 题面只提出一个自然业务判断和开放式依据要求；不得使用“逐项说明”“分别列出”“先……再……”或内部维度名称拆解答案。
- 不显示 operator 名称、能力轴、目标错误、内部事实角色、预期方向、决定性事实角色或“唯一改变”等执行说明；不得使用“结论边界”“最高支持”“不能直接推出”“仅凭现有材料”等答案方向提示。
- 若为多选题，所有选项的模态强度、核查语言和业务层级必须平衡；正确项不得因独有“疑似、线索、核查、限定范围”等谨慎标记而显得安全。

题面生成可见上下文
{_json_block(visible_context)}

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
  "balanced_semantic_load": "说明候选场景或选项如何保持相近的语义槽位、表面完整度与信息显著性；不比较字符数",
  "notes_for_reference": "参考答案是否需要轻量补充；如基本适用则写基本适用"
}}
""".strip()
