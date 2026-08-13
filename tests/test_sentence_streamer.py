import time
import unittest

from kareem.voice.tts import SentenceStreamer


class _SlowSpeaker:
    """Stand-in for Speaker: records what it 'spoke', slowly, so wait() has
    something real to wait for. Duck-typed — only speak() is used."""

    def __init__(self):
        self.spoken = []

    def speak(self, text):
        time.sleep(0.05)
        self.spoken.append(text)


class SentenceStreamerWaitTests(unittest.TestCase):
    def test_wait_blocks_until_all_fed_sentences_are_spoken(self):
        # Regression for the B-2 race: wait() used to be able to return in the
        # gap between q.get() and the old _speaking flag being set, leaving
        # sentences unspoken. With queue task-tracking, all 5 must be spoken.
        speaker = _SlowSpeaker()
        streamer = SentenceStreamer(speaker)
        for i in range(5):
            streamer.feed(f"sentence {i}")
        streamer.wait(timeout=10)
        self.assertEqual(len(speaker.spoken), 5)

    def test_wait_returns_promptly_when_nothing_was_fed(self):
        streamer = SentenceStreamer(_SlowSpeaker())
        start = time.monotonic()
        streamer.wait(timeout=5)
        self.assertLess(time.monotonic() - start, 1.0)


if __name__ == "__main__":
    unittest.main()
