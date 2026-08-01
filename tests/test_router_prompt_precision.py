import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from operator_router import _operator_adjacency, _operator_cards
from operator_routing_cards import ROUTING_CARD_GATES
from prompts.operators import OPERATOR_SPECS
from prompts.router_prompt import build_router_prompt


def test_every_operator_card_has_explicit_hard_slots_reject_rule_and_boundaries():
    assert set(ROUTING_CARD_GATES) == set(OPERATOR_SPECS)

    cards = _operator_cards(list(OPERATOR_SPECS), _operator_adjacency())
    assert len(cards) == len(OPERATOR_SPECS)
    for card in cards:
        assert card["required_slots"]
        assert card["reject_if_missing"]
        assert card["adjacent_boundaries"]


def test_high_confusion_cards_name_the_required_hard_structure():
    cards = {
        card["operator_id"]: card
        for card in _operator_cards(list(OPERATOR_SPECS), _operator_adjacency())
    }

    assert any("预期出口窗口" in slot for slot in cards["O11_unobserved_state_attribution"]["required_slots"])
    assert any("至少两个可竞争实体" in slot for slot in cards["O19_multi_entity_role_binding"]["required_slots"])
    assert any("路径图中的节点与可通行边" in slot for slot in cards["O22_path_topology_reachability"]["required_slots"])
    assert any("可写结论或行动" in slot for slot in cards["O27_cross_layer_conclusion_calibration"]["required_slots"])
    assert any("跨实体、阶段、节点或路径" in slot for slot in cards["O28_multihop_chain_closure"]["required_slots"])
    assert any("来源、独立性或依赖传播" in slot for slot in cards["O31_observation_accumulation_calibration"]["required_slots"])


def test_router_prompt_requires_hard_slot_gate_overlap_resolution_and_audit_only_output():
    prompt = build_router_prompt(
        {},
        compact_input={
            "sample_id": "precision-prompt",
            "prompt": "根据已有材料控制结论强度。",
            "overscore_diagnosis": {"target_failure_mode": "将线索越级写成行动结论"},
            "operator_cards": [],
        },
    )

    for expected in (
        "内部判断顺序",
        "任务契约",
        "目标失败机制",
        "required_slots",
        "候选门禁",
        "近邻选择策略",
        "operator_decision_audit",
        "不参与执行、排序、二次过滤或补选",
        "O27_cross_layer_conclusion_calibration",
    ):
        assert expected in prompt
    assert "拉车门" not in prompt
    assert "笑气" not in prompt
