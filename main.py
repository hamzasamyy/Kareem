"""
Jarvis entrypoint.

Run it with:
    python main.py            -> starts Jarvis (text chat + voice if available)
    python main.py --check    -> verifies your setup without starting Jarvis
    python main.py --no-voice -> text chat only, skip all voice features
"""

import sys
import threading
import json
import time
from pathlib import Path

if sys.stdout is None or sys.stderr is None:
    _background_log = open(
        Path(__file__).resolve().parent / "jarvis_background.log",
        "a", encoding="utf-8", buffering=1,
    )
    if sys.stdout is None:
        sys.stdout = _background_log
    if sys.stderr is None:
        sys.stderr = _background_log

# The Windows console often defaults to a legacy encoding (cp1252) that
# can't print emoji or some model output — switch to UTF-8 so Jarvis never
# crashes just because a reply contains a fancy character.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stdin, "reconfigure"):
    # utf-8-sig transparently strips the byte-order mark PowerShell adds
    # when text is piped into Jarvis
    sys.stdin.reconfigure(encoding="utf-8-sig", errors="replace")


def run_check():
    """Verifies Ollama is reachable and required dependencies import cleanly.
    Prints a plain-English PASS/FAIL report — no stack traces."""
    print("Jarvis setup check\n" + "=" * 40)
    all_ok = True

    # --- Python version ---
    py_ver = sys.version_info
    if py_ver >= (3, 11):
        print(f"[OK]   Python {py_ver.major}.{py_ver.minor}.{py_ver.micro}")
    else:
        print(f"[FAIL] Python {py_ver.major}.{py_ver.minor} — Jarvis needs Python 3.11+.")
        print("       Fix: install Python 3.11+ from https://python.org and re-run.")
        all_ok = False

    # --- Config loads ---
    try:
        from jarvis import config
        print(f"[OK]   config.py loaded (BRAIN = '{config.BRAIN}')")
    except Exception as e:
        print(f"[FAIL] Couldn't load jarvis/config.py: {e}")
        return False

    # --- Core dependency imports ---
    deps = [
        ("dotenv", "python-dotenv"),
        ("requests", "requests"),
        ("bs4", "beautifulsoup4"),
        ("ddgs", "ddgs"),
        ("pygetwindow", "pygetwindow"),
    ]
    if config.BRAIN == "ollama":
        deps.append(("ollama", "ollama"))
    if config.BRAIN == "hosted":
        deps.append(("openai", "openai"))
    if config.BRAIN == "claude":
        deps.append(("anthropic", "anthropic"))

    for module_name, pip_name in deps:
        try:
            __import__(module_name)
            print(f"[OK]   {pip_name} is installed")
        except ImportError:
            print(f"[FAIL] {pip_name} is not installed.")
            print("       Fix: pip install -r requirements.txt")
            all_ok = False

    # --- Google Calendar (optional) ---
    project_root = Path(__file__).resolve().parent
    if (project_root / "credentials.json").is_file():
        print("[OK]   Google Calendar credentials found (credentials.json)")
        if (project_root / "token.json").is_file():
            print("[OK]   Google Calendar already authorized (token.json present)")
        for module_name, pip_name in [
            ("google_auth_oauthlib", "google-auth-oauthlib"),
            ("googleapiclient", "google-api-python-client"),
            ("tzlocal", "tzlocal"),
        ]:
            try:
                __import__(module_name)
                print(f"[OK]   {pip_name} is installed (Google Calendar)")
            except ImportError:
                print(f"[WARN] {pip_name} missing — Google Calendar won't connect. "
                      "Fix: pip install -r requirements.txt")
    else:
        print("[WARN] Google Calendar features are disabled — credentials.json is missing. "
              "See 'Google Calendar (optional)' in README.md")

    # --- Web interface dependencies (warnings only — console works without) ---
    for module_name, pip_name in [("fastapi", "fastapi"), ("uvicorn", "uvicorn")]:
        try:
            __import__(module_name)
            print(f"[OK]   {pip_name} is installed (web interface)")
        except ImportError:
            print(f"[WARN] {pip_name} missing — the browser UI won't start. "
                  "Fix: pip install -r requirements.txt")

    # --- Playwright browser (needed for browser_open/browser_click AND the
    #     GUC integration, which both drive Chromium under the hood; warning
    #     only — Jarvis still starts, those tools just report a clear error
    #     on first use instead of working) ---
    try:
        import playwright  # noqa: F401
        print("[OK]   playwright is installed")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                chromium_path = Path(p.chromium.executable_path)
            if chromium_path.exists():
                print("[OK]   Playwright's Chromium browser is downloaded")
            else:
                print("[WARN] Playwright's Chromium browser isn't downloaded yet — "
                      "browser control and GUC checks won't work.")
                print("       Fix: playwright install chromium")
        except Exception as e:
            print(f"[WARN] Couldn't verify Playwright's Chromium browser ({e}).")
            print("       Fix: playwright install chromium")
    except ImportError:
        print("[WARN] playwright is not installed — browser control and GUC "
              "checks won't work.")
        print("       Fix: pip install -r requirements.txt")

    # --- Voice dependencies (warnings only — text chat works without them) ---
    voice_deps = [
        ("sounddevice", "sounddevice"),
        ("webrtcvad", "webrtcvad-wheels"),
        ("faster_whisper", "faster-whisper"),
        ("openwakeword", "openwakeword"),
        ("pyttsx3", "pyttsx3"),
        ("pynput", "pynput"),
    ]
    for module_name, pip_name in voice_deps:
        try:
            __import__(module_name)
            print(f"[OK]   {pip_name} is installed (voice)")
        except ImportError:
            print(f"[WARN] {pip_name} missing — voice features limited. "
                  "Fix: pip install -r requirements.txt")

    import shutil as _shutil
    if _shutil.which("ffmpeg"):
        print("[OK]   ffmpeg found")
    else:
        print("[WARN] ffmpeg not found — speech-to-text may not work. "
              "Fix: winget install ffmpeg (then reopen the terminal)")

    # --- Hosted brain key (only matters if BRAIN = "hosted") ---
    if config.BRAIN == "hosted":
        import os
        from dotenv import load_dotenv
        load_dotenv()
        key_env = getattr(config, "HOSTED_API_KEY_ENV", "HOSTED_API_KEY")
        if os.getenv(key_env):
            print(f"[OK]   {key_env} found in .env (model: {config.HOSTED_MODEL})")
        else:
            print(f"[FAIL] No {key_env} in .env (needed for HOSTED_MODEL {config.HOSTED_MODEL}).")
            print(f"       Fix: add {key_env}=your-key to .env.")
            all_ok = False

    # --- Ollama server reachability (only matters if BRAIN = "ollama") ---
    if config.BRAIN == "ollama":
        try:
            import ollama
            client = ollama.Client(host=config.OLLAMA_URL)
            models = client.list()
            names = [m.get("model", m.get("name", "?")) for m in models.get("models", [])]
            print(f"[OK]   Ollama is running at {config.OLLAMA_URL}")
            if any(config.OLLAMA_MODEL in n for n in names):
                print(f"[OK]   Model '{config.OLLAMA_MODEL}' is downloaded")
            else:
                print(f"[FAIL] Model '{config.OLLAMA_MODEL}' is not downloaded yet.")
                print(f"       Fix: run  ollama pull {config.OLLAMA_MODEL}")
                all_ok = False
        except ImportError:
            pass  # already reported above
        except Exception as e:
            print(f"[FAIL] Couldn't reach Ollama at {config.OLLAMA_URL}: {e}")
            print("       Fix: install Ollama from https://ollama.com and make sure it's running.")
            all_ok = False

    print("=" * 40)
    if all_ok:
        print("Everything looks good. Run 'python main.py' to start Jarvis.")
    else:
        print("Some checks failed — fix the items above, then run 'python main.py --check' again.")
    return all_ok


def main():
    background = "--background" in sys.argv
    if "--check" in sys.argv:
        ok = run_check()
        sys.exit(0 if ok else 1)

    # Must happen before ANY subsystem starts — this is the actual fix for
    # duplicate wake-word/hotkey/GUC-scheduler threads piling up: a second
    # `python main.py` used to get this far and start everything a second
    # time even though the web server below would fail to bind.
    from jarvis import singleton
    if not singleton.acquire():
        print("Jarvis is already running — see http://127.0.0.1:8000")
        sys.exit(0)

    launch_cmd = [sys.executable] + sys.argv

    from jarvis import config
    from jarvis.agent import Agent
    from jarvis.errors import user_safe_error

    # Optional web server module — imported here (before Agent, which is
    # slow) so we can fail fast on a port conflict. Missing fastapi/uvicorn
    # is a separate, pre-existing degrade-to-console-only case, not an error.
    web_server_module = None
    try:
        from jarvis.web import server as web_server_module
    except Exception:
        pass

    if web_server_module is not None:
        import socket
        port_probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            port_probe.bind((web_server_module.HOST, web_server_module.PORT))
        except OSError as e:
            port_probe.close()
            print(f"Jarvis couldn't start: port {web_server_module.PORT} is "
                  f"already in use ({e}).")
            print("Another Jarvis process may still be running or shutting "
                  "down — check Task Manager, or wait a moment and retry.")
            sys.exit(1)
        port_probe.close()

    print("Jarvis is starting…")
    try:
        agent = Agent()
    except Exception as e:
        print(f"\nJarvis couldn't start: {e}")
        print("\nRun 'python main.py --check' to diagnose the problem.")
        sys.exit(1)

    agent_lock = threading.Lock()

    # --- web interface (optional, degrades gracefully) ---
    # Runs in a daemon background thread wrapping the SAME agent, so the
    # browser, console, and voice all share one conversation.
    web_server = None
    if web_server_module is not None:
        try:
            threading.Thread(
                target=web_server_module.run_server,
                args=(agent, agent_lock),
                daemon=True,
                name="jarvis-web",
            ).start()
            web_server = web_server_module
            print(f"Web interface: {web_server_module.URL}")
        except Exception as e:
            print(f"Web interface unavailable ({e}). Console chat still works.")
            print("Fix: pip install -r requirements.txt")
    else:
        print("Web interface unavailable (fastapi/uvicorn not installed). "
              "Console chat still works.")
        print("Fix: pip install -r requirements.txt")

    # --- voice (optional, degrades gracefully) ---
    voice = None
    if config.VOICE_ENABLED and "--no-voice" not in sys.argv:
        try:
            from jarvis.voice.controller import VoiceController
            voice = VoiceController(agent, agent_lock)
            if web_server is not None:
                # "hey jarvis" / hotkey now opens the web UI (see WAKE_OPENS_WEB
                # in jarvis/config.py to restore the old talk-out-loud behavior)
                voice.open_web = web_server.open_page
            voice.start()
        except Exception as e:
            print(f"Voice features unavailable ({e}). Text chat still works.")
            voice = None

    # --- reminders (toast-only when voice is disabled/unavailable) ---
    try:
        from jarvis.reminders import start_reminder_thread
        speak_fn = (voice.speaker.speak
                    if voice and voice.speaker and voice.speaker.available else None)
        start_reminder_thread(speak_fn=speak_fn)
    except Exception as e:
        print(f"Reminder service unavailable ({e}).")

    # --- GUC deadline checks (optional, degrades gracefully) ---
    try:
        from jarvis.guc import scheduler as guc_scheduler
        from jarvis.guc import auth as guc_auth
        if guc_auth.credentials_configured():
            guc_scheduler.start_background_loop()
            print("GUC background checks: enabled (every ~45 min)")
        else:
            print("GUC background checks: disabled (GUC_USERNAME/GUC_PASSWORD not set in .env)")
    except Exception as e:
        print(f"GUC background checks unavailable ({e}).")

    # --- sentence streamer: speaks streamed sentences while the model keeps
    #     generating (only used when voice output is available) ---
    streamer = None
    if voice and voice.speaker and voice.speaker.available and config.STREAMING:
        try:
            from jarvis.voice.tts import SentenceStreamer
            streamer = SentenceStreamer(voice.speaker)
            voice.streamer = streamer  # voice commands reuse the same queue
        except Exception as e:
            print(f"(speak-while-generating unavailable: {e})")

    try:
        from jarvis import briefing as briefing_module, session_log
        if not session_log.current_session_materialized():
            text = briefing_module.build_briefing()
            if text and not session_log.current_session_materialized():
                print(f"Jarvis: {text}")
                session_log.log_event("assistant_reply", text=text)
                if voice and voice.speaker and voice.speaker.available:
                    voice.speak_reply(text)
    except Exception as e:
        print(f"(briefing unavailable: {e})")

    # --- system tray (optional, degrades gracefully) ---
    if getattr(config, "TRAY_ICON_ENABLED", True):
        try:
            from jarvis.tray import start_tray_icon
            if not start_tray_icon(launch_cmd):
                print("Tray icon unavailable (pystray/Pillow not usable) — "
                      "Jarvis keeps running without one.")
        except Exception as e:
            print(f"Tray icon unavailable ({e}) — Jarvis keeps running without one.")

    if background:
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass
        finally:
            if voice:
                voice.stop()
        return

    # --- text chat loop ---
    print("\nType your message and press Enter. "
          "Type 'new' for a fresh session, 'sessions' to list history, "
          "'resume <id>' to continue one, or 'exit' to stop.\n")
    try:
        while True:
            try:
                # strip whitespace and any stray byte-order mark from piped input
                user_text = input("You: ").strip().lstrip("﻿").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye.")
                break

            if not user_text:
                continue
            if user_text.lower() in ("exit", "quit"):
                print("Goodbye.")
                break
            if user_text.lower() == "new":
                with agent_lock:
                    agent.reset()
                print("Started a new session — conversation cleared.\n")
                continue
            if user_text.lower() == "sessions":
                try:
                    index_path = Path(__file__).resolve().parent / "sessions" / "index.json"
                    data = json.loads(index_path.read_text(encoding="utf-8"))
                    entries = data.get("sessions", [])
                    if not isinstance(entries, list):
                        entries = []
                except Exception:
                    entries = []
                if not entries:
                    print("No logged sessions found.\n")
                else:
                    for entry in reversed(entries[-10:]):
                        print(f"{entry.get('id', '?')}  {entry.get('start', '?')}  "
                              f"{int(entry.get('message_count') or 0)} msgs  "
                              f"{entry.get('summary') or '(no messages)'}")
                    print()
                continue
            if user_text.lower().startswith("resume "):
                session_id = user_text[7:].strip()
                with agent_lock:
                    resumed = agent.resume(session_id)
                if resumed:
                    print(f"Resumed session {session_id} — conversation reloaded.\n")
                else:
                    print(f"No session found with id '{session_id}'.\n")
                continue

            # count sentences handed to the speaker so we know whether the
            # reply was already spoken while streaming
            spoken = {"count": 0}

            def say_sentence(s):
                spoken["count"] += 1
                streamer.feed(s)

            on_sentence = say_sentence if streamer else None
            if on_sentence and voice and voice.wakeword:
                voice.wakeword.pause()  # don't let Jarvis hear itself

            try:
                with agent_lock:
                    reply = agent.send(user_text, on_sentence=on_sentence)
            except Exception as e:
                print(f"Jarvis hit a problem answering that: {user_safe_error(e)}")
                print("(if this keeps happening, run 'python main.py --check')\n")
                continue
            finally:
                if on_sentence and voice and voice.wakeword and spoken["count"] == 0:
                    voice.wakeword.resume()

            if not agent.last_streamed:
                print(f"Jarvis: {reply}\n")

            if spoken["count"]:
                streamer.wait()          # let it finish talking
                if voice and voice.wakeword:
                    voice.wakeword.resume()
            elif voice:
                voice.speak_reply(reply)  # non-streamed reply: speak it whole
    finally:
        if voice:
            voice.stop()


if __name__ == "__main__":
    main()
