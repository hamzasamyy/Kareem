"""
Speech-to-text using faster-whisper, running locally (your voice never
leaves the PC). The model is downloaded automatically the first time and
cached; model size + compute type come from jarvis/config.py (STT_MODEL,
STT_COMPUTE_TYPE — "tiny.en" + "int8" is the fast default).

Even faster option (not enabled): Groq hosts Whisper
("whisper-large-v3-turbo") on its free tier, callable with the same OpenAI
client Jarvis already uses for the hosted brain
(client.audio.transcriptions.create(model="whisper-large-v3-turbo",
file=open(wav_path, "rb"))). It's both faster AND more accurate than local
tiny.en — the tradeoff is your mic audio gets sent to Groq's servers, which
is why local transcription stays the default.
"""

import threading

from jarvis import config

_model = None
_model_lock = threading.Lock()


def get_model():
    """Lazy-loads the whisper model once. Raises RuntimeError with a
    plain-English message if faster-whisper isn't usable. Thread-safe: the
    desktop voice loop and the web server can both warm this at startup."""
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        # Double-checked: another thread may have loaded it while we waited.
        if _model is not None:
            return _model

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "faster-whisper isn't installed. Fix: pip install -r requirements.txt"
            )

        try:
            model = WhisperModel(
                config.STT_MODEL,
                device="cpu",
                compute_type=getattr(config, "STT_COMPUTE_TYPE", "int8"),
            )
        except Exception as e:
            raise RuntimeError(
                f"Couldn't load the speech-recognition model '{config.STT_MODEL}': {e}"
            )
        # Publish only once fully constructed, so a failed load never leaves a
        # half-built model visible to another thread.
        _model = model
        return _model


def _normalize_audio(audio_f32, target_peak: float = 0.95):
    """Boost quiet audio toward target_peak so a quiet/distant mic doesn't
    feed Whisper a low-amplitude signal it recognizes worse than a clear one.
    Never attenuates an already-strong recording, and no-ops on (near-)
    silent audio so noise isn't amplified into something audible."""
    import numpy as np

    peak = float(np.abs(audio_f32).max()) if audio_f32.size else 0.0
    if peak < 1e-4 or peak >= target_peak:
        return audio_f32
    return audio_f32 * (target_peak / peak)


def _transcribe_groq(audio_f32) -> str:
    """Send audio to Groq's hosted whisper-large-v3-turbo — faster AND more
    accurate than any local model here — via the same OpenAI-compatible
    client/key used for the hosted brain. Only called when STT_ENGINE =
    "groq" in config.py (opt-in; local stays the default because this sends
    mic audio to a third party). Raises on any failure — the caller falls
    back to the local model rather than losing the turn."""
    import io
    import os
    import wave

    import numpy as np
    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    api_key = os.getenv("HOSTED_API_KEY")
    if not api_key:
        raise RuntimeError("No HOSTED_API_KEY configured — the Groq STT engine needs one.")

    pcm16 = np.clip(audio_f32 * 32768.0, -32768, 32767).astype(np.int16)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(pcm16.tobytes())

    client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key, timeout=20)
    result = client.audio.transcriptions.create(
        model="whisper-large-v3-turbo",
        file=("audio.wav", buf.getvalue()),
        language="en",
    )
    return (result.text or "").strip()


def _initial_prompt():
    """Build a Whisper initial-prompt string from config.STT_VOCABULARY, or
    None when none is set. Biasing the decoder toward the user's frequent
    names/terms measurably improves recognition of the proper nouns a small
    model otherwise garbles; returning None preserves the original behavior."""
    vocab = getattr(config, "STT_VOCABULARY", None) or []
    terms = [str(term).strip() for term in vocab if str(term).strip()]
    return ", ".join(terms) if terms else None


def transcribe(audio) -> str:
    """audio: int16 mono numpy array at 16 kHz (from vad.record_until_silence).
    Returns the recognized text ('' if nothing was understood)."""
    import numpy as np

    # whisper wants float32 in [-1, 1]
    audio_f32 = audio.astype(np.float32) / 32768.0
    audio_f32 = _normalize_audio(audio_f32)

    if getattr(config, "STT_ENGINE", "local") == "groq":
        try:
            return _transcribe_groq(audio_f32)
        except Exception as e:
            print(f"(Groq speech recognition unavailable ({e}) — using the local model instead.)")

    model = get_model()
    segments, _info = model.transcribe(
        audio_f32,
        language="en",
        beam_size=5,                       # more accurate than greedy (was 1)
        vad_filter=True,                   # drop non-speech so silence/room
                                           # noise isn't "heard" as phantom words
        condition_on_previous_text=False,  # each command is independent
        initial_prompt=_initial_prompt(),  # bias toward STT_VOCABULARY terms
    )
    return " ".join(seg.text.strip() for seg in segments).strip()
