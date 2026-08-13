import unittest
from datetime import datetime, time, timedelta
from unittest.mock import MagicMock, patch

from kareem.tools import calendar


class RangeBoundsTests(unittest.TestCase):
    """calendar_list_events' _range_bounds (bug report #4): extended to
    accept flexible ranges beyond the original fixed set of today/tomorrow/
    this week/next 7 days, reusing trackers.parse_due() — via calendar.py's
    own _parse_datetime helper, the SAME parser tracker_add and
    calendar_add_event already use — for anything not one of the bespoke
    labels below."""

    def test_today_unchanged(self):
        now = datetime.now().astimezone()
        today = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        label, start, end = calendar._range_bounds("today")
        self.assertEqual(label, "today")
        self.assertEqual(start, today)
        self.assertEqual(end, today + timedelta(days=1))

    def test_yesterday_reproduces_exact_repro_phrase(self):
        # Reproduces the exact console failure: "whats on my calendar
        # yesterday" -> calendar_list_events(range='yesterday') -> "Use
        # 'today', 'tomorrow', 'this week', or 'next 7 days'." for a
        # completely reasonable request.
        now = datetime.now().astimezone()
        today = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        label, start, end = calendar._range_bounds("yesterday")
        self.assertEqual(label, "yesterday")
        self.assertEqual(start, today - timedelta(days=1))
        self.assertEqual(end, today)

    def test_tomorrow_unchanged(self):
        now = datetime.now().astimezone()
        today = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        label, start, end = calendar._range_bounds("tomorrow")
        self.assertEqual(start, today + timedelta(days=1))
        self.assertEqual(end, today + timedelta(days=2))

    def test_this_week_unchanged(self):
        now = datetime.now().astimezone()
        today = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        expected_start = today - timedelta(days=today.weekday())
        label, start, end = calendar._range_bounds("this week")
        self.assertEqual(start, expected_start)
        self.assertEqual(end, expected_start + timedelta(days=7))

    def test_last_week_is_the_7_days_before_this_week(self):
        now = datetime.now().astimezone()
        today = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        this_week_start = today - timedelta(days=today.weekday())
        label, start, end = calendar._range_bounds("last week")
        self.assertEqual(label, "last week")
        self.assertEqual(end, this_week_start)
        self.assertEqual(start, this_week_start - timedelta(days=7))

    def test_this_weekend_covers_saturday_and_sunday_of_current_week(self):
        now = datetime.now().astimezone()
        today = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
        week_start = today - timedelta(days=today.weekday())
        expected_saturday = week_start + timedelta(days=5)
        label, start, end = calendar._range_bounds("this weekend")
        self.assertEqual(label, "this weekend")
        self.assertEqual(start, expected_saturday)
        self.assertEqual(end, expected_saturday + timedelta(days=2))
        self.assertEqual(start.weekday(), 5)  # Saturday

    def test_next_7_days_unchanged(self):
        label, start, end = calendar._range_bounds("next 7 days")
        self.assertEqual(label, "next 7 days")
        self.assertAlmostEqual((end - start).total_seconds(),
                                timedelta(days=7).total_seconds(), delta=5)

    def test_specific_iso_date_becomes_a_one_day_range(self):
        label, start, end = calendar._range_bounds("2026-07-25")
        self.assertEqual((start.year, start.month, start.day), (2026, 7, 25))
        self.assertEqual((start.hour, start.minute), (0, 0))
        self.assertEqual(end, start + timedelta(days=1))

    def test_specific_natural_language_date_becomes_a_one_day_range(self):
        # Reuses the SAME parser tracker_add uses (trackers.parse_due) —
        # this is bug report #4's explicit reuse requirement.
        label, start, end = calendar._range_bounds("next Thursday")
        self.assertEqual(start.weekday(), 3)  # Thursday
        self.assertEqual(end, start + timedelta(days=1))
        self.assertEqual((start.hour, start.minute), (0, 0))

    def test_case_and_whitespace_insensitive(self):
        label, _, _ = calendar._range_bounds("  YESTERDAY  ")
        self.assertEqual(label, "yesterday")

    def test_none_defaults_to_today(self):
        label, _, _ = calendar._range_bounds(None)
        self.assertEqual(label, "today")

    def test_genuinely_unparseable_still_raises_with_updated_message(self):
        with self.assertRaises(ValueError) as ctx:
            calendar._range_bounds("asdkjfh not a date at all !!")
        message = str(ctx.exception)
        self.assertIn("yesterday", message)
        self.assertIn("last week", message)
        self.assertIn("this weekend", message)


class CalendarListEventsToolTests(unittest.TestCase):
    """calendar_list_events itself (not just _range_bounds), Google API
    mocked out — confirms the exact repro ("whats on my calendar yesterday")
    no longer hits the old rejection message at the tool-call level."""

    @patch("kareem.tools.calendar.get_calendar_service")
    def test_yesterday_no_longer_rejected_by_the_tool(self, mock_get_service):
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {"items": []}
        mock_get_service.return_value = mock_service

        result = calendar.calendar_list_events(range="yesterday")

        self.assertNotIn("Use 'today'", result)
        self.assertIn("yesterday", result)

    @patch("kareem.tools.calendar.get_calendar_service")
    def test_last_week_no_longer_rejected_by_the_tool(self, mock_get_service):
        mock_service = MagicMock()
        mock_service.events.return_value.list.return_value.execute.return_value = {"items": []}
        mock_get_service.return_value = mock_service

        result = calendar.calendar_list_events(range="last week")

        self.assertNotIn("Use 'today'", result)
        self.assertIn("last week", result)


if __name__ == "__main__":
    unittest.main()
