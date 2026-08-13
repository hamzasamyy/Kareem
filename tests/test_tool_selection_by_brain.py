"""F2: two-tier tool selection.

Keyword-gating the tool list kept causing under-inclusion failures (a real
request whose phrasing missed the keywords got its tool silently denied — web
search, then calendar, and the pattern persisted in desktop/files/browser).
select_tools_for_message now sends the FULL set to the capable hosted/Claude
brains (where the token saving is negligible and the bug lived), and keeps the
keyword filter only for the small local Ollama model that actually degrades
with many tools."""

import unittest

from jarvis import tool_routing


class ToolSelectionByBrainTests(unittest.TestCase):
    def setUp(self):
        self.specs = [{"name": name} for name in (
            "web_search", "calendar_add_event", "open_app",
            "run_command", "tracker_add", "delete_file",
        )]

    def _names(self, brain, text):
        return {s["name"] for s in tool_routing.select_tools_for_message(self.specs, text, brain)}

    def test_hosted_brain_always_gets_the_full_set(self):
        # "start Spotify" hits no desktop keyword, yet open_app must be offered.
        names = self._names("hosted", "start Spotify")
        self.assertEqual(names, {s["name"] for s in self.specs})
        self.assertIn("open_app", names)

    def test_claude_brain_always_gets_the_full_set(self):
        self.assertEqual(self._names("claude", "hello"), {s["name"] for s in self.specs})

    def test_ollama_brain_gets_the_keyword_filtered_subset(self):
        names = self._names("ollama", "hello")           # greeting -> core only
        self.assertIn("web_search", names)               # core, always on
        self.assertIn("calendar_add_event", names)       # core, always on
        self.assertNotIn("open_app", names)              # desktop, gated
        self.assertNotIn("run_command", names)

    def test_ollama_still_routes_a_matched_category(self):
        self.assertIn("delete_file", self._names("ollama", "delete that file"))


if __name__ == "__main__":
    unittest.main()
