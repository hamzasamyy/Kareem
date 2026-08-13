"""
Text-to-speech. Uses the engine named by TTS_ENGINE in jarvis/config.py
(default "piper"); if that engine can't start, it automatically falls back
down the chain kokoro -> piper -> pyttsx3 (the built-in Windows SAPI voice)
from that point on. Whatever is installed, speak() either works or tells you
plainly why it can't.

Kokoro's model and voices files belong in jarvis/voice/models/kokoro/. Piper
voice files belong directly in jarvis/voice/models/ so its model search can
find them. See the README's voice section for download instructions.
"""

import re
import threading
from pathlib import Path

# Module-level so the web server (which doesn't hold a direct Speaker
# reference — voice/web are wired up independently in main.py) can still
# report which TTS engine is actually active, e.g. in the status bar.
_active_engine_name = None


def get_active_tts_engine() -> str | None:
    """The currently active engine's display name (e.g. "kokoro (bm_george)",
    "piper (en_GB-alan-medium)"), or None if no Speaker has initialized yet
    or none was available."""
    return _active_engine_name


def strip_markdown(text: str) -> str:
    """Make model output speakable: remove markdown symbols the TTS engines
    would otherwise read out loud ("asterisk asterisk"). Keeps the words."""
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)   # fenced code blocks
    text = re.sub(r"`([^`]*)`", r"\1", text)                  # inline code
    text = re.sub(r"\*\*?|__?", "", text)                     # *bold* / _italics_
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.MULTILINE)  # headings
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)       # bullets
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # [label](url)
    text = normalize_for_speech(text)
    return re.sub(r"\s{2,}", " ", text).strip()


def normalize_for_speech(text: str) -> str:
    """Rewrites patterns that read fine as text but sound wrong or garbled
    spoken aloud verbatim — mainly GUC's own scraped course-code markup
    (e.g. "(|CSEN401|)") and bare "CSEN401"-style codes, which a TTS engine
    otherwise tries to pronounce as one unbroken token or reads the raw
    parens/pipes out loud. Splitting the letters from the digits with a
    space is enough for every engine here to say it as two clear words
    ("CSEN four oh one" / "CSEN 401") instead of stumbling over it."""
    # GUC's own scraped wrapper: "(|CSEN401|)" -> "CSEN401" -> "CSEN 401"
    text = re.sub(r"\(\|([A-Za-z]+\d+)\|\)", r"\1", text)
    text = re.sub(r"\|", " ", text)  # any remaining stray pipes from scraped markup
    # Any other letters-immediately-followed-by-digits token (course codes,
    # model numbers, etc.) — split so it reads as two words, not one blob.
    text = re.sub(r"\b([A-Za-z]{2,8})(\d{2,5})\b", r"\1 \2", text)
    return text

# Keep Kokoro in its own subfolder so Piper's *.onnx search cannot select it.
KOKORO_MODEL_DIR = Path(__file__).resolve().parent / "models" / "kokoro"

# Piper voice files are looked for directly in this folder (see docstring).
PIPER_MODEL_DIR = Path(__file__).resolve().parent / "models"


class Speaker:
    """speak(text) says the text out loud, choosing the best available engine."""

    def __init__(self):
        from jarvis import config

        self._kokoro = None
        self._piper_voice = None
        self._engine_name = None
        self._init_error = None
        self._init_errors = []
        # Only one thing may speak at a time — the text loop and the voice
        # loop can both call speak(), and the engines aren't thread-safe.
        self._speak_lock = threading.Lock()

        engines = ("kokoro", "piper", "pyttsx3")
        chosen = str(getattr(config, "TTS_ENGINE", "kokoro")).lower()
        if chosen not in engines:
            note = f"Unknown TTS_ENGINE '{chosen}' in config.py; trying Kokoro instead"
            self._init_errors.append(note)
            print(f"(voice note: {note})")
            chosen = "kokoro"

        for engine in engines[engines.index(chosen):]:
            if engine == "kokoro" and self._init_kokoro(config):
                self._start_warmup("kokoro")
                break
            if engine == "piper" and self._init_piper():
                self._start_warmup("piper")
                break
            if engine == "pyttsx3" and self._init_pyttsx3():
                break

        if self._engine_name is not None:
            self._announce_engine()
        else:
            details = "; ".join(self._init_errors)
            self._init_error = (
                f"No text-to-speech engine available. {details}. "
                "Run: pip install -r requirements.txt"
            )

    def _announce_engine(self):
        """Print + publish which engine is actually active right now.

        Called at initial startup AND from speak()'s mid-conversation
        Kokoro-failure fallback, so the module-level getter (which the web
        status bar reads, since the web server holds no direct Speaker
        reference) never goes stale after a runtime engine switch.
        """
        print(f"TTS engine active: {self._engine_name}")
        global _active_engine_name
        _active_engine_name = self._engine_name

    def _init_kokoro(self, config) -> bool:
        """Try to load Kokoro's CPU model and return whether it is ready."""
        model_path = KOKORO_MODEL_DIR / "kokoro-v1.0.onnx"
        voices_path = KOKORO_MODEL_DIR / "voices-v1.0.bin"
        if not model_path.exists() or not voices_path.exists():
            reason = "Kokoro voice files missing — see README section 'Kokoro voice'"
            self._init_errors.append(reason)
            print(f"(voice note: {reason}; trying the next engine.)")
            return False

        try:
            from kokoro_onnx import Kokoro

            # kokoro-onnx uses onnxruntime's CPU provider by default. Do not
            # opt into DirectML: this model is not compatible with it.
            self._kokoro = Kokoro(str(model_path), str(voices_path))
            self._engine_name = f"kokoro ({config.TTS_VOICE})"
            return True
        except Exception as e:
            reason = f"Kokoro unavailable ({e})"
            self._init_errors.append(reason)
            print(f"(voice note: {reason}; trying the next engine.)")
            return False

    def _init_piper(self) -> bool:
        """Try the first Piper model stored directly in the models folder."""
        try:
            onnx_files = sorted(PIPER_MODEL_DIR.glob("*.onnx")) if PIPER_MODEL_DIR.exists() else []
            if onnx_files:
                from piper import PiperVoice
                self._piper_voice = PiperVoice.load(str(onnx_files[0]))
                self._engine_name = f"piper ({onnx_files[0].stem})"
                return True
            self._init_errors.append("Piper voice model missing")
        except Exception as e:
            self._init_errors.append(f"Piper unavailable ({e})")
        return False

    def _init_pyttsx3(self) -> bool:
        """Check that the final Windows voice fallback can import."""
        try:
            import pyttsx3  # noqa: F401  (verify it imports; engine made per call)
            self._engine_name = "pyttsx3 (Windows voice)"
            return True
        except Exception as e:
            self._engine_name = None
            self._init_errors.append(f"pyttsx3 failed ({e})")
            return False

    def _start_warmup(self, engine: str):
        """Pay a neural engine's lazy startup cost without playing audio."""
        def warm_up():
            try:
                with self._speak_lock:
                    if engine == "kokoro" and self._kokoro is not None:
                        from jarvis import config

                        lang = "en-gb" if config.TTS_VOICE.lower().startswith("b") else "en-us"
                        self._kokoro.create(
                            "ready", voice=config.TTS_VOICE,
                            speed=config.TTS_SPEED, lang=lang,
                        )
                    elif engine == "piper" and self._piper_voice is not None:
                        import io
                        import wave

                        buf = io.BytesIO()
                        with wave.open(buf, "wb") as wav_file:
                            self._piper_voice.synthesize_wav("ready", wav_file)
            except Exception:
                pass

        threading.Thread(target=warm_up, daemon=True).start()

    @property
    def available(self) -> bool:
        return self._engine_name is not None

    @property
    def engine_name(self) -> str:
        return self._engine_name or "none"

    def speak(self, text: str):
        """Say the text out loud. Never raises — prints a note if speech fails."""
        if text:
            text = strip_markdown(text)  # don't read "asterisk asterisk" aloud
        if not text or not text.strip():
            return
        if not self.available:
            print(f"(can't speak: {self._init_error})")
            return

        try:
            with self._speak_lock:
                if self._kokoro is not None:
                    try:
                        self._speak_kokoro(text)
                    except Exception as e:
                        if self._is_unknown_kokoro_voice(e):
                            from jarvis import config

                            print(
                                f"(speech failed: unknown Kokoro voice "
                                f"'{config.TTS_VOICE}' — check TTS_VOICE in config.py)"
                            )
                            self._kokoro = None
                            self._engine_name = None
                            if self._init_piper():
                                self._announce_engine()
                                self._speak_piper(text)
                            elif self._init_pyttsx3():
                                self._announce_engine()
                                self._speak_pyttsx3(text)
                            else:
                                self._init_error = (
                                    "No text-to-speech engine available. "
                                    + "; ".join(self._init_errors)
                                )
                        else:
                            raise
                elif self._piper_voice is not None:
                    self._speak_piper(text)
                else:
                    self._speak_pyttsx3(text)
        except Exception as e:
            print(f"(speech failed: {e})")

    @staticmethod
    def _is_unknown_kokoro_voice(error: Exception) -> bool:
        """Recognize the errors kokoro-onnx uses for an absent voice id."""
        message = str(error).lower()
        return isinstance(error, KeyError) or (
            "voice" in message and any(word in message for word in ("not found", "invalid"))
        )

    def _speak_kokoro(self, text: str):
        import sounddevice as sd

        from jarvis import config

        lang = "en-gb" if config.TTS_VOICE.lower().startswith("b") else "en-us"
        samples, sample_rate = self._kokoro.create(
            text, voice=config.TTS_VOICE, speed=config.TTS_SPEED, lang=lang,
        )
        sd.play(samples, sample_rate)
        sd.wait()

    def _speak_piper(self, text: str):
        import io
        import wave

        import numpy as np
        import sounddevice as sd
        from piper.config import SynthesisConfig

        from jarvis import config

        # Piper's length_scale is the inverse of speed (higher = slower), and
        # unlike Kokoro's _speak_kokoro above, this call previously ignored
        # TTS_SPEED entirely — on Piper (the configured default engine) the
        # speed setting silently did nothing at all, which is a real
        # contributor to "unnatural/robotic pacing" complaints, not just a
        # missing nice-to-have.
        speed = getattr(config, "TTS_SPEED", 1.0) or 1.0
        syn_config = SynthesisConfig(length_scale=1.0 / speed)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav_file:
            self._piper_voice.synthesize_wav(text, wav_file, syn_config=syn_config)

        buf.seek(0)
        with wave.open(buf, "rb") as wav_file:
            samplerate = wav_file.getframerate()
            frames = wav_file.readframes(wav_file.getnframes())
        audio = np.frombuffer(frames, dtype=np.int16)
        sd.play(audio, samplerate)
        sd.wait()

    def _speak_pyttsx3(self, text: str):
        # A fresh engine per call avoids pyttsx3's "second runAndWait hangs"
        # quirk on some Windows setups. SAPI init is fast, so this is cheap.
        import pyttsx3

        engine = pyttsx3.init()
        engine.setProperty("rate", 180)
        engine.say(text)
        engine.runAndWait()
        engine.stop()


class SentenceStreamer:
    """Plays sentences from a queue on a background thread, so Jarvis can
    speak the first sentence of a streamed answer while the model is still
    generating the rest. Speaker's own lock keeps audio from overlapping."""

    def __init__(self, speaker: Speaker):
        import queue

        self._speaker = speaker
        self._q = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def feed(self, sentence: str):
        """Queue one sentence for speaking (returns immediately)."""
        if sentence and sentence.strip():
            self._q.put(sentence.strip())

    def wait(self, timeout: float = 120):
        """Block until every sentence fed so far has finished being spoken.

        Uses the queue's own task tracking (put/task_done) rather than a
        separate 'speaking' flag. That flag was set only AFTER q.get()
        returned, so wait() could observe an empty queue with the flag still
        False in the gap between get() and the flag being set — and return
        while a sentence was about to be spoken, cutting it off (or resuming
        the wake word early, so Jarvis heard itself)."""
        done = threading.Event()

        def _joiner():
            try:
                self._q.join()
            finally:
                done.set()

        threading.Thread(target=_joiner, daemon=True).start()
        done.wait(timeout)

    def _run(self):
        while True:
            sentence = self._q.get()
            try:
                self._speaker.speak(sentence)
            except Exception as e:
                print(f"(speech failed: {e})")
            finally:
                self._q.task_done()
