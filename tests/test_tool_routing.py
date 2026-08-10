import unittest

from jarvis import tool_routing


class ToolRoutingTests(unittest.TestCase):
    def test_simple_greeting_gets_only_core(self):
        categories = tool_routing.categories_for("hello")
        self.assertEqual(categories, {"core"})

    def test_due_this_week_routes_to_university_and_core(self):
        categories = tool_routing.categories_for("what's due this week")
        self.assertEqual(categories, {"core", "university"})

    def test_unmatched_message_falls_back_to_core_only(self):
        categories = tool_routing.categories_for("xyzzy plugh qux")
        self.assertEqual(categories, {"core"})

    def test_filter_tool_specs_trims_to_relevant_categories(self):
        specs = [
            {"name": "navigate_app"},
            {"name": "tracker_add"},
            {"name": "browser_open"},
            {"name": "delete_file"},
        ]
        filtered = tool_routing.filter_tool_specs(specs, "add a reminder for my assignment")
        names = {s["name"] for s in filtered}
        self.assertIn("navigate_app", names)   # core, always on
        self.assertIn("tracker_add", names)    # university, matched
        self.assertNotIn("browser_open", names)
        self.assertNotIn("delete_file", names)

    def test_unknown_tool_name_is_always_kept(self):
        specs = [{"name": "navigate_app"}, {"name": "some_future_tool"}]
        filtered = tool_routing.filter_tool_specs(specs, "hello")
        names = {s["name"] for s in filtered}
        self.assertIn("some_future_tool", names)

    def test_web_search_always_included_even_without_search_keywords(self):
        # Reproduces the reported hallucination bug: this exact phrasing
        # ("the world cup finished now, who won and what was the score")
        # hits none of the old "web" category keywords (search/google/
        # news/weather/...), so web_search was silently excluded and Jarvis
        # answered from stale training data instead of searching.
        specs = [{"name": "navigate_app"}, {"name": "web_search"}, {"name": "fetch_page"}]
        filtered = tool_routing.filter_tool_specs(
            specs, "the world cup finished now, who won and what was the score"
        )
        names = {s["name"] for s in filtered}
        self.assertIn("web_search", names)
        self.assertIn("fetch_page", names)

    def test_web_search_included_on_plain_greeting_too(self):
        specs = [{"name": "navigate_app"}, {"name": "web_search"}]
        filtered = tool_routing.filter_tool_specs(specs, "hello")
        names = {s["name"] for s in filtered}
        self.assertIn("web_search", names)

    def test_unrelated_categories_still_excluded_for_web_queries(self):
        # Ground rule: fixing web search shouldn't reopen the token-bloat
        # problem for genuinely unrelated categories.
        specs = [
            {"name": "web_search"},
            {"name": "browser_open"},
            {"name": "delete_file"},
            {"name": "app_click"},
        ]
        filtered = tool_routing.filter_tool_specs(specs, "what's 2 plus 2")
        names = {s["name"] for s in filtered}
        self.assertIn("web_search", names)
        self.assertNotIn("browser_open", names)
        self.assertNotIn("delete_file", names)
        self.assertNotIn("app_click", names)

    # --- word-boundary matching (B-1): keywords match whole words only ---
    def test_app_keyword_does_not_fire_inside_other_words(self):
        # "app" must not match "happy"/"happened"/"apparently" and drag in the
        # desktop category (which includes run_command/run_python/shutdown).
        self.assertEqual(tool_routing.categories_for("that makes me happy"), {"core"})
        self.assertEqual(tool_routing.categories_for("what happened yesterday"), {"core"})
        self.assertEqual(tool_routing.categories_for("apparently it rained"), {"core"})

    def test_app_keyword_still_fires_as_a_whole_word(self):
        self.assertIn("desktop", tool_routing.categories_for("open the maps app"))

    def test_due_keyword_does_not_fire_inside_other_words(self):
        self.assertEqual(tool_routing.categories_for("the duel begins at noon"), {"core"})
        self.assertEqual(tool_routing.categories_for("I felt subdued"), {"core"})

    def test_grade_keyword_does_not_fire_on_upgrade(self):
        self.assertEqual(tool_routing.categories_for("should I upgrade my laptop"), {"core"})

    def test_event_keyword_does_not_fire_on_eventually(self):
        self.assertEqual(tool_routing.categories_for("eventually I will sleep"), {"core"})

    def test_real_keywords_still_route_after_boundary_fix(self):
        self.assertEqual(
            tool_routing.categories_for("what's due this week"), {"core", "university"}
        )
        self.assertIn("files", tool_routing.categories_for("delete that file"))

    # --- calendar tools always included (bug report #1) ---
    # Reproduces the reported routing-exclusion bug: "put an appointment
    # tomorrow at 5pm" (and the fuller "...to travel to sahel" variant from
    # the actual console reproduction) hit no "university" keyword at all —
    # "appointment" isn't one, and neither is a bare date/time phrase — so
    # calendar_add_event was silently excluded from tools=, the hosted model
    # tried to call it anyway, and the provider rejected the call with
    # "Tool call validation failed: ... not in request.tools". Calendar
    # tools are now in the always-on "core" category (same fix as
    # web_search above), so they can no longer be excluded this way.
    def test_calendar_tools_always_included_for_appointment_phrasing_without_keywords(self):
        specs = [
            {"name": "navigate_app"},
            {"name": "calendar_add_event"},
            {"name": "calendar_list_events"},
        ]
        filtered = tool_routing.filter_tool_specs(specs, "put an appointment tomorrow at 5pm")
        names = {s["name"] for s in filtered}
        self.assertIn("calendar_add_event", names)
        self.assertIn("calendar_list_events", names)

    def test_calendar_tools_always_included_exact_repro_phrase_with_destination(self):
        specs = [{"name": "calendar_add_event"}]
        filtered = tool_routing.filter_tool_specs(
            specs, "put an appointment tomorrow at 5pm to travel to sahel"
        )
        names = {s["name"] for s in filtered}
        self.assertIn("calendar_add_event", names)

    def test_calendar_tools_included_for_explicit_calendar_wording_too(self):
        # "what's on my calendar yesterday" already matched the "university"
        # keyword "calendar" before this fix (its actual bug is #4: the
        # rigid calendar_list_events range, not routing) — confirm it still
        # works now that calendar tools live in "core" instead.
        specs = [{"name": "calendar_list_events"}]
        filtered = tool_routing.filter_tool_specs(specs, "what's on my calendar yesterday")
        names = {s["name"] for s in filtered}
        self.assertIn("calendar_list_events", names)

    def test_calendar_tools_included_even_on_plain_greeting(self):
        # Always-on means always-on, same as web_search's equivalent test.
        specs = [{"name": "calendar_add_event"}]
        filtered = tool_routing.filter_tool_specs(specs, "hello")
        names = {s["name"] for s in filtered}
        self.assertIn("calendar_add_event", names)

    def test_university_no_longer_lists_calendar_tools_directly(self):
        # Calendar tools moved to "core" (always-on); "university" should
        # only gate GUC/tracker tools now, not duplicate them.
        self.assertNotIn("calendar_add_event", tool_routing.CATEGORIES["university"])
        self.assertIn("calendar_add_event", tool_routing.CATEGORIES["core"])


if __name__ == "__main__":
    unittest.main()
