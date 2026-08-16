"""kareem.tools.calendar_auth's Reconnect Calendar support (Section 4):
get_status() (safe to poll — no network calls, no interactive OAuth) and
reconnect() (deletes token.json, runs the OAuth flow on a background
thread, never blocks the caller)."""

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kareem.tools import calendar_auth


class CalendarStatusTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._creds_path = Path(self._tmp.name) / "credentials.json"
        self._token_path = Path(self._tmp.name) / "token.json"
        self._patches = [
            patch.object(calendar_auth, "CREDENTIALS_PATH", self._creds_path),
            patch.object(calendar_auth, "TOKEN_PATH", self._token_path),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        # Isolate from any real in-progress reconnect state between tests.
        calendar_auth._reconnect_state = {
            "in_progress": False, "last_error": None, "last_result_at": None,
        }

    def test_not_configured_when_no_credentials_file(self):
        status = calendar_auth.get_status()
        self.assertEqual(status["state"], "not_configured")

    def test_disconnected_when_credentials_exist_but_no_token(self):
        self._creds_path.write_text("{}")
        status = calendar_auth.get_status()
        self.assertEqual(status["state"], "disconnected")

    def test_connected_when_token_is_valid(self):
        self._creds_path.write_text("{}")
        self._token_path.write_text("{}")
        fake_creds = MagicMock(valid=True)
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file",
                   return_value=fake_creds):
            status = calendar_auth.get_status()
        self.assertEqual(status["state"], "connected")

    def test_expired_when_token_expired_but_has_refresh_token(self):
        self._creds_path.write_text("{}")
        self._token_path.write_text("{}")
        fake_creds = MagicMock(valid=False, expired=True, refresh_token="rt")
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file",
                   return_value=fake_creds):
            status = calendar_auth.get_status()
        self.assertEqual(status["state"], "expired")

    def test_error_when_token_has_no_refresh_token(self):
        self._creds_path.write_text("{}")
        self._token_path.write_text("{}")
        fake_creds = MagicMock(valid=False, expired=True, refresh_token=None)
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file",
                   return_value=fake_creds):
            status = calendar_auth.get_status()
        self.assertEqual(status["state"], "error")

    def test_error_when_token_file_is_corrupt(self):
        self._creds_path.write_text("{}")
        self._token_path.write_text("not valid json")
        with patch("google.oauth2.credentials.Credentials.from_authorized_user_file",
                   side_effect=ValueError("bad token")):
            status = calendar_auth.get_status()
        self.assertEqual(status["state"], "error")
        self.assertIn("invalid", status["detail"])


class CalendarReconnectTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._creds_path = Path(self._tmp.name) / "credentials.json"
        self._token_path = Path(self._tmp.name) / "token.json"
        self._creds_path.write_text("{}")
        self._token_path.write_text("stale-token")
        self._patches = [
            patch.object(calendar_auth, "CREDENTIALS_PATH", self._creds_path),
            patch.object(calendar_auth, "TOKEN_PATH", self._token_path),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)
        calendar_auth._reconnect_state = {
            "in_progress": False, "last_error": None, "last_result_at": None,
        }
        calendar_auth._service = "some-cached-service"

    def test_deletes_token_and_returns_immediately_without_blocking(self):
        # _acquire_credentials would normally launch an interactive browser
        # flow — block it in a controllable way so we can prove reconnect()
        # itself returns before that flow finishes.
        release = threading.Event()

        def fake_acquire():
            release.wait(timeout=5)
            return MagicMock()

        with patch.object(calendar_auth, "_acquire_credentials", side_effect=fake_acquire):
            start = time.monotonic()
            result = calendar_auth.reconnect()
            elapsed = time.monotonic() - start

        self.assertTrue(result["started"])
        self.assertLess(elapsed, 1.0, "reconnect() must return immediately, not block on OAuth")
        self.assertFalse(self._token_path.exists(), "token.json must be deleted right away")
        self.assertIsNone(calendar_auth._service, "cached service must be dropped")
        release.set()  # let the background thread finish so it doesn't leak into other tests
        time.sleep(0.05)

    def test_refuses_concurrent_reconnect(self):
        with patch.object(calendar_auth, "_reconnect_state",
                           {"in_progress": True, "last_error": None, "last_result_at": None}):
            result = calendar_auth.reconnect()
        self.assertFalse(result["started"])

    def test_background_failure_is_recorded_and_in_progress_clears(self):
        # Keep the patch alive across the background thread's actual call —
        # a `with` block scoped only around reconnect() would race: it can
        # unpatch before the thread reaches _acquire_credentials(), letting
        # the REAL (interactive) implementation run instead of the mock.
        patcher = patch.object(calendar_auth, "_acquire_credentials",
                                side_effect=RuntimeError("consent not completed"))
        patcher.start()
        self.addCleanup(patcher.stop)
        result = calendar_auth.reconnect()
        self.assertTrue(result["started"])
        # Wait for the background thread to finish and record the failure.
        deadline = time.monotonic() + 3
        while calendar_auth._reconnect_state["in_progress"] and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertFalse(calendar_auth._reconnect_state["in_progress"])
        self.assertIn("consent not completed", calendar_auth._reconnect_state["last_error"])
        status = calendar_auth.get_status()
        self.assertEqual(status["last_error"], calendar_auth._reconnect_state["last_error"])


if __name__ == "__main__":
    unittest.main()
