"""F1 / #3: the shared user-facing error sanitizer.

Every surface that reports a failure (web server, console, calendar tool,
hosted brain) routes exceptions through jarvis.errors.user_safe_error so a raw
JSON/dict body, request URL, or nested exception repr can never reach the user.
These lock in that guarantee and the two former per-module copies now folded
into it (brain's provider errors, calendar's Google HttpError)."""

import unittest

from jarvis.errors import user_safe_error
from jarvis.tools import calendar as calendar_tool


class _FakeGoogleHttpError(Exception):
    """Duck-types googleapiclient.errors.HttpError: a clean .reason phrase plus
    a str() that embeds the raw JSON details and the request URL."""

    def __init__(self, reason, raw):
        super().__init__(raw)
        self.reason = reason
        self._raw = raw

    def __str__(self):
        return self._raw


class _FakeOpenAIError(Exception):
    """Duck-types an openai APIStatusError: a parsed .body dict + a .message."""

    def __init__(self, raw, body=None, message=None):
        super().__init__(raw)
        self.body = body
        if message is not None:
            self.message = message


class UserSafeErrorTests(unittest.TestCase):
    def test_openai_rate_limit_uses_body_message_not_the_json(self):
        exc = _FakeOpenAIError(
            "Error code: 429 - {'error': {'message': 'Rate limit reached, retry in 2s', 'type': 'tokens'}}",
            body={"error": {"message": "Rate limit reached, retry in 2s", "type": "tokens"}},
            message="Error code: 429 - {'error': {'message': 'x'}}",
        )
        self.assertEqual(user_safe_error(exc), "Rate limit reached, retry in 2s")

    def test_google_httperror_uses_reason_phrase(self):
        exc = _FakeGoogleHttpError(
            "Insufficient Permission",
            '<HttpError 403 when requesting https://www.googleapis.com/calendar/v3/x '
            'returned "Insufficient Permission". Details: "[{\'message\': \'x\'}]">',
        )
        self.assertEqual(user_safe_error(exc), "Insufficient Permission")

    def test_plain_prose_message_passes_through(self):
        self.assertEqual(user_safe_error(RuntimeError("No internet connection.")), "No internet connection.")

    def test_never_returns_a_brace_or_angle_blob(self):
        for exc in [
            _FakeOpenAIError("Error code: 500 - {'error': 'boom'}"),
            RuntimeError("client could not be created: <HttpError 500 {'e': 1}>"),
            ValueError("{'raw': 'dict', 'leaked': True}"),
            Exception("<html><body>500</body></html>"),
        ]:
            out = user_safe_error(exc)
            self.assertNotIn("{", out)
            self.assertNotIn("<", out)
            self.assertTrue(out)  # never empty

    def test_all_blob_falls_back_to_type_name(self):
        self.assertEqual(user_safe_error(ValueError("{}")), "ValueError")

    def test_leading_prose_before_a_blob_is_kept(self):
        # A nested-exception repr tail is dropped, but the useful prose survives.
        self.assertEqual(
            user_safe_error(RuntimeError("could not build client: <HttpError 500>")),
            "could not build client",
        )


class CalendarErrorSurfaceTests(unittest.TestCase):
    def test_httperror_surfaces_clean_reason(self):
        exc = _FakeGoogleHttpError("Not Found", '<HttpError 404 ... Details: "[{\'m\': 1}]">')
        self.assertEqual(calendar_tool._error(exc), "Google rejected that request: Not Found")

    def test_runtime_error_is_the_connection_message(self):
        msg = calendar_tool._error(RuntimeError("token.json is invalid — remove it and reconnect"))
        self.assertTrue(msg.startswith("Google Calendar isn't connected: token.json is invalid"))

    def test_nested_repr_tail_never_leaks(self):
        msg = calendar_tool._error(RuntimeError("client could not be created: <HttpError 500 {'e': 1}>"))
        self.assertNotIn("<", msg)
        self.assertNotIn("{", msg)


if __name__ == "__main__":
    unittest.main()
