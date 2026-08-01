"""Compatibility collection for the individually defined O19-O33 specs.

Each operator now lives beside O10-O18 in its own module.  Keep this export
stable for callers that consume the ordered new-operator collection.
"""

from .O19_multi_entity_role_binding import SPEC as O19_SPEC
from .O20_multistage_event_breakpoint import SPEC as O20_SPEC
from .O21_object_provenance_identity import SPEC as O21_SPEC
from .O22_path_topology_reachability import SPEC as O22_SPEC
from .O23_observation_reliability_conflict import SPEC as O23_SPEC
from .O24_multi_hypothesis_residual_ranking import SPEC as O24_SPEC
from .O25_procedural_invariant_frame import SPEC as O25_SPEC
from .O26_quantitative_threshold_propagation import SPEC as O26_SPEC
from .O27_cross_layer_conclusion_calibration import SPEC as O27_SPEC
from .O28_multihop_chain_closure import SPEC as O28_SPEC
from .O29_entity_identity_conflict_resolution import SPEC as O29_SPEC
from .O30_active_discriminative_observation import SPEC as O30_SPEC
from .O31_observation_accumulation_calibration import SPEC as O31_SPEC
from .O32_role_graph_critical_edge import SPEC as O32_SPEC
from .O33_cross_modal_support_boundary import SPEC as O33_SPEC


NEW_OPERATOR_SPECS = (
    O19_SPEC,
    O20_SPEC,
    O21_SPEC,
    O22_SPEC,
    O23_SPEC,
    O24_SPEC,
    O25_SPEC,
    O26_SPEC,
    O27_SPEC,
    O28_SPEC,
    O29_SPEC,
    O30_SPEC,
    O31_SPEC,
    O32_SPEC,
    O33_SPEC,
)
