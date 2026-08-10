import unittest

from jarvis import tool_routing, tools
from jarvis.agent import _CONFIRMATION_CAPABLE_TOOLS


class CalendarToolNamingTests(unittest.TestCase):
    """Bug report #2: the hosted-brain fallback path attempted
    'calendar_add_event' while the local Ollama fallback attempted
    'calendar_create_event' for the same action — two different names for
    what must be exactly one registered tool.

    Audit: grepped the whole repo plus every retained session log for the
    literal string 'calendar_create_event' — zero matches anywhere. There is
    no hardcoded wrong name in this codebase; jarvis/guc/sync.py's
    `_create_calendar_event` is a private, non-@register'd, non-LLM-facing
    helper (an internal caller of the Calendar API, not a tool a model can
    ever see or call — see that module's own docstring), so its
    similar-sounding name is coincidental, not a leak. The real cause was
    bug #1: calendar tools were excluded from tools= by routing, so a model
    calling the "obvious" calendar-add tool had nothing real to reference
    and guessed a plausible-sounding name blind. Fixing #1 (calendar tools
    now always-on in jarvis/tool_routing.py's "core" category) means every
    brain — hosted AND the Ollama fallback, which is handed the exact same
    tools= list — always sees the one real name now.

    These tests pin the actual canonical names so any future drift between
    jarvis/tools/calendar.py's @register'd names and the places that
    reference them by string (tool_routing.py, agent.py) fails loudly
    instead of silently reintroducing this bug.
    """

    CANONICAL_CALENDAR_TOOLS = {
        "calendar_add_event", "calendar_list_events",
        "calendar_find_event", "calendar_delete_event",
    }

    @classmethod
    def setUpClass(cls):
        tools.load_tools()

    def test_registered_calendar_tool_names_match_expected_canonical_set(self):
        registered = {name for name in tools.TOOL_FUNCTIONS if name.startswith("calendar_")}
        self.assertEqual(registered, self.CANONICAL_CALENDAR_TOOLS)

    def test_tool_routing_references_only_real_registered_calendar_names(self):
        routed_calendar_names = {
            name for names in tool_routing.CATEGORIES.values() for name in names
            if name.startswith("calendar_")
        }
        self.assertEqual(routed_calendar_names, self.CANONICAL_CALENDAR_TOOLS)
        for name in routed_calendar_names:
            self.assertIn(name, tools.TOOL_FUNCTIONS)

    def test_confirmation_capable_calendar_entry_is_a_real_registered_name(self):
        calendar_confirmation_names = {
            name for name in _CONFIRMATION_CAPABLE_TOOLS if name.startswith("calendar_")
        }
        self.assertEqual(calendar_confirmation_names, {"calendar_delete_event"})
        for name in calendar_confirmation_names:
            self.assertIn(name, tools.TOOL_FUNCTIONS)

    def test_no_stray_calendar_create_event_name_anywhere_in_registry(self):
        # The exact wrong name from the bug report must never actually exist.
        self.assertNotIn("calendar_create_event", tools.TOOL_FUNCTIONS)
        self.assertNotIn("calendar_create_event", _CONFIRMATION_CAPABLE_TOOLS)
        all_routed_names = {name for names in tool_routing.CATEGORIES.values() for name in names}
        self.assertNotIn("calendar_create_event", all_routed_names)


if __name__ == "__main__":
    unittest.main()
