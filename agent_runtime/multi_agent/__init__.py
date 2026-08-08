"""Read-only, auditable advisor collaboration for the Agent control plane."""

from .advisor_registry import AdvisorSpec, get_advisor, list_advisors
from .evidence_pack import build_evidence_pack

__all__ = ["AdvisorSpec", "build_evidence_pack", "get_advisor", "list_advisors"]
