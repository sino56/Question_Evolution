import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Sequence


@dataclass(frozen=True)
class OperatorPromptSpec:
    operator_id: str
    name: str
    ability_axis: str
    goal: str
    reasoning_object: str
    required_question_shape: str
    content_transformation: str
    invariants: Sequence[str]
    competition_structure: str
    preserved_parent_obligations: Sequence[str]
    required_reasoning_tasks: Sequence[str]
    target_error_taxonomy: Sequence[str]
    excluded_error_taxonomy: Sequence[str]
    forbidden_shortcuts: str
    adjacent_boundaries: str
    content_controls: Sequence[str]
    allowed_answer_shape: str
    forbidden_answer_shape: str
    default_evaluation_focus: Sequence[str]
    generates_question: bool = True
    ability_axes: Sequence[str] = ()
    axis_reasoning_tasks: Mapping[str, Sequence[str]] = None
    axis_dependencies: Sequence[str] = ()

    def __post_init__(self) -> None:
        if self.axis_reasoning_tasks is None:
            object.__setattr__(self, "axis_reasoning_tasks", {})


def _json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _payload_example(schema: Dict[str, Any]) -> Dict[str, Any]:
    placeholders = {
        "array": [],
        "object": {},
        "string": "按事实账本和算子语义填写",
        "boolean": False,
        "number": 0,
    }
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    return {
        field: placeholders.get(properties.get(field), None)
        for field in schema.get("required", [])
    }


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
    operator_manifest: Dict[str, Any],
    fact_ledger: Sequence[Dict[str, Any]],
) -> str:
    # Imported lazily to avoid a module-import cycle: operator_contracts owns
    # mechanism metadata but derives its content fields from OPERATOR_SPECS.
    from operator_contracts import get_operator_contract

    contract = get_operator_contract(spec.operator_id)
    input_payload = {
        "sample_profile": sample_profile,
        "overscore_diagnosis": overscore_diagnosis,
        "evolution_state": evolution_state,
        "operator_route": operator_route,
        "operator_manifest": operator_manifest,
        "fact_ledger": list(fact_ledger),
    }
    return f"""
角色
你是一位 question evolution 题目生成专家。本轮只能执行指定 operator：{spec.operator_id}（{spec.name}）。

Operator 目标
{spec.goal}

内部推理对象
{spec.reasoning_object}

要求题型
{spec.required_question_shape}

内容变换
{spec.content_transformation}

内容不变量
{_json_block(list(spec.invariants))}

竞争结构
{spec.competition_structure}

必须保留的父题推理义务
{_json_block(list(spec.preserved_parent_obligations))}

回答者必须自行完成的推理任务
{_json_block(list(spec.required_reasoning_tasks))}

目标错误 taxonomy
{_json_block(list(spec.target_error_taxonomy))}

排除错误 taxonomy
{_json_block(list(spec.excluded_error_taxonomy))}

相邻算子边界
{spec.adjacent_boundaries}

内容控制
{_json_block(list(spec.content_controls))}

允许的回答形态
{spec.allowed_answer_shape}

禁止退化形态
{spec.forbidden_answer_shape}

禁止捷径
{spec.forbidden_shortcuts}

机制契约（只用于结构化输出，不得泄漏到题面）
{_json_block(contract.to_dict())}

必守边界
- 只生成一道完整、可独立作答的新题。
- 新题只执行当前 selected operator，不得混用或切换其他 operator；同一 operator 边界内可自然承载多个可区分语义轴。
- 不修改 rubric，不生成评分标准，不把 expected_evaluation_focus 写进 rubric。
- 不引入题干外事实；如必须比较候选事实，题面要给足比较依据。
- 当前调用已经通过适用性门控；不得用题外事实补齐缺口，也不得改用其他 operator。
- 每个实际使用的语义轴都要有独立、可归因的目标错误和结论边界；不得用"综合能力"兜底。
- 题面给事实，不给分层答案；不得把答案标签、目标错误名或完整推理路径直接写进题面。
- required_reasoning_tasks、内容控制、目标错误和角色标签都是生成器内部控制，不得写成题面步骤或作答提纲。
- 最终题面原则上只提出一个自然业务判断和开放式依据要求，不得用"逐项说明""分别列出""先……再……"替回答者完成分解。
- 不得靠题长、多事实、多任务制造表面复杂度；实体、事实、节点、候选、来源、假设、任务和语义轴均不设固定数量门槛。
- 不要把题目改成固定分层问法，也不要直接点名隐含补设位置。
- 好的输出应是边界诱发器，而不是答案拆解器：让模型在接近判断之间暴露是否守住证据边界。

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
  "evolution_strategy": "说明本 operator 压的唯一可归因错误、制造了哪两个接近但不等价的竞争判断，以及如何避免提示答案。",
  "ability_axis": "{spec.ability_axis}",
  "ability_axes": {_json_block(list(spec.ability_axes or (spec.ability_axis,)))},
  "axis_assignments": [],
  "axis_interactions": [],
  "target_subclaim": "本题压测的最小子判断或关键层级",
  "boundary_hypothesis": "一句话说明预期能力边界",
  "expected_qwen_failure": "一句话说明弱模型最可能犯的错",
  "expected_evaluation_focus": {_json_block(list(spec.default_evaluation_focus))},
  "preserved_parent_obligations": {_json_block(list(spec.preserved_parent_obligations))},
  "required_reasoning_output": {_json_block(list(spec.required_reasoning_tasks))},
  "target_error_taxonomy": {_json_block(list(spec.target_error_taxonomy))},
  "target_claim": "本题保持不变的目标命题；优先引用 operator_manifest 中的结构化值",
  "conclusion_layer": "题目要求判断的单一结论层级",
  "surface_fact_ids": ["evolved_prompt 中每一项业务事实对应的 fact ID"],
  "applied_transforms": ["只填写 transformation_contract.allowed_transforms 中实际使用的变换"],
  "operator_payload": {_json_block(_payload_example(dict(contract.operator_payload_schema)))},
  "surface_leakage_risks": {{
    "option_only": false,
    "fact_ablated": false,
    "surface_swapped": false,
    "parent_obligation_drift": false,
    "cross_operator_isomorphism": false
  }},
  "answer_contract": {{
    "answer_key": "结构化正确关系、方向或答案对象，不得只写泛化 focus",
    "decisive_fact_ids": ["只引用 fact_ledger 或人工 manifest 中存在的 fact ID"],
    "rubric_assertions": ["可由回答直接观察和评分的关键断言，不保存完整思维过程"]
  }},
  "notes_for_reference": "参考答案是否需要轻量补充；如基本适用则写基本适用"
}}
""".strip()
