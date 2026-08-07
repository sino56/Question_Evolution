"""Shared constructors for individually owned O19-O33 content specs."""

from typing import Any, Dict, Optional, Sequence

from .base import OperatorPromptSpec


def _axis(
    name: str,
    reasoning_task: str,
    target_errors: Sequence[str],
    conclusion_boundary: str,
    content_dependencies: Sequence[str],
) -> Dict[str, Any]:
    return {
        "axis_name": name,
        "reasoning_task": reasoning_task,
        "target_errors": list(target_errors),
        "conclusion_boundary": conclusion_boundary,
        "content_dependencies": list(content_dependencies),
    }


def _spec(
    *,
    operator_id: str,
    name: str,
    ability_axis: str,
    goal: str,
    required_question_shape: str,
    avoid: str,
    evaluation_focus: Sequence[str],
    reasoning_object: str,
    question_construction: str,
    transformation: str,
    invariants: Sequence[str],
    competition: str,
    parent_obligations: Sequence[str],
    reasoning_tasks: Sequence[str],
    semantic_axes: Sequence[Dict[str, Any]],
    target_errors: Sequence[str],
    excluded_errors: Sequence[str],
    shortcuts: Sequence[str],
    boundaries: Sequence[str],
    positive_controls: Sequence[str],
    negative_controls: Sequence[str],
    adjacent_controls: Sequence[str],
    surface_controls: Sequence[str],
    balance_controls: Sequence[str],
    semantic_economy: Sequence[str],
    scene_content_seeds: Optional[Dict[str, str]] = None,
) -> OperatorPromptSpec:
    return OperatorPromptSpec(
        operator_id=operator_id,
        name=name,
        ability_axis=ability_axis,
        goal=goal,
        required_question_shape=required_question_shape,
        avoid=avoid,
        default_evaluation_focus=evaluation_focus,
        reasoning_object=reasoning_object,
        question_construction=question_construction,
        content_transformation=transformation,
        invariants=invariants,
        competition_structure=competition,
        preserved_parent_obligations=parent_obligations,
        required_reasoning_tasks=reasoning_tasks,
        semantic_axes=semantic_axes,
        scene_content_seeds=dict(scene_content_seeds or {}),
        target_error_taxonomy=target_errors,
        excluded_error_taxonomy=excluded_errors,
        forbidden_shortcuts=shortcuts,
        adjacent_operator_boundaries=boundaries,
        positive_controls=positive_controls,
        conclusion_invariant_negative_controls=negative_controls,
        adjacent_operator_controls=adjacent_controls,
        surface_swap_controls=surface_controls,
        hidden_role_balance_controls=balance_controls,
        allowed_answer_shapes=(
            "一个自然业务判断，随后用题面事实解释关键关系与结论边界",
            "可以承认局部不确定性，但必须给出当前材料允许的最强结论",
        ),
        forbidden_answer_shapes=(
            "按内部语义轴、事实角色或控制类型逐项作答",
            "复述 operator 名称、目标错误标签或预设答案方向",
            "用固定表格、清单或机械步骤替代业务判断",
        ),
        semantic_economy=semantic_economy,
        prompt_recipe_version="semantic_economy_structural_v1",
    )
