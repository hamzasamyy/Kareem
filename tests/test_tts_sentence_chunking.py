"""TTS sentence chunking + the Python/JS abbreviation parity guard.

The abbreviation list that keeps streamed speech from splitting mid-sentence
("Dr. Smith" -> "Dr." | "Smith") is duplicated in Python (brain.SentenceBuffer)
and JS (app.js TTS_ABBREVIATIONS) because the desktop and web HUD speak through
different engines. The parity test guards against the two silently drifting."""

import re
import unittest
from pathlib import Path

from jarvis.brain import SentenceBuffer

APP_JS = Path(__file__).resolve().parent.parent / "jarvis" / "web" / "static" / "app.js"


class SentenceBufferTests(unittest.TestCase):
    def test_does_not_split_on_a_common_abbreviation(self):
        got = []
        buffer = SentenceBuffer(got.append)
        buffer.push("Dr. Smith called. ")
        buffer.push("How are you? ")
        buffer.flush()
        self.assertEqual(got, ["Dr. Smith called.", "How are you?"])

    def test_splits_plain_sentences(self):
        got = []
        buffer = SentenceBuffer(got.append)
        buffer.push("First sentence. Second one! Third?")
        buffer.flush()
        self.assertEqual(got, ["First sentence.", "Second one!", "Third?"])


class PyJsAbbreviationParityTests(unittest.TestCase):
    def _js_abbreviations(self):
        text = APP_JS.read_text(encoding="utf-8")
        match = re.search(r"TTS_ABBREVIATIONS\s*=\s*new Set\(\[(.*?)\]\)", text, re.DOTALL)
        self.assertIsNotNone(match, "could not find TTS_ABBREVIATIONS in app.js")
        return set(re.findall(r'"([^"]+)"', match.group(1)))

    def test_python_and_js_abbreviation_sets_match(self):
        self.assertEqual(SentenceBuffer._ABBREVIATIONS, self._js_abbreviations())


if __name__ == "__main__":
    unittest.main()
