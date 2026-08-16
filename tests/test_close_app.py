"""close_app: the fix for a real, verified bug where app_click reported a
successful click on Notepad's "Close" control while the process kept
running (Windows 11's tabbed Notepad has both a "Close Tab" and a window
"Close" control, and the click landed on/registered against the wrong or
non-functional one — see task report). close_app adds a graceful
close-by-PID request as the PRIMARY path plus real post-close
verification: it polls for the process to actually exit rather than
trusting the close request was sent, and never reports success on a guess.

Detection is TWO independent signals, unioned by PID — see
kareem/tools/apps.py's _find_processes_by_exe_name /
_find_processes_by_window_title docstrings:
  1. exe-name match (KNOWN_APPS's assumed executable, e.g. notepad.exe)
  2. visible-window-title match, resolved to its real owning process via
     pywinauto — this is the fix for a SECOND real, verified bug: Windows
     11's modern Calculator ('calc' resolves through an App Execution Alias
     to CalculatorApp.exe, not calc.exe) made exe-name-only matching find
     nothing and made Kareem falsely claim "already closed" while the real
     process kept running (see task report) — instead of hardcoding a third
     KNOWN_APPS name exception, detection now doesn't depend on knowing the
     exe name at all when a matching window is visible.

Live-verified (see task report, not reproduced in these deterministic
unit tests): closing a real Notepad instance succeeded and was confirmed
independently via a separate process check; Windows 11's modern Notepad
closed even with an unsaved edit rather than pausing on its own
save-changes prompt — the tool's description is deliberately honest about
this, not overclaiming protection against data loss. Also live-verified:
closing Windows 11's real Calculator (CalculatorApp.exe) via window-title
detection, confirmed via an independent psutil PID check that the process
was actually gone afterward."""

import unittest
from unittest.mock import MagicMock, patch

from kareem.tools import apps


def _fake_process(name="notepad.exe", pid=1234, running_forever=False):
    proc = MagicMock()
    proc.info = {"pid": pid, "name": name}
    proc.pid = pid
    if running_forever:
        proc.is_running.return_value = True
    else:
        proc.is_running.return_value = False
    return proc


def _fake_window(title, pid, visible=True):
    win = MagicMock()
    win.window_text.return_value = title
    win.is_visible.return_value = visible
    win.process_id.return_value = pid
    return win


class NoWindowMatches:
    """Context manager: patches pywinauto.Desktop so the window-title
    detection signal always finds nothing — isolates tests that only care
    about the exe-name path, matching this module's pre-refactor behavior."""

    def __enter__(self):
        self._patcher = patch("pywinauto.Desktop")
        mock_desktop_cls = self._patcher.start()
        mock_desktop_cls.return_value.windows.return_value = []
        return mock_desktop_cls

    def __exit__(self, *exc):
        self._patcher.stop()


class CloseAppNoMatchingProcessTests(unittest.TestCase):
    def test_no_running_process_reports_clearly_without_calling_taskkill(self):
        with patch("psutil.process_iter", return_value=[]), \
             NoWindowMatches(), \
             patch("subprocess.run") as mock_run:
            result = apps.close_app("notepad")
        self.assertIn("No running process found", result)
        self.assertIn("notepad.exe", result)
        mock_run.assert_not_called()


class CloseAppSuccessTests(unittest.TestCase):
    def test_process_that_exits_reports_success(self):
        proc = _fake_process(running_forever=False)
        with patch("psutil.process_iter", return_value=[proc]), \
             NoWindowMatches(), \
             patch("subprocess.run") as mock_run:
            result = apps.close_app("notepad")
        self.assertEqual(result, "Closed notepad.")
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        self.assertIn("taskkill", args)
        self.assertIn("/PID", args)
        self.assertIn(str(proc.pid), args)
        self.assertNotIn("/F", args, "must not force-kill by default")

    def test_matches_process_by_name_case_insensitively(self):
        proc = _fake_process(name="NOTEPAD.EXE", running_forever=False)
        with patch("psutil.process_iter", return_value=[proc]), \
             NoWindowMatches(), \
             patch("subprocess.run"):
            result = apps.close_app("notepad")
        self.assertEqual(result, "Closed notepad.")

    def test_known_app_alias_resolves_to_real_exe_name(self):
        # KNOWN_APPS maps 'calculator' -> 'calc'
        proc = _fake_process(name="calc.exe", running_forever=False)
        with patch("psutil.process_iter", return_value=[proc]), \
             NoWindowMatches(), \
             patch("subprocess.run") as mock_run:
            result = apps.close_app("calculator")
        self.assertEqual(result, "Closed calculator.")
        args = mock_run.call_args[0][0]
        self.assertIn(str(proc.pid), args)


class CloseAppWindowTitleDetectionTests(unittest.TestCase):
    """The actual fix: a process whose real executable name does NOT match
    KNOWN_APPS's assumption at all (exe-name search finds nothing), but
    which has a visible window whose title matches — exactly the real
    Calculator scenario (CalculatorApp.exe vs. the assumed calc.exe)."""

    def test_closes_by_window_title_when_exe_name_search_finds_nothing(self):
        pid = 9999
        win = _fake_window("Calculator", pid)
        proc = _fake_process(name="CalculatorApp.exe", pid=pid, running_forever=False)
        with patch("psutil.process_iter", return_value=[]), \
             patch("pywinauto.Desktop") as mock_desktop_cls, \
             patch("psutil.Process", return_value=proc), \
             patch("subprocess.run") as mock_run:
            mock_desktop_cls.return_value.windows.return_value = [win]
            result = apps.close_app("calculator")
        self.assertEqual(result, "Closed calculator.")
        args = mock_run.call_args[0][0]
        self.assertIn("/PID", args)
        self.assertIn(str(pid), args)

    def test_window_title_match_is_case_insensitive_substring(self):
        pid = 4242
        win = _fake_window("Untitled - Notepad", pid)
        proc = _fake_process(name="notepad.exe", pid=pid, running_forever=False)
        with patch("psutil.process_iter", return_value=[]), \
             patch("pywinauto.Desktop") as mock_desktop_cls, \
             patch("psutil.Process", return_value=proc), \
             patch("subprocess.run"):
            mock_desktop_cls.return_value.windows.return_value = [win]
            result = apps.close_app("NOTEPAD")
        self.assertEqual(result, "Closed NOTEPAD.")

    def test_ignores_windows_whose_title_does_not_match(self):
        win = _fake_window("Google Chrome", pid=1111)
        with patch("psutil.process_iter", return_value=[]), \
             patch("pywinauto.Desktop") as mock_desktop_cls, \
             patch("subprocess.run") as mock_run:
            mock_desktop_cls.return_value.windows.return_value = [win]
            result = apps.close_app("notepad")
        self.assertIn("No running process found", result)
        mock_run.assert_not_called()

    def test_ignores_invisible_windows(self):
        win = _fake_window("Notepad", pid=5555, visible=False)
        with patch("psutil.process_iter", return_value=[]), \
             patch("pywinauto.Desktop") as mock_desktop_cls, \
             patch("subprocess.run") as mock_run:
            mock_desktop_cls.return_value.windows.return_value = [win]
            result = apps.close_app("notepad")
        self.assertIn("No running process found", result)
        mock_run.assert_not_called()

    def test_same_pid_found_by_both_signals_is_not_double_killed(self):
        pid = 7777
        proc = _fake_process(name="notepad.exe", pid=pid, running_forever=False)
        win = _fake_window("Untitled - Notepad", pid)
        with patch("psutil.process_iter", return_value=[proc]), \
             patch("pywinauto.Desktop") as mock_desktop_cls, \
             patch("psutil.Process", return_value=proc), \
             patch("subprocess.run") as mock_run:
            mock_desktop_cls.return_value.windows.return_value = [win]
            result = apps.close_app("notepad")
        self.assertEqual(result, "Closed notepad.")
        mock_run.assert_called_once()  # deduped by PID, not one taskkill per signal

    def test_pywinauto_unavailable_degrades_to_exe_name_only(self):
        # UIA/pywinauto can genuinely fail to initialize on some machines —
        # the window-title signal must degrade to "found nothing" rather
        # than raising and losing the exe-name path entirely.
        proc = _fake_process(name="notepad.exe", running_forever=False)
        with patch("psutil.process_iter", return_value=[proc]), \
             patch("pywinauto.Desktop", side_effect=RuntimeError("no display")), \
             patch("subprocess.run") as mock_run:
            result = apps.close_app("notepad")
        self.assertEqual(result, "Closed notepad.")
        mock_run.assert_called_once()

    def test_a_window_that_vanishes_mid_scan_is_skipped_not_fatal(self):
        # process_id()/psutil.Process() raising for one stale window handle
        # must not abort the whole detection pass.
        win = _fake_window("Notepad", pid=8888)
        win.process_id.side_effect = Exception("window closed")
        proc = _fake_process(name="notepad.exe", pid=2222, running_forever=False)
        with patch("psutil.process_iter", return_value=[proc]), \
             patch("pywinauto.Desktop") as mock_desktop_cls, \
             patch("subprocess.run") as mock_run:
            mock_desktop_cls.return_value.windows.return_value = [win]
            result = apps.close_app("notepad")
        self.assertEqual(result, "Closed notepad.")  # exe-name match still succeeds


class CloseAppFailureTests(unittest.TestCase):
    def test_process_that_never_exits_reports_honest_failure_not_success(self):
        proc = _fake_process(running_forever=True)
        # Fake the clock so the 6s poll deadline is reached on the second
        # check instead of actually waiting ~6 real seconds.
        with patch("psutil.process_iter", return_value=[proc]), \
             NoWindowMatches(), \
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
             NoWindowMatches(), \
             patch("subprocess.run", side_effect=OSError("taskkill not found")):
            result = apps.close_app("notepad")
        self.assertIn("Couldn't send a close request", result)


if __name__ == "__main__":
    unittest.main()
