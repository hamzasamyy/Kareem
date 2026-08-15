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


class SearchLatencyFixWordingTests(unittest.TestCase):
    """Search-latency bug report: measured that the real 30s+ cost was the
    model escalating past the snippet — one web_search then straight to
    browser_open/browser_read (a real, visible browser window) instead of
    answering from the snippet or using the cheap, capped fetch_page — never
    once calling fetch_page across 4 live repro runs. Cheap regression guard
    on the resulting tool-description wording only (see this fix's live
    before/after numbers in the task report for the actual behavioral
    check)."""

    @classmethod
    def setUpClass(cls):
        tools.load_tools()
        cls.web_search_spec = next(s for s in tools.TOOL_SPECS if s["name"] == "web_search")
        cls.browser_open_spec = next(s for s in tools.TOOL_SPECS if s["name"] == "browser_open")

    def test_web_search_prefers_answering_from_snippets_first(self):
        description = self.web_search_spec["description"]
        self.assertIn("Answer from the results you already have", description)

    def test_web_search_tells_the_model_not_to_escalate_to_the_browser(self):
        description = self.web_search_spec["description"]
        self.assertIn("Never escalate to browser_open/browser_read", description)

    def test_browser_open_steers_plain_lookups_to_fetch_page_instead(self):
        description = self.browser_open_spec["description"]
        self.assertIn("use web_search + fetch_page for that instead", description)


if __name__ == "__main__":
    unittest.main()
