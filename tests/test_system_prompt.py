import unittest

from kareem.agent import SYSTEM_PROMPT, SYSTEM_PROMPT_SMALL


class SystemPromptWordingTests(unittest.TestCase):
    """Cross-turn search bug fix #1: these are cheap regression
    guards on PROMPT WORDING only — they confirm certain phrases are
    present, not that a model actually behaves a certain way (that's a
    probabilistic, model-side question that can only be verified by
    actually driving a live model through a real conversation, not this
    file). Their only job is to catch a future edit that accidentally
    deletes one of these two instructions while touching the other."""

    def test_time_sensitivity_instruction_still_present(self):
        # The cross-turn exception below must not have replaced/weakened
        # this — it was added to fix an earlier, unrelated bug (a real
        # query got no search at all and Kareem hallucinated a stale-
        # training-data answer instead).
        self.assertIn("default to calling", SYSTEM_PROMPT)
        self.assertIn("web_search", SYSTEM_PROMPT)
        self.assertIn("time-sensitive", SYSTEM_PROMPT)

    def test_cross_turn_clarification_exception_present(self):
        self.assertIn("EXCEPTION", SYSTEM_PROMPT)
        self.assertIn("already answered earlier in THIS", SYSTEM_PROMPT)
        self.assertIn("actually about the wrong thing", SYSTEM_PROMPT)

    def test_exception_is_narrow_not_a_blanket_no_search_rule(self):
        # Must still call for a fresh search on a genuinely new follow-up —
        # a carve-out broad enough to suppress those would be a new bug
        # (under-searching), not a fix.
        self.assertIn("genuinely new or different", SYSTEM_PROMPT)

    def test_small_prompt_variant_has_short_exception_too(self):
        # The local-fallback brain gets handed the SAME tools= list as the
        # hosted brain (see kareem/brain.py's _fallback_to_ollama), so it
        # can hit this same failure mode and needs at least a short version.
        self.assertIn("web_search", SYSTEM_PROMPT_SMALL)
        self.assertIn("don't re-search", SYSTEM_PROMPT_SMALL)

    def test_small_prompt_variant_stays_short(self):
        # Documented failure mode (see the comment above SYSTEM_PROMPT_SMALL
        # in kareem/agent.py): qwen2.5:3b answers silently empty when the
        # system prompt gets long. Guardrail against that regressing here;
        # current length is 431 chars, this leaves real headroom without
        # allowing unbounded growth.
        self.assertLess(len(SYSTEM_PROMPT_SMALL), 600)


if __name__ == "__main__":
    unittest.main()
