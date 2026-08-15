import unittest

from kareem.agent import Agent, MAX_TOOL_RESULT_CHARS


class ToolResultHardCapTests(unittest.TestCase):
    """Reproduced bug: "who won the last F1 race?" ran one correct web_search
    -> fetch_page round on a Wikipedia page. fetch_page's own 4000-char
    truncation wasn't tight enough on top of the tool-schema + history
    overhead already in the request — a single Groq call hit 8750 tokens
    against its 8000 tokens/minute limit and was rejected outright (not a
    multi-search cascade — that's the separate, still-intact fix pinned by
    test_web_search_tool.py). _execute_tool now hard-caps EVERY tool's
    result before it's added to context, regardless of source, as a
    backstop below any tool-specific truncation."""

    def _make_agent(self):
        return Agent.__new__(Agent)

    def test_oversized_result_from_any_tool_is_capped(self):
        agent = self._make_agent()
        huge = "x" * 50_000
        agent._notify_tool = lambda *a, **k: None
        from kareem import tools as tools_module
        from kareem import session_log

        tools_module.TOOL_FUNCTIONS["_fake_huge_tool"] = lambda: huge
        try:
            session_log.log_event = lambda *a, **k: None
            result = agent._execute_tool("_fake_huge_tool", {})
        finally:
            del tools_module.TOOL_FUNCTIONS["_fake_huge_tool"]

        self.assertLessEqual(len(result), MAX_TOOL_RESULT_CHARS + len(" …[truncated]"))
        self.assertTrue(result.endswith("…[truncated]"))

    def test_small_result_is_untouched(self):
        agent = self._make_agent()
        self.assertEqual(agent._cap_tool_result("a short result"), "a short result")

    def test_cap_is_exactly_at_the_documented_constant(self):
        agent = self._make_agent()
        exact = "y" * MAX_TOOL_RESULT_CHARS
        self.assertEqual(agent._cap_tool_result(exact), exact)
        over = exact + "z"
        capped = agent._cap_tool_result(over)
        self.assertEqual(capped, exact + " …[truncated]")

    def test_wikipedia_sized_page_fetch_result_fits_under_the_cap(self):
        # The exact shape of the repro: fetch_page's own 4000-char page text.
        wikipedia_like = ("The 2026 result was announced today. " * 200)[:4000]
        agent = self._make_agent()
        capped = agent._cap_tool_result(wikipedia_like)
        self.assertLessEqual(len(capped), MAX_TOOL_RESULT_CHARS + len(" …[truncated]"))


if __name__ == "__main__":
    unittest.main()
