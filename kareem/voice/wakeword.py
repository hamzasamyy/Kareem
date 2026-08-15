"""
Wake-word detection using a custom-trained "hey_kareem" openWakeWord model
(kareem/voice/models/hey_kareem.onnx, trained via train_kareem.py — see
that script and README.md's wake-word section), listening in the
background. This is the most finicky part of the stack on Windows — if
anything fails to initialize, start() returns False and Kareem falls back
to hotkey-only activation instead of crashing.
"""

import pathlib
import threading

SAMPLE_RATE = 16000
CHUNK_SAMPLES = 1280  # 80 ms — the chunk size openWakeWord expects
THRESHOLD = 0.5       # detection confidence 0..1; raise if it false-triggers

WAKE_MODEL_NAME = "hey_kareem"
# Resolved relative to this file, not the process's cwd, so it works
# regardless of where Kareem is launched from. Git-ignored like the other
# large voice model binaries (see .gitignore) — a fresh clone needs this
# file placed here, either copied in or regenerated with
# `python train_kareem.py`.
WAKE_MODEL_PATH = pathlib.Path(__file__).parent / "models" / f"{WAKE_MODEL_NAME}.onnx"


class WakeWordListener:
    """Runs in a background thread; calls `on_wake()` each time it hears the wake word."""

    def __init__(self, on_wake):
        self.on_wake = on_wake
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._thread = None
        self._model = None

    def start(self) -> bool:
        """Returns True if the listener is running, False if it couldn't start
        (reason is printed, never raised)."""
        if not WAKE_MODEL_PATH.exists():
            print(
                f"Note: wake word disabled ({WAKE_MODEL_PATH} not found — "
                "copy hey_kareem.onnx there, or regenerate it with "
                "`python train_kareem.py`). Use the hotkey instead."
            )
            return False
        try:
            import openwakeword

            # One-time download of the shared melspectrogram/embedding/VAD
            # models openWakeWord needs regardless of which wakeword model is
            # used (cached afterwards, works offline then) — NOT the
            # wakeword model itself, which is our own local hey_kareem.onnx,
            # not one of openWakeWord's pretrained models to fetch by name.
            openwakeword.utils.download_models()
            from openwakeword.model import Model

            self._model = Model(
                wakeword_models=[str(WAKE_MODEL_PATH)],
                inference_framework="onnx",
            )
        except Exception as e:
            print(f"Note: wake word disabled ({e}). Use the hotkey instead.")
            return False

        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        return True

    def pause(self):
        """Temporarily ignore the mic (used while Kareem is recording/speaking
        so it doesn't hear itself)."""
        self._paused.set()

    def resume(self):
        if self._model is not None:
            self._model.reset()  # clear scores so old audio can't retrigger
        self._paused.clear()

    def stop(self):
        self._stop.set()
        # Give the listener thread a moment to close the audio stream
        # cleanly so the process can exit without audio-driver complaints.
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _listen_loop(self):
        import sounddevice as sd
        import time

        try:
            while not self._stop.is_set():
                if self._paused.is_set():
                    # Fully release the mic while paused instead of holding the
                    # stream open and discarding frames. A voice interaction
                    # opens its OWN InputStream to record the command; two open
                    # capture streams on the same device fail on some Windows
                    # drivers. Closing here keeps exactly one capture live.
                    time.sleep(0.05)
                    continue
                with sd.InputStream(samplerate=SAMPLE_RATE, channels=1,
                                    dtype="int16") as stream:
                    while not self._stop.is_set() and not self._paused.is_set():
                        chunk, _ = stream.read(CHUNK_SAMPLES)
                        scores = self._model.predict(chunk[:, 0])
                        if scores.get(WAKE_MODEL_NAME, 0) >= THRESHOLD:
                            self._model.reset()
                            self.on_wake()
                # stream is closed here (device released) whenever we pause/stop
        except Exception as e:
            print(f"Note: wake word stopped working ({e}). Use the hotkey instead.")
