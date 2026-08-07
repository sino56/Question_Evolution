"""Prompt used to rebuild a scoring reference after a question changes."""

REFERENCE_REBUILD_PROMPT = """
Answer the final question using only the supplied public fact projection and
valid rules.  Cite the fact_id or rule_id supporting each material conclusion.
Do not use hidden planning fields, stale answers, stale rubrics, or old scores.
If the public material permits materially conflicting answers, report that the
answer is not uniquely recoverable instead of inventing a resolution.
""".strip()
