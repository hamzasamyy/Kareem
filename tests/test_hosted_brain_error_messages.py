"""Reproduced bug (companion to the token-budget fix in
kareem/agent.py's MAX_TOOL_RESULT_CHARS): "who won the last F1 race?" hit
Groq's 8000 tokens/minute limit (8750 requested). Two problems with what the
user then saw:

  1. The error text was Groq's raw 429 body verbatim — organization id,
     service tier, exact token counts, and a billing-upgrade link, none of
     which belongs in a personal-assistant error message.
  2. The fallback hint ("This usually means no internet, or a wrong
     key/model") was shown anyway, which is flatly wrong for a rate-limit/
     size failure and points debugging in the wrong direction.

These pin kareem.brain._is_size_or_rate_limit_error / _size_or_rate_limit_message
and HostedBrain._create_with_retry's use of them."""

import unittest

import httpx
from openai import RateLimitError

from kareem.brain import (
    HostedBrain,
    _is_size_or_rate_limit_error,
    _size_or_rate_limit_message,
)

# A realistic Groq 429 body: clean prose (no '{'/'<') that user_safe_error's
# blob-trimming would NOT touch, containing exactly the leaky details the
# bug reported.
_GROQ_RATE_LIMIT_TEXT = (
    "Rate limit reached for model `openai/gpt-oss-120b` in organization "
    "`org_01j9xyzredacted` service tier `on_demand` on tokens per minute "
    "(TPM): Limit 8000, Used 0, Requested 8750. Please try again in 5.625s. "
    "Visit https://console.groq.com/docs/rate-limits to learn more. Need "
    "more tokens? Upgrade to Dev Tier at https://console.groq.com/settings/billing."
)


def _real_rate_limit_error(message=_GROQ_RATE_LIMIT_TEXT):
    """A genuine openai.RateLimitError (not a duck-typed stand-in) so
    isinstance-based detection is exercised for real."""
    response = httpx.Response(
        429, request=httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    )
    return RateLimitError(message, response=response, body={"error": {"message": message}})


class SizeOrRateLimitDetectionTests(unittest.TestCase):
    def test_real_ratelimiterror_is_detected_by_type(self):
        self.assertTrue(_is_size_or_rate_limit_error(_real_rate_limit_error()))

    def test_request_too_large_detected_by_keyword_even_as_a_plain_exception(self):
        # A 400 "request too large" can land as a different exception shape
        # than RateLimitError depending on provider — keyword fallback covers it.
        self.assertTrue(_is_size_or_rate_limit_error(
            RuntimeError("Request too large for model, please reduce your context length.")
        ))

    def test_context_length_exceeded_detected_by_keyword(self):
        self.assertTrue(_is_size_or_rate_limit_error(
            ValueError("This model's maximum context length is 8192 tokens.")
        ))

    def test_ordinary_connection_error_is_not_detected(self):
        self.assertFalse(_is_size_or_rate_limit_error(
            ConnectionError("Connection refused")
        ))

    def test_ordinary_auth_error_is_not_detected(self):
        self.assertFalse(_is_size_or_rate_limit_error(
            RuntimeError("Invalid API key provided")
        ))


class SizeOrRateLimitMessageTests(unittest.TestCase):
    def test_message_contains_no_org_id_tier_counts_or_billing_link(self):
        msg = _size_or_rate_limit_message(_real_rate_limit_error())
        for leak in ("org_", "service tier", "on_demand", "8750", "8000",
                     "console.groq.com", "billing", "Dev Tier"):
            self.assertNotIn(leak, msg)

    def test_message_is_the_specific_clean_wording(self):
        msg = _size_or_rate_limit_message(_real_rate_limit_error())
        self.assertEqual(
            msg,
            "That question needed more information than I could process at "
            "once — try asking something more specific.",
        )

    def test_message_never_mentions_internet_or_api_key(self):
        # The old fallback hint ("no internet, or a wrong key/model") must
        # not appear on this path — it's misleading for this error class.
        msg = _size_or_rate_limit_message(_real_rate_limit_error())
        self.assertNotIn("internet", msg.lower())
        self.assertNotIn("key", msg.lower())


class CreateWithRetryErrorSurfaceTests(unittest.TestCase):
    """End-to-end through HostedBrain._create_with_retry: the exact method
    that turned Groq's raw 429 into what the user saw."""

    def _make_brain(self):
        brain = HostedBrain.__new__(HostedBrain)
        brain.model = "openai/gpt-oss-120b"
        return brain

    def test_exhausted_rate_limit_retries_raises_clean_message_no_leak(self):
        brain = self._make_brain()
        brain._client = type("C", (), {})()
        brain._client.chat = type("Chat", (), {})()
        brain._client.chat.completions = type("Completions", (), {})()
        brain._client.chat.completions.create = _mock_raiser(_real_rate_limit_error())

        with _no_sleep():
            with self.assertRaises(RuntimeError) as ctx:
                brain._create_with_retry([{"role": "user", "content": "hi"}], {})

        message = str(ctx.exception)
        self.assertEqual(
            message,
            "That question needed more information than I could process at "
            "once — try asking something more specific.",
        )
        for leak in ("org_", "service tier", "8750", "console.groq.com", "billing"):
            self.assertNotIn(leak, message)
        self.assertNotIn("internet", message.lower())

    def test_request_too_large_as_a_non_ratelimiterror_also_gets_clean_message(self):
        # Simulates a provider returning request-too-large as a plain
        # exception rather than a RateLimitError subclass.
        brain = self._make_brain()
        brain._client = type("C", (), {})()
        brain._client.chat = type("Chat", (), {})()
        brain._client.chat.completions = type("Completions", (), {})()
        brain._client.chat.completions.create = _mock_raiser(
            RuntimeError("Request too large: reduce your context length and try again.")
        )

        with self.assertRaises(RuntimeError) as ctx:
            brain._create_with_retry([{"role": "user", "content": "hi"}], {})

        message = str(ctx.exception)
        self.assertEqual(
            message,
            "That question needed more information than I could process at "
            "once — try asking something more specific.",
        )
        self.assertNotIn("internet", message.lower())

    def test_genuine_connection_error_still_gets_the_connectivity_hint(self):
        # Ground rule: don't regress the existing, CORRECT hint for real
        # connectivity failures.
        brain = self._make_brain()
        brain._client = type("C", (), {})()
        brain._client.chat = type("Chat", (), {})()
        brain._client.chat.completions = type("Completions", (), {})()
        brain._client.chat.completions.create = _mock_raiser(
            ConnectionError("Connection refused")
        )

        with self.assertRaises(RuntimeError) as ctx:
            brain._create_with_retry([{"role": "user", "content": "hi"}], {})

        message = str(ctx.exception)
        self.assertIn("no internet, or a wrong key/model", message)


def _mock_raiser(exc):
    def _raise(*args, **kwargs):
        raise exc
    return _raise


class _no_sleep:
    """Context manager: patches time.sleep to a no-op so the retry-exhaustion
    test doesn't actually wait ~12s (4s + 8s backoff)."""

    def __enter__(self):
        import time
        self._original = time.sleep
        time.sleep = lambda *_: None
        return self

    def __exit__(self, *exc_info):
        import time
        time.sleep = self._original


if __name__ == "__main__":
    unittest.main()
