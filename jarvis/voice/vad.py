"""
Microphone recording with end-of-speech detection: records from the default
mic and stops automatically about a second after you stop talking (or at a
hard cap), using webrtcvad to tell speech from silence.
"""

SAMPLE_RATE = 16000        # what whisper and webrtcvad both want
FRAME_MS = 30              # webrtcvad supports 10/20/30 ms frames
SILENCE_END_SEC = 0.7      # stop this long after speech ends (lower = snappier;
                           # keep >=0.6 so trailing words aren't clipped)
MAX_RECORD_SEC = 15        # hard cap so it can never record forever
START_TIMEOUT_SEC = 6      # give up if no speech starts within this window


def record_until_silence() -> "np.ndarray | None":
    """Record one utterance from the mic. Returns int16 mono audio at 16 kHz,
    or None if nothing was said / the mic isn't usable."""
    import numpy as np
    import sounddevice as sd
    import webrtcvad

    vad = webrtcvad.Vad(2)  # 0..3, higher = more aggressive silence detection
    frame_samples = SAMPLE_RATE * FRAME_MS // 1000

    frames = []
    speech_started = False
    silence_frames = 0
    silence_needed = int(SILENCE_END_SEC * 1000 / FRAME_MS)
    max_frames = int(MAX_RECORD_SEC * 1000 / FRAME_MS)
    start_timeout_frames = int(START_TIMEOUT_SEC * 1000 / FRAME_MS)

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16") as stream:
            for i in range(max_frames):
                chunk, _ = stream.read(frame_samples)
                frame = chunk[:, 0]
                frames.append(frame)

                is_speech = vad.is_speech(frame.tobytes(), SAMPLE_RATE)
                if is_speech:
                    speech_started = True
                    silence_frames = 0
                elif speech_started:
                    silence_frames += 1
                    if silence_frames >= silence_needed:
                        break
                elif i >= start_timeout_frames:
                    return None  # user never spoke
    except Exception as e:
        print(f"(microphone problem: {e})")
        return None

    if not speech_started:
        return None
    return np.concatenate(frames)
