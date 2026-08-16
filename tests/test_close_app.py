"""close_app: the fix for a real, verified bug where app_click reported a
successful click on Notepad's "Close" control while the process kept
running (Windows 11's tabbed Notepad has both a "Close Tab" and a window
"Close" control, and the click landed on/registered against the wrong or
non-functional one — see task report). close_app adds a graceful
taskkill-based close request as the PRIMARY path plus real post-close
verification: it polls for the process to actually exit rather than
trusting the close request was sent, and never reports success on a guess.

Live-verified (see task report, not reproduced in these deterministic
unit tests): closing a real Notepad instance succeeded and was confirmed
independently via a separate process check; Windows 11's modern Notepad
closed even with an unsaved edit rather than pausing on its own
save-changes prompt — the tool's description is deliberately honest about
this, not overclaiming protection against data loss."""

import unittest
from unittest.mock import MagicMock, patch

from kareem.tools import apps


def _fake_process(name="notepad.exe", pid=1234, running_forever=False):
    proc = MagicMock()
    proc.info = {"pid": pid, "name": name}
    if running_forever:
        proc.is_running.return_value = True
    else:
        proc.is_running.return_value = False
    return proc


class CloseAppNoMatchingProcessTests(unittest.TestCase):
    def test_no_running_process_reports_clearly_without_calling_taskkill(self):
        with patch("psutil.process_iter", return_value=[]), \
             patch("subprocess.run") as mock_run:
            result = apps.close_app("notepad")
        self.assertIn("No running process found", result)
        self.assertIn("notepad.exe", result)
        mock_run.assert_not_called()


class CloseAppSuccessTests(unittest.TestCase):
    def test_process_that_exits_reports_success(self):
        proc = _fake_process(running_forever=False)
        with patch("psutil.process_iter", return_value=[proc]), \
             patch("subprocess.run") as mock_run:
            result = apps.close_app("notepad")
        self.assertEqual(result, "Closed notepad.")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("taskkill", args)
        self.assertIn("notepad.exe", args)
        self.assertNotIn("/F", args, "must not force-kill by default")

    def test_matches_process_by_name_case_insensitively(self):
        proc = _fake_process(name="NOTEPAD.EXE", running_forever=False)
        with patch("psutil.process_iter", return_value=[proc]), \
             patch("subprocess.run"):
            result = apps.close_app("notepad")
        self.assertEqual(result, "Closed notepad.")

    def test_known_app_alias_resolves_to_real_exe_name(self):
        # KNOWN_APPS maps 'calculator' -> 'calc'
        proc = _fake_process(name="calc.exe", running_forever=False)
        with patch("psutil.process_iter", return_value=[proc]), \
             patch("subprocess.run") as mock_run:
            result = apps.close_app("calculator")
        self.assertEqual(result, "Closed calculator.")
        args = mock_run.call_args[0][0]
        self.assertIn("calc.exe", args)


class CloseAppFailureTests(unittest.TestCase):
    def test_process_that_never_exits_reports_honest_failure_not_success(self):
        proc = _fake_process(running_forever=True)
        # Fake the clock so the 6s poll deadline is reached on the second
        # check instead of actually waiting ~6 real seconds.
        with patch("psutil.process_iter", return_value=[proc]), \
             patch("subprocess.run") as mock_run, \
             patch("time.sleep"), \
             patch("time.time", side_effect=[0, 0, 100]):
            result = apps.close_app("notepad")
        self.assertNotIn("Closed", result)
        self.assertIn("didn't actually close", result)
        self.assertIn("Not reporting success", result)
        mock_run.assert_called_once()

    def test_taskkill_invocation_failure_is_reported_not_swallowed(self):
        proc = _fake_process(running_forever=False)
        with patch("psutil.process_iter", return_value=[proc]), \
             patch("subprocess.run", side_effect=OSError("taskkill not found")):
            result = apps.close_app("notepad")
        self.assertIn("Couldn't send a close request", result)


if __name__ == "__main__":
    unittest.main()
