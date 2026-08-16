"""vision_click's full-screen screenshot can capture ANY window currently
open, not just the one being clicked in — including something unrelated
like an .env file left open in an editor, or a credential manager, sitting
in the background while Kareem tries to click something else entirely.
That screenshot then gets uploaded to an external vision API. This guard
checks open window TITLES for sensitive-looking keywords before capturing
anything, and asks for explicit confirmation first if it finds one —
live-verified against a real open ".env.example - Notepad" window and a
real "API Keys - GroqCloud" browser tab (see task report)."""

import unittest
from unittest.mock import patch

from kareem.tools import vision


class SensitiveWindowsOpenTests(unittest.TestCase):
    def _titles(self, titles):
        return patch("pygetwindow.getAllTitles", return_value=titles)

    def test_detects_env_file_title(self):
        with self._titles([".env - Notepad", "Kareem"]):
            self.assertEqual(vision._sensitive_windows_open(), [".env - Notepad"])

    def test_detects_credential_manager_and_api_key_tab(self):
        titles = ["1Password", "API Keys - GroqCloud - Google Chrome", "Kareem"]
        with self._titles(titles):
            hits = vision._sensitive_windows_open()
        self.assertIn("1Password", hits)
        self.assertIn("API Keys - GroqCloud - Google Chrome", hits)

    def test_ordinary_windows_are_not_flagged(self):
        with self._titles(["File Explorer", "Kareem", "Calculator"]):
            self.assertEqual(vision._sensitive_windows_open(), [])

    def test_blank_titles_are_ignored(self):
        with self._titles(["", "   ", "Kareem"]):
            self.assertEqual(vision._sensitive_windows_open(), [])

    def test_never_raises_if_pygetwindow_is_unavailable(self):
        with patch("pygetwindow.getAllTitles", side_effect=Exception("no display")):
            self.assertEqual(vision._sensitive_windows_open(), [])


class VisionClickSensitiveWindowGuardTests(unittest.TestCase):
    def _run(self, titles, ask_response, screenshot_should_run):
        asked = []

        def ask_fn(prompt):
            asked.append(prompt)
            return ask_response

        with patch("pygetwindow.getAllTitles", return_value=titles), \
             patch("kareem.session_log.log_event"), \
             patch.object(vision, "log_action"), \
             patch.object(vision, "_capture_screenshot_b64") as mock_capture:
            mock_capture.return_value = ("fakeb64", (100, 100))
            with patch.object(vision, "_ask_vision_model", return_value={"found": False, "reasoning": "n/a"}):
                from kareem import safety
                prev = safety.set_ask_fn(ask_fn)
                try:
                    result = vision.vision_click("something")
                finally:
                    safety.set_ask_fn(prev)

            self.assertEqual(mock_capture.called, screenshot_should_run)
        return result, asked

    def test_sensitive_window_open_and_declined_never_screenshots(self):
        result, asked = self._run([".env - Notepad"], ask_response="no", screenshot_should_run=False)
        self.assertIn("Refused", result)
        self.assertIn(".env - Notepad", result)
        self.assertTrue(asked, "must actually ask for confirmation")

    def test_sensitive_window_open_and_approved_proceeds(self):
        result, asked = self._run([".env - Notepad"], ask_response="yes", screenshot_should_run=True)
        self.assertTrue(asked, "must ask before proceeding")
        self.assertNotIn("Refused", result)

    def test_no_sensitive_window_never_asks(self):
        result, asked = self._run(["Kareem", "File Explorer"], ask_response="no", screenshot_should_run=True)
        self.assertFalse(asked, "must not ask when nothing sensitive is open")


if __name__ == "__main__":
    unittest.main()
