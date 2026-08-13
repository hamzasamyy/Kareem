"""F3: the GUC parser's archive-noise filter.

_looks_like_noise() used to drop any line containing ANY past-year token, so a
real current/future deadline that also mentioned a past year in passing ("…due
5 January <this year>, based on 2019 material") was silently discarded — not
even surfaced as an announcement. It now treats the year signal as noise only
when EVERY explicit year in the line is in the past."""

import unittest
from datetime import datetime

from jarvis.guc import parser


class ArchiveNoiseFilterTests(unittest.TestCase):
    def test_real_deadline_mentioning_a_past_year_is_kept(self):
        future_year = datetime.now().year + 1
        line = f"Assignment 3 due 5 January {future_year}, based on 2019 material"
        self.assertFalse(parser._looks_like_noise(line))
        result = parser.find_candidates_in_text(line, "CSEN401", "cms_announcement")
        self.assertEqual(len(result["candidates"]), 1)   # captured, not dropped
        self.assertEqual(len(result["announcements"]), 0)

    def test_pure_archive_listing_is_still_noise(self):
        self.assertTrue(parser._looks_like_noise("Midterm Spring 2011"))

    def test_all_past_years_is_noise(self):
        self.assertTrue(parser._looks_like_noise("final exam 2014 and 2015"))

    def test_noise_markers_win_regardless_of_year(self):
        this_year = datetime.now().year
        self.assertTrue(parser._looks_like_noise(f"assignment 1 solution {this_year}"))

    def test_line_with_no_year_is_not_year_noise(self):
        self.assertFalse(parser._looks_like_noise("quiz next Tuesday"))


if __name__ == "__main__":
    unittest.main()
