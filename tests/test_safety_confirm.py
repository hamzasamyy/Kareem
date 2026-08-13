"""F7: the confirmation gate's accepted answers.

A typed/web confirmation used to accept only 'y'/'yes', so a typed
'yeah'/'ok'/'sure' was silently treated as a DECLINE. The accepted set is now
broadened, matched by exact token (after strip+lower) so 'no'/'nope'/'cancel'
still decline. The voice path normalizes spoken replies to 'yes'/'no' before
confirm() sees them, so this only governs typed answers."""

import unittest
from unittest.mock import patch

from jarvis import safety


class ConfirmAffirmativeTests(unittest.TestCase):
    def _confirm(self, response):
        # ask_fn bypasses console input(); patch out the log side effects so the
        # test doesn't depend on (or write to) session/log state.
        with patch("jarvis.session_log.log_event"), patch.object(safety, "log_action"):
            return safety.confirm("run_code", "Run this snippet", ask_fn=lambda _prompt: response)

    def test_broadened_affirmatives_are_accepted(self):
        for answer in ["y", "yes", "Yes", "  YEAH  ", "yep", "yup", "ok", "okay", "sure", "confirm"]:
            self.assertTrue(self._confirm(answer), f"{answer!r} should confirm")

    def test_declines_and_ambiguous_still_reject(self):
        for answer in ["n", "no", "nope", "cancel", "nah", "", "later", "maybe", "y e s"]:
            self.assertFalse(self._confirm(answer), f"{answer!r} should decline")

    def test_affirmative_set_contains_the_broadened_words(self):
        for word in ("y", "yes", "yeah", "yep", "ok", "okay", "sure"):
            self.assertIn(word, safety._AFFIRMATIVE_RESPONSES)


if __name__ == "__main__":
    unittest.main()
