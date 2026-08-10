import unittest

from jarvis import config
from jarvis.voice import stt


class InitialPromptTests(unittest.TestCase):
    """The STT vocabulary hint (config.STT_VOCABULARY -> Whisper initial_prompt).

    Importing jarvis.voice.stt is cheap (numpy/faster-whisper are imported
    lazily inside the functions), so this exercises the pure prompt builder
    without loading a speech model.
    """

    def setUp(self):
        self._had_attr = hasattr(config, "STT_VOCABULARY")
        self._saved = getattr(config, "STT_VOCABULARY", None)

    def tearDown(self):
        if self._had_attr:
            config.STT_VOCABULARY = self._saved
        elif hasattr(config, "STT_VOCABULARY"):
            del config.STT_VOCABULARY

    def test_none_when_empty(self):
        config.STT_VOCABULARY = []
        self.assertIsNone(stt._initial_prompt())

    def test_none_when_attribute_missing(self):
        if hasattr(config, "STT_VOCABULARY"):
            del config.STT_VOCABULARY
        self.assertIsNone(stt._initial_prompt())

    def test_joins_terms_with_commas(self):
        config.STT_VOCABULARY = ["Jarvis", "CSEN", "DMET"]
        self.assertEqual(stt._initial_prompt(), "Jarvis, CSEN, DMET")

    def test_strips_whitespace_and_drops_blanks(self):
        config.STT_VOCABULARY = ["  Jarvis ", "", "   ", "MET"]
        self.assertEqual(stt._initial_prompt(), "Jarvis, MET")


if __name__ == "__main__":
    unittest.main()
