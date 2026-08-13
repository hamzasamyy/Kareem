import unittest

from kareem import tools


class WebSearchQueryGuidanceWordingTests(unittest.TestCase):
    """Cross-turn search bug report, fix #2: cheap regression guard on the
    web_search TOOL DESCRIPTION wording only — confirms the query-quality
    guidance text is present, not that a model actually follows it (a
    probabilistic, model-side question; see this fix's live-verification
    notes in the task report for the actual behavioral check)."""

    @classmethod
    def setUpClass(cls):
        tools.load_tools()
        cls.spec = next(s for s in tools.TOOL_SPECS if s["name"] == "web_search")

    def test_prefers_users_exact_wording_over_translation(self):
        description = self.spec["description"]
        self.assertIn("prefer the exact name", description)
        self.assertIn("rather than translating or reformulating", description)

    def test_warns_against_cascading_query_mutation_with_a_concrete_budget(self):
        # A vague "don't cascade, try one clean alternative" phrasing was
        # live-tested against the real repro and did NOT stop an 8-round
        # cascade (each individual rephrasing looked locally reasonable to
        # the model in isolation, so it kept going) — replaced with an
        # explicit numeric budget instead. This test only pins the wording;
        # see the task report for what live re-testing of the numeric
        # version actually showed.
        description = self.spec["description"]
        self.assertIn("Don't cascade", description)
        self.assertIn("at most 2", description)
        self.assertIn("TOTAL", description)

    def test_original_core_description_still_present(self):
        # The guidance addition must not have replaced the tool's basic
        # purpose statement.
        description = self.spec["description"]
        self.assertIn("Search the web and return the top results", description)
        self.assertIn("fetch_page", description)


if __name__ == "__main__":
    unittest.main()
