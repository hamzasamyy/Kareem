"""kareem.cost: approximate Claude token-usage/cost tracking (Section 2).

Verifies the actual persisted state and running session total, not just
that record() runs without raising — the point of this module is the
numbers it produces.
"""

import json
import unittest
from unittest.mock import patch

from kareem import cost


class CostRecordTests(unittest.TestCase):
    def setUp(self):
        # Isolate every test from the real data/claude_usage.json and from
        # each other's in-memory session totals.
        cost._session_input_tokens = 0
        cost._session_output_tokens = 0
        self._data = {}

        def fake_read():
            return dict(self._data)

        def fake_write(data):
            self._data = dict(data)

        self._read_patch = patch.object(cost, "_read_day_totals", side_effect=fake_read)
        self._write_patch = patch.object(cost, "_write_day_totals", side_effect=fake_write)
        self._read_patch.start()
        self._write_patch.start()
        self.addCleanup(self._read_patch.stop)
        self.addCleanup(self._write_patch.stop)
        self.addCleanup(setattr, cost, "_session_input_tokens", 0)
        self.addCleanup(setattr, cost, "_session_output_tokens", 0)

    def test_records_and_accumulates_session_totals(self):
        cost.record("claude-haiku-4-5", 1000, 500)
        self.assertEqual(cost._session_input_tokens, 1000)
        self.assertEqual(cost._session_output_tokens, 500)

        cost.record("claude-haiku-4-5", 2000, 1000)
        self.assertEqual(cost._session_input_tokens, 3000)
        self.assertEqual(cost._session_output_tokens, 1500)

    def test_persists_day_total_across_calls(self):
        cost.record("claude-haiku-4-5", 1000, 500)
        cost.record("claude-haiku-4-5", 2000, 1000)

        from datetime import date
        today = date.today().isoformat()
        self.assertIn(today, self._data)
        self.assertEqual(self._data[today]["input_tokens"], 3000)
        self.assertEqual(self._data[today]["output_tokens"], 1500)

    def test_cost_estimate_uses_haiku_pricing(self):
        # $1.00/$5.00 per million for Haiku 4.5 — 1M in + 1M out = $6.00 total.
        cost_usd = cost._estimate_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost_usd, 6.00, places=4)

    def test_unrecognized_model_falls_back_to_default_pricing(self):
        cost_usd = cost._estimate_cost_usd("some-future-model", 1_000_000, 1_000_000)
        self.assertAlmostEqual(cost_usd, 6.00, places=4)  # same as the Haiku default

    def test_never_raises_if_persistence_fails(self):
        with patch.object(cost, "_write_day_totals", side_effect=OSError("disk full")):
            try:
                cost.record("claude-haiku-4-5", 100, 50)
            except Exception as e:
                self.fail(f"record() must never raise, but raised: {e}")

    def test_logs_to_kareem_log_via_log_action(self):
        with patch("kareem.safety.log_action") as mock_log:
            cost.record("claude-haiku-4-5", 100, 50)
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args[0][0], "claude_usage")


if __name__ == "__main__":
    unittest.main()
