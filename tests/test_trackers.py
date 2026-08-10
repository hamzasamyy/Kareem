import unittest
from datetime import datetime, timedelta

from jarvis import trackers


class ParseDueTests(unittest.TestCase):
    def test_iso_passthrough(self):
        result = trackers.parse_due("2026-07-21T00:00:00+03:00")
        self.assertEqual(result, "2026-07-21T00:00:00+03:00")

    def test_in_n_units(self):
        before = datetime.now().astimezone()
        result = trackers.parse_due("in 3 days")
        parsed = datetime.fromisoformat(result)
        self.assertAlmostEqual(
            (parsed - before).total_seconds(), timedelta(days=3).total_seconds(), delta=5,
        )

    def test_today_tomorrow_at_time(self):
        result = trackers.parse_due("tomorrow at 9am")
        parsed = datetime.fromisoformat(result)
        self.assertEqual((parsed.hour, parsed.minute), (9, 0))

    def test_next_weekday_with_time_reproduces_reported_bug(self):
        # This is the exact phrase from the reported crash: Jarvis resolved
        # it correctly in conversation but the raw phrase sent to the tool
        # was rejected because parse_due() didn't understand weekday names.
        result = trackers.parse_due("next Thursday 12am")
        self.assertIsNotNone(result)
        parsed = datetime.fromisoformat(result)
        self.assertEqual(parsed.weekday(), 3)  # Thursday
        self.assertEqual((parsed.hour, parsed.minute), (0, 0))

    def test_next_thursday_with_space_before_am(self):
        result = trackers.parse_due("next Thursday 12 am")
        self.assertIsNotNone(result)
        parsed = datetime.fromisoformat(result)
        self.assertEqual(parsed.weekday(), 3)

    def test_next_weekday_skips_today_if_today_matches(self):
        now = datetime.now().astimezone()
        today_name = ["monday", "tuesday", "wednesday", "thursday",
                       "friday", "saturday", "sunday"][now.weekday()]
        result = trackers.parse_due(f"next {today_name}")
        parsed = datetime.fromisoformat(result)
        self.assertGreaterEqual((parsed.date() - now.date()).days, 1)

    def test_bare_weekday_can_resolve_to_today(self):
        now = datetime.now().astimezone()
        today_name = ["monday", "tuesday", "wednesday", "thursday",
                       "friday", "saturday", "sunday"][now.weekday()]
        result = trackers.parse_due(today_name)
        parsed = datetime.fromisoformat(result)
        self.assertEqual(parsed.date(), now.date())

    def test_dateparser_fallback_handles_month_day(self):
        result = trackers.parse_due("August 3rd")
        self.assertIsNotNone(result)
        parsed = datetime.fromisoformat(result)
        self.assertEqual((parsed.month, parsed.day), (8, 3))

    def test_genuinely_unparseable_returns_none(self):
        self.assertIsNone(trackers.parse_due("asdkjfh not a date at all !!"))

    def test_empty_returns_none(self):
        self.assertIsNone(trackers.parse_due(""))
        self.assertIsNone(trackers.parse_due(None))


class LocalAwareTests(unittest.TestCase):
    """_local_aware must interpret stored times DST-correctly (B-5).

    Forcing a DST transition isn't portable (time.tzset is Unix-only), so
    these lock in the correctness properties the fix guarantees: naive values
    are treated as local wall-clock time, and aware values keep their instant.
    """

    def test_naive_becomes_timezone_aware(self):
        result = trackers._local_aware("2026-07-21T12:00:00")
        self.assertIsNotNone(result.tzinfo)

    def test_naive_kept_as_local_wall_clock(self):
        result = trackers._local_aware("2026-01-15T09:30:00")
        self.assertEqual((result.hour, result.minute), (9, 30))

    def test_aware_instant_is_preserved(self):
        from datetime import timezone
        result = trackers._local_aware("2026-07-21T12:00:00+00:00")
        self.assertEqual(result, datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc))


if __name__ == "__main__":
    unittest.main()
