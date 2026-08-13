"""
Voice controller — glues together wake word / hotkey -> record -> transcribe
-> agent -> speak. Runs entirely in background threads so the console text
chat keeps working at the same time.

Every piece degrades gracefully:
- no TTS engine        -> replies are print-only
- wake word won't init -> hotkey still works (a note is printed)
- hotkey won't init    -> wake word still works (a note is printed)
- mic/STT broken       -> voice is disabled with a clear message
"""

import threading

from jarvis import config, safety


def _hotkey_to_pynput(hotkey: str) -> str:
    """'ctrl+alt+j' -> '<ctrl>+<alt>+j' (pynput's format)."""
    parts = [p.strip().lower() for p in hotkey.split("+")]
    out = []
    for p in parts:
        if len(p) > 1:  # modifier or named key
            out.append(f"<{p}>")
        else:
            out.append(p)
    return "+".join(out)


class VoiceController:
    def __init__(self, agent, agent_lock: threading.Lock):
        self.agent = agent
        self.agent_lock = agent_lock
        self.speaker = None
        self.wakeword = None
        self.streamer = None  # set by main.py when speak-while-generating is on
        self.open_web = None  # set by main.py: opens/focuses the web UI
        self._hotkey_listener = None
        self._busy = threading.Lock()  # one voice interaction at a time

    # ------------------------------------------------------------------ setup

    def start(self) -> bool:
        """Starts whatever voice features are available. Returns True if at
        least listening (hotkey or wake word) is up."""
        # --- speaking ---
        try:
            from jarvis.voice.tts import Speaker
            self.speaker = Speaker()
            if self.speaker.available:
                print(f"Voice output: {self.speaker.engine_name}")
            else:
                print("Voice output unavailable — replies will be text-only.")
        except Exception as e:
            print(f"Voice output unavailable ({e}) — replies will be text-only.")

        # --- check the mic/STT stack imports before promising anything ---
        try:
            import sounddevice  # noqa: F401
            import webrtcvad    # noqa: F401
        except Exception as e:
            print(f"Voice input unavailable ({e}). Text chat still works.")
            return False

        # Warm the speech-to-text model in the background so the FIRST voice
        # command doesn't pay the one-time model-load cost (~1-2s). get_model()
        # is cached and thread-safe, so this is harmless even if the web server
        # warms the same model too. Never blocks startup.
        def _prewarm_stt():
            try:
                from jarvis.voice import stt
                stt.get_model()
            except Exception:
                pass

        threading.Thread(target=_prewarm_stt, daemon=True, name="jarvis-stt-warmup").start()

        listening = False

        # --- global hotkey ---
        if config.HOTKEY:
            try:
                from pynput import keyboard
                combo = _hotkey_to_pynput(config.HOTKEY)
                self._hotkey_listener = keyboard.GlobalHotKeys({combo: self._on_trigger})
                self._hotkey_listener.daemon = True
                self._hotkey_listener.start()
                print(f"Hotkey ready: press {config.HOTKEY} to talk.")
                listening = True
            except Exception as e:
                print(f"Note: global hotkey disabled ({e}).")

        # --- wake word ---
        if config.WAKE_WORD_ENABLED:
            try:
                from jarvis.voice.wakeword import WakeWordListener
                self.wakeword = WakeWordListener(on_wake=self._on_trigger)
                if self.wakeword.start():
                    print('Wake word ready: say "hey jarvis" to talk.')
                    listening = True
                else:
                    self.wakeword = None
            except Exception as e:
                print(f"Note: wake word disabled ({e}).")
                self.wakeword = None

        if not listening:
            print("No voice trigger could start — Kareem is text-only this session.")
        return listening

    def stop(self):
        if self.wakeword:
            self.wakeword.stop()
        if self._hotkey_listener:
            self._hotkey_listener.stop()

    def speak_reply(self, text: str):
        """Speak a reply from the text loop, muting the wake word while the
        speakers are on so Jarvis can't trigger itself."""
        if not (self.speaker and self.speaker.available):
            return
        if self.wakeword:
            self.wakeword.pause()
        try:
            self.speaker.speak(text)
        finally:
            if self.wakeword:
                self.wakeword.resume()

    # ------------------------------------------------------------- interaction

    def _say(self, text: str):
        # (the wake word is already paused during voice interactions)
        if self.speaker and self.speaker.available:
            self.speaker.speak(text)

    def _listen_once(self) -> str:
        """Record one utterance and return the recognized text ('' if none)."""
        from jarvis.voice import stt, vad

        audio = vad.record_until_silence()
        if audio is None:
            return ""
        return stt.transcribe(audio)

    def _voice_confirm(self, prompt: str) -> str:
        """Spoken version of the safety confirmation: reads the question out
        loud and listens for yes/no. Also printed, and defaults to NO if the
        answer isn't understood."""
        print(f"\n{prompt}", flush=True)
        self._say(prompt.replace("(y/n)", "yes or no"))
        answer = self._listen_once().lower()
        print(f"(heard: {answer or 'nothing'})")
        return "yes" if any(w in answer for w in ("yes", "yeah", "yep", "sure", "go ahead")) else "no"

    def _on_trigger(self):
        """Called by the wake word or hotkey. Runs the whole interaction in a
        fresh thread so the trigger callbacks never block."""
        if not self._busy.acquire(blocking=False):
            return  # already mid-conversation; ignore extra triggers
        threading.Thread(target=self._handle_interaction, daemon=True).start()

    def _handle_interaction(self):
        try:
            # New default: the trigger opens/focuses the web UI and the
            # conversation continues in the browser. Set WAKE_OPENS_WEB =
            # False in jarvis/config.py for the old talk-out-loud behavior.
            if getattr(config, "WAKE_OPENS_WEB", True) and self.open_web:
                print("\n[opening the Kareem web interface…]", flush=True)
                try:
                    self.open_web()
                except Exception as e:
                    print(f"(couldn't open the web interface: {e})")
                return

            if self.wakeword:
                self.wakeword.pause()

            print("\n[listening…]", flush=True)
            self._say("Yes?")
            text = self._listen_once()
            if not text:
                print("(didn't catch anything)")
                self._say("Sorry, I didn't catch that.")
                return

            print(f"You (voice): {text}")

            spoken = {"count": 0}

            def say_sentence(s):
                spoken["count"] += 1
                self.streamer.feed(s)

            on_sentence = say_sentence if self.streamer else None

            # Install the spoken-confirmation handler INSIDE the lock and
            # restore whatever was there before (mirroring the web server), so
            # a concurrent console/web turn can't have its confirmation routed
            # to voice, and voice can't clobber another loop's handler to None.
            with self.agent_lock:
                previous_ask = safety.set_ask_fn(self._voice_confirm)
                try:
                    reply = self.agent.send(text, on_sentence=on_sentence)
                finally:
                    safety.set_ask_fn(previous_ask)

            if not self.agent.last_streamed:
                print(f"Kareem: {reply}\n")

            if spoken["count"]:
                self.streamer.wait()  # sentences were spoken while generating
            else:
                self._say(reply)
        except Exception as e:
            print(f"(voice interaction failed: {e})")
        finally:
            if self.wakeword:
                self.wakeword.resume()
            self._busy.release()
