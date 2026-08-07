"""Restricted-side planning contract.

This prompt is intentionally never given to the final question writer.  Its
output belongs in a restricted sidecar and is used only to audit generation.
"""

EVOLUTION_PLANNER_PROMPT = """
Create a hidden evolution plan from the authorized mode, public ledger, and
operator contract.  Return: fact ledger, any registered controlled hypothetical
observations, competing explanations, answer outline, conclusion contract,
control plan, and expected weak-model error.  Do not claim unprovided real
facts, rules, thresholds, or case conclusions.  This plan is restricted and
must not be passed to the question-surface writer.
""".strip()
