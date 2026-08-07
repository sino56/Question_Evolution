"""Prompt contract for source analysis.

The runtime keeps a deterministic fallback, but live implementations can use
this contract to produce the same ledger shape without exposing answer-side
material to the surface writer.
"""

SOURCE_ANALYSIS_PROMPT = """
Analyze only the supplied source question.  Split it into direct observations,
source claims, rule candidates, answer-direction claims, and derived summaries.
Do not use a reference answer, rubric, score, or prior model answer.  Every
observation must have fact_id, world_id, global_fact_key, origin_type, and a
source_locator.  Do not promote a conclusion, role inference, sufficiency
judgment, or answer direction to an observation fact.  A real external rule
without an auditable source/version must remain unresolved; it must not be
invented or repaired.
""".strip()
