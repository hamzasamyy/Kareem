import unittest
from unittest import mock

import numpy as np

from kareem import config
from kareem.voice import stt


class NormalizeAudioTests(unittest.TestCase):
    def test_quiet_audio_boosted_to_target_peak(self):
        audio = np.array([0.1, -0.05, 0.02], dtype=np.float32)
        result = stt._normalize_audio(audio, target_peak=0.95)
        self.assertAlmostEqual(float(np.abs(result).max()), 0.95, places=4)

    def test_already_loud_audio_left_untouched(self):
        audio = np.array([0.98, -0.5, 0.1], dtype=np.float32)
        result = stt._normalize_audio(audio, target_peak=0.95)
        np.testing.assert_array_equal(result, audio)

    def test_near_silent_audio_left_untouched(self):
        # Must NOT amplify noise-floor hiss into something audible.
        audio = np.array([1e-6, -2e-6, 0.0], dtype=np.float32)
        result = stt._normalize_audio(audio, target_peak=0.95)
        np.testing.assert_array_equal(result, audio)

    def test_empty_audio_does_not_crash(self):
        audio = np.array([], dtype=np.float32)
        result = stt._normalize_audio(audio, target_peak=0.95)
        self.assertEqual(result.size, 0)

    def test_scaling_is_uniform(self):
        audio = np.array([0.1, 0.2, -0.05], dtype=np.float32)
        result = stt._normalize_audio(audio, target_peak=0.95)
        self.assertAlmostEqual(float(result[1] / result[0]), 2.0, places=4)


class TranscribeEngineDispatchTests(unittest.TestCase):
    """transcribe() must dispatch on STT_ENGINE and always degrade to the
    local model rather than losing the user's command (matches the rest of
    the codebase's try-then-fall-back-and-say-so philosophy)."""

    def setUp(self):
        self._saved_engine = getattr(config, "STT_ENGINE", "local")

    def tearDown(self):
        config.STT_ENGINE = self._saved_engine

    @staticmethod
    def _fake_local_model():
        model = mock.Mock()
        segment = mock.Mock()
        segment.text = " local text "
        model.transcribe.return_value = ([segment], None)
        return model

    def test_default_local_engine_never_calls_groq(self):
        config.STT_ENGINE = "local"
        audio = np.zeros(1600, dtype=np.int16)
        with mock.patch.object(stt, "_transcribe_groq") as fake_groq, \
             mock.patch.object(stt, "get_model", return_value=self._fake_local_model()) as fake_get_model:
            result = stt.transcribe(audio)
        fake_groq.assert_not_called()
        fake_get_model.assert_called_once()
        self.assertEqual(result, "local text")

    def test_groq_engine_used_when_configured(self):
        config.STT_ENGINE = "groq"
        audio = np.zeros(1600, dtype=np.int16)
        with mock.patch.object(stt, "_transcribe_groq", return_value="groq text") as fake_groq, \
             mock.patch.object(stt, "get_model") as fake_get_model:
            result = stt.transcribe(audio)
        fake_groq.assert_called_once()
        fake_get_model.assert_not_called()
        self.assertEqual(result, "groq text")

    def test_groq_failure_falls_back_to_local_instead_of_raising(self):
        config.STT_ENGINE = "groq"
        audio = np.zeros(1600, dtype=np.int16)
        with mock.patch.object(stt, "_transcribe_groq", side_effect=RuntimeError("no key")), \
             mock.patch.object(stt, "get_model", return_value=self._fake_local_model()) as fake_get_model:
            result = stt.transcribe(audio)
        fake_get_model.assert_called_once()
        self.assertEqual(result, "local text")


if __name__ == "__main__":
    unittest.main()
