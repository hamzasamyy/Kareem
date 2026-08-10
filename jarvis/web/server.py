"""
Jarvis web server — FastAPI + one WebSocket, wrapping the EXISTING engine.

This file does not contain any AI logic. It only:
  1. serves the static page (jarvis/web/static/) at http://127.0.0.1:8000
  2. accepts a WebSocket at /ws and relays messages to/from the shared Agent
  3. routes the safety gate's confirmation question to the browser and BLOCKS
     the tool until the user clicks Confirm or Cancel

SECURITY MODEL: the server binds to 127.0.0.1 ONLY (see run_server), so it is
reachable exclusively from this PC. Never change the host to 0.0.0.0.

WebSocket protocol (JSON messages):
  browser -> server:
    raw binary audio                                    one complete voice utterance
    {"type": "user_message", "text": "...", "images": [...]} chat; images are optional data URLs
    {"type": "new_session"}                            clear history and rotate the log
    {"type": "resume_session", "id": "<8hex>"}       reopen a logged conversation
    {"type": "confirm_response", "approved": bool}   Confirm/Cancel click
    {"type": "shutdown_request"}                       deliberately stop Jarvis
  server -> browser:
    {"type": "hello", "brain": "...", "model": "...", "safety": "...", "stt_source": "...", "briefing": "..." (optional)}
    {"type": "stt_result", "text": "...", "error": "..." (optional)}
    {"type": "token",      "text": "..."}            next chunk of the streaming reply
    {"type": "reply_done", "text": "..."}            full final reply (always sent)
    {"type": "session_reset"}                          fresh conversation is ready
    {"type": "session_resumed", "events": [...]}      old conversation is now live
    {"type": "tool_start", "name": "...", "detail": "..."}
    {"type": "tool_end",   "name": "...", "detail": "...", "ok": bool}
    {"type": "confirmation_required", "description": "..."}   risky action paused
    {"type": "confirmation_resolved", "approved": bool, "timed_out": bool}
    {"type": "navigate_app", "section": "..."}       the navigate_app tool asking this
                                                       tab to switch screens (see
                                                       jarvis/web/bridge.py)
    {"type": "shutting_down"}                         shutdown request accepted
    {"type": "error",      "message": "..."}         plain-English problem report

REST routes:
  GET  /api/guc/status                         GUC status and dashboard data
  POST /api/guc/check                          start a background check cycle
  POST /api/guc/review/{item_id}/approve       approve a review item
  POST /api/guc/review/{item_id}/dismiss       dismiss a review item
  POST /api/guc/item/{item_id}/seen             set an item's seen state (rejected for finance items)
  POST /api/guc/item/{item_id}/completed        set an item's completed state (rejected for finance items)
  POST /api/guc/bulk/seen                       mark a section/all sections seen (finance items skipped)
  POST /api/guc/bulk/completed                  complete a section/all sections (finance/announcements skipped)

There is deliberately no delete route or WS message for University items —
Completing an item moves it to the Completed tab instead of deleting it
(see jarvis/guc/sync.py's section_for_item()), and Finance items are
fully read-only. The general (non-University) safety confirmation gate
in jarvis/safety.py is untouched by this.
"""

import asyncio
import io
import json
import re
import shutil
import subprocess
import threading
import wave
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jarvis.guc import (auth as guc_auth, dedupe as guc_dedupe,
                        scheduler as guc_scheduler, sync as guc_sync)
from jarvis.web import bridge

STATIC_DIR = Path(__file__).resolve().parent / "static"

HOST = "127.0.0.1"  # localhost only — this is the whole security model
PORT = 8000
URL = f"http://{HOST}:{PORT}"

# "Localhost only" stops a REMOTE attacker, but not a malicious page open in
# another tab of the user's OWN browser — browsers happily let any page open
# a WebSocket or send a fetch() to 127.0.0.1, and the WS side can drive the
# full agent (including run_command/read_file/delete_file, gated only by a
# confirmation click). Browsers always set an Origin header on WS upgrades
# and on state-changing fetch()/form requests, so rejecting a PRESENT origin
# that isn't Jarvis's own page closes that hole without needing a token.
# A MISSING origin is allowed through (non-browser clients — e.g. this
# module's own __main__ test mode, or a future CLI — can't mount a
# cross-site attack because they can't be triggered by visiting a webpage).
_ALLOWED_ORIGINS = {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}


def _origin_is_allowed(origin) -> bool:
    return origin is None or origin in _ALLOWED_ORIGINS

# How many browser pages are connected right now (updated by the WebSocket
# endpoint). Lets the wake word focus an already-open tab instead of piling
# up new ones.
_page_clients = 0


def _transcribe_backend(audio_bytes: bytes) -> str:
    """Decode one browser MediaRecorder blob and use desktop Jarvis's STT."""
    import numpy as np
    from jarvis.voice import stt as voice_stt

    result = subprocess.run(
        ["ffmpeg", "-i", "pipe:0", "-f", "wav", "-ar", "16000", "-ac", "1",
         "-loglevel", "error", "pipe:1"],
        input=audio_bytes, capture_output=True, timeout=30,
    )
    if result.returncode != 0 or not result.stdout:
        detail = (result.stderr or b"ffmpeg failed").decode(errors="replace")[:300]
        raise RuntimeError(detail)

    with wave.open(io.BytesIO(result.stdout), "rb") as wav_file:
        frames = wav_file.readframes(wav_file.getnframes())
    audio = np.frombuffer(frames, dtype=np.int16)
    return voice_stt.transcribe(audio)


def page_is_open() -> bool:
    """True if the web UI is currently open in some browser tab."""
    return _page_clients > 0


def open_page():
    """Open the web UI in the default browser — or, if a page is already
    connected, just bring the browser window to the front. Called when the
    wake word or hotkey fires."""
    if page_is_open():
        try:
            import pygetwindow
            for window in pygetwindow.getWindowsWithTitle("Jarvis"):
                # browser tab titles start with the page title ("Jarvis - Chrome")
                if window.title.startswith("Jarvis"):
                    window.activate()
                    return
        except Exception:
            pass  # focusing is best-effort; fall through to opening
    import webbrowser
    webbrowser.open(URL)

# If the user never clicks Confirm/Cancel, give up after this long and treat
# the action as declined, so a forgotten tab can't leave a tool blocked forever.
CONFIRM_TIMEOUT_SECONDS = 300
MAX_MESSAGE_IMAGES = 4
MAX_IMAGE_BYTES = 15 * 1024 * 1024


def _validate_images(value) -> list[str]:
    """Validate browser image data URLs and enforce inexpensive size caps."""
    import base64

    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("Attached images must be sent as a list.")
    if len(value) > MAX_MESSAGE_IMAGES:
        raise ValueError(f"Attach at most {MAX_MESSAGE_IMAGES} images per message.")
    total = 0
    for image in value:
        if not isinstance(image, str) or not image.startswith("data:image/"):
            raise ValueError("Every attachment must be a valid image data URL.")
        if ";base64," not in image:
            raise ValueError("Every attached image must use base64 encoding.")
        try:
            total += len(base64.b64decode(image.split(";base64,", 1)[1], validate=True))
        except Exception as exc:
            raise ValueError("An attached image contains invalid base64 data.") from exc
        if total > MAX_IMAGE_BYTES:
            raise ValueError("Attached images exceed the 15 MB total limit.")
    return value


def create_app(agent, agent_lock: threading.Lock) -> FastAPI:
    """Build the FastAPI app around an existing Agent instance.

    agent/agent_lock are shared with the console + voice loops, so the web UI
    and the desktop app talk to the SAME conversation and never overlap turns.
    """
    from jarvis import briefing, config, memory, safety, session_log, trackers

    effective_stt_source = "browser"
    if getattr(config, "WEB_STT_SOURCE", "browser") == "backend":
        try:
            if not shutil.which("ffmpeg"):
                raise RuntimeError("ffmpeg was not found on PATH")
            from jarvis.voice import stt as voice_stt
            voice_stt.get_model()
            effective_stt_source = "backend"
        except Exception as e:
            print(f"(web voice note: backend speech recognition is unavailable: {e}; "
                  "using browser speech recognition instead.)")

    app = FastAPI(title="Jarvis", docs_url=None, redoc_url=None)

    # Reject cross-origin state-changing requests (a malicious page's hidden
    # form or fetch() POSTing to this API) — see _ALLOWED_ORIGINS above.
    # GET is left unchecked: it can't be a fetch()-based cross-site WRITE,
    # and blocking it would also break plain browser navigation.
    @app.middleware("http")
    async def check_origin(request, call_next):
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if not _origin_is_allowed(request.headers.get("origin")):
                return JSONResponse({"error": "Origin not allowed"}, status_code=403)
        return await call_next(request)

    # Localhost app, tiny files: tell the browser to always revalidate instead
    # of caching. Otherwise a browser can keep running last week's app.js after
    # an update and buttons silently do nothing.
    @app.middleware("http")
    async def no_stale_cache(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-cache"
        return response

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/sessions")
    async def sessions():
        """Return the tolerant on-disk index, newest session first."""
        try:
            index_path = Path(__file__).resolve().parent.parent.parent / "sessions" / "index.json"
            data = json.loads(index_path.read_text(encoding="utf-8"))
            entries = data.get("sessions", [])
            if not isinstance(entries, list):
                entries = []
        except Exception:
            entries = []
        return {"sessions": list(reversed(entries)),
                "current": session_log.current_session_id()}

    @app.get("/api/sessions/search")
    async def search_sessions_endpoint(q: str = ""):
        return {"results": session_log.search_sessions(q)}

    @app.get("/api/sessions/{session_id}")
    async def session_events(session_id: str):
        """Return one read-only transcript; unknown ids are simply empty."""
        return {"events": session_log.read_session_events(session_id)}

    @app.delete("/api/sessions/{session_id}")
    async def delete_session_endpoint(session_id: str):
        """Permanently remove one inactive logged session, if it is safe."""
        ok = session_log.delete_session(session_id)
        return {"deleted": ok}

    @app.get("/api/trackers")
    async def tracker_items(category: str = "", status: str = ""):
        """Return personal tracker items with optional category/status filters."""
        return {"items": trackers.list_items(category or None, status or None)}

    @app.post("/api/trackers")
    async def add_tracker_item(body: dict):
        """Add a personal tracker item; unclear due times are rejected."""
        due = body.get("due")
        due_iso = trackers.parse_due(due) if due else None
        if due and due_iso is None:
            return {"item": None}
        item = trackers.add_item(
            body.get("category"), body.get("text"), due_iso,
            body.get("tags"), body.get("list_name"),
        )
        return {"item": item}

    @app.post("/api/trackers/{item_id}/complete")
    async def complete_tracker_item(item_id: str):
        return {"ok": trackers.complete_item(item_id)}

    @app.delete("/api/trackers/{item_id}")
    async def delete_tracker_item(item_id: str):
        return {"ok": trackers.remove_item(item_id)}

    @app.get("/api/memory")
    async def memory_facts():
        return {"facts": memory.list_facts()}

    @app.post("/api/memory")
    async def add_memory_fact(body: dict):
        fact = memory.add_fact(body.get("text"))
        return {"fact": fact}

    @app.delete("/api/memory/{fact_id}")
    async def delete_memory_fact(fact_id: str):
        return {"ok": memory.remove_fact(fact_id)}

    @app.get("/api/guc/status")
    async def guc_status():
        status = guc_scheduler.get_status()
        courses = {}
        needs_review = []
        sections = {
            section: {"needs_review": [], "items": []}
            for section in guc_sync.SECTIONS
        }
        unclassified = {"needs_review": [], "items": []}
        # "Tracked" items (rendered with Seen/Completed controls where
        # applicable, per section_for_item()'s routing) are everything NOT
        # still pending triage — auto_added, finance, AND announcement
        # items all land here. Only "needs_review" gets the separate
        # Approve/Dismiss bucket. Keeping this as an explicit membership
        # check (not an if/elif per tag) means adding a future tag type
        # can't silently fall through and vanish the way "announcement"
        # once did here.
        TRACKED_TAGS = ("auto_added", "finance", "announcement")
        for item in guc_dedupe.get_existing_university_items():
            tags = item.get("tags", [])
            course = (item.get("text") or "").split(" — ", 1)[0]
            shown = {key: item.get(key) for key in ("id", "text", "due", "tags")}
            section = guc_sync.section_for_item(item)
            if any(tag in tags for tag in TRACKED_TAGS):
                if "auto_added" in tags:
                    courses.setdefault(course, []).append(shown)
                tracked_item = {
                    key: item.get(key)
                    for key in ("id", "text", "due", "status", "completed_at", "tags")
                } | {
                    "seen": item.get("seen", False),
                    # Only meaningful once an item is routed into the
                    # Completed tab (section != its original category) —
                    # lets the UI label which tab it came from.
                    "category": guc_sync._category_for_item(item),
                }
                (sections[section] if section in sections else unclassified)["items"].append(tracked_item)
            elif "needs_review" in tags:
                review_item = {
                    "id": item.get("id"), "course": course,
                    "text": item.get("text"), "due": item.get("due"),
                }
                needs_review.append(review_item)
                (sections[section] if section in sections else unclassified)["needs_review"].append({
                    key: review_item[key] for key in ("id", "text", "due")
                })
        return {
            "credentials_configured": guc_auth.credentials_configured(),
            "last_checked": status.get("last_checked"),
            "in_progress": status.get("in_progress", False),
            "login_status": status.get("login_status", {}),
            "login_errors": status.get("login_errors", {}),
            "history": status.get("history", []),
            "courses": [{"course": course, "items": items} for course, items in courses.items()],
            "needs_review": needs_review,
            "sections": sections,
            "unclassified": unclassified,
        }

    @app.post("/api/guc/check")
    async def guc_check():
        if guc_scheduler.get_status().get("in_progress"):
            return {"started": False, "reason": "already in progress"}
        threading.Thread(
            target=guc_scheduler.run_check_cycle, daemon=True, name="jarvis-guc-manual-check"
        ).start()
        return {"started": True}

    @app.post("/api/guc/review/{item_id}/approve")
    async def approve_guc_review(item_id: str):
        return guc_sync.approve_review_item(item_id)

    @app.post("/api/guc/review/{item_id}/dismiss")
    async def dismiss_guc_review(item_id: str):
        return guc_sync.dismiss_review_item(item_id)

    def _is_guc_finance_item(item_id: str) -> bool:
        item = next(
            (i for i in guc_dedupe.get_existing_university_items() if i.get("id") == item_id),
            None,
        )
        return bool(item) and "finance" in item.get("tags", [])

    def _is_guc_announcement_item(item_id: str) -> bool:
        item = next(
            (i for i in guc_dedupe.get_existing_university_items() if i.get("id") == item_id),
            None,
        )
        return bool(item) and "announcement" in item.get("tags", [])

    @app.post("/api/guc/item/{item_id}/seen")
    async def set_guc_item_seen(item_id: str, body: dict):
        # Finance items are fully read-only: their displayed status only
        # ever changes via the automatic background re-scrape, never a
        # manual toggle (see jarvis/guc/sync.py's sync_finances).
        if _is_guc_finance_item(item_id):
            return {"ok": False, "error": "finance items are read-only"}
        return {"ok": trackers.mark_seen(item_id, bool(body.get("seen", True)))}

    @app.post("/api/guc/item/{item_id}/completed")
    async def set_guc_item_completed(item_id: str, body: dict):
        if _is_guc_finance_item(item_id):
            return {"ok": False, "error": "finance items are read-only"}
        if _is_guc_announcement_item(item_id):
            return {"ok": False, "error": "announcement items cannot be completed"}
        return {"ok": trackers.set_completed(item_id, bool(body.get("completed", True)))}

    @app.post("/api/guc/bulk/seen")
    async def mark_guc_bulk_seen(body: dict):
        return guc_sync.bulk_mark_seen(body.get("scope", ""))

    @app.post("/api/guc/bulk/completed")
    async def set_guc_bulk_completed(body: dict):
        return guc_sync.bulk_set_completed(body.get("scope", ""))

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        global _page_clients
        if not _origin_is_allowed(ws.headers.get("origin")):
            # Close during the handshake, before accept() — this is the tool-
            # execution channel (run_command/read_file/delete_file and every
            # other tool), so a mismatched Origin (a page other than Jarvis's
            # own) must never even reach the point of relaying messages.
            await ws.close(code=1008)
            return
        await ws.accept()
        _page_clients += 1
        loop = asyncio.get_running_loop()

        # Everything the engine wants to tell the browser goes through this
        # queue; a single sender task owns the socket's outbound side.
        outbox: asyncio.Queue = asyncio.Queue()

        def push(message: dict):
            """Thread-safe: queue a message for the browser. Callable from the
            worker thread that runs the (blocking) agent."""
            loop.call_soon_threadsafe(outbox.put_nowait, message)

        async def sender():
            while True:
                await ws.send_json(await outbox.get())

        sender_task = asyncio.create_task(sender())

        # Per-connection state. busy: a turn is running. pending: the
        # confirmation the engine is currently blocked on, if any.
        session = {"busy": False, "pending": None}

        def web_ask(prompt: str) -> str:
            """Replaces the console's y/n input for turns started from the
            browser. Runs on the worker thread INSIDE the safety gate: sends
            the question to the page, then blocks until the user clicks."""
            # confirm() builds: "Jarvis wants to: {description}\nProceed? (y/n): "
            # — recover the bare description for a clean card in the UI.
            m = re.match(r"Jarvis wants to: (.*)\nProceed", prompt, re.DOTALL)
            description = m.group(1) if m else prompt

            pending = {"event": threading.Event(), "approved": False}
            session["pending"] = pending
            push({"type": "confirmation_required", "description": description})

            answered = pending["event"].wait(timeout=CONFIRM_TIMEOUT_SECONDS)
            session["pending"] = None
            push({
                "type": "confirmation_resolved",
                "approved": pending["approved"],
                "timed_out": not answered,
            })
            return "y" if (answered and pending["approved"]) else "n"

        def run_turn(text: str, images: list[str]):
            """Worker thread: one full user turn through the shared engine."""
            def on_token(chunk):
                push({"type": "token", "text": chunk})

            def on_tool(event):
                push({
                    "type": "tool_start" if event["phase"] == "start" else "tool_end",
                    "name": event["name"],
                    "detail": event["detail"],
                    "ok": event["ok"],
                })

            try:
                with agent_lock:  # one turn at a time, shared with console/voice
                    # Route safety confirmations AND UI-navigation pushes to
                    # this browser tab for THIS turn only; whatever was
                    # installed before (console input, voice; no navigate
                    # target at all) is restored afterwards.
                    previous_ask = safety.set_ask_fn(web_ask)
                    previous_push = bridge.set_push_fn(push)
                    try:
                        reply = agent.send(
                            text, images=images, on_token=on_token, on_tool=on_tool
                        )
                    finally:
                        safety.set_ask_fn(previous_ask)
                        bridge.set_push_fn(previous_push)
                push({"type": "reply_done", "text": reply})
            except Exception as e:
                push({
                    "type": "error",
                    "message": f"Jarvis hit a problem answering that: {e}",
                })
            finally:
                session["busy"] = False

        def reset_session():
            """Worker thread: wait for shared access, then reset quickly."""
            try:
                with agent_lock:
                    agent.reset()
                push({"type": "session_reset"})
            except Exception as e:
                push({
                    "type": "error",
                    "message": f"Jarvis couldn't start a new session: {e}",
                })
            finally:
                session["busy"] = False

        def transcribe_and_continue(audio_bytes: bytes):
            """Worker thread: transcribe audio, then release this connection."""
            try:
                text = _transcribe_backend(audio_bytes)
            except Exception as e:
                push({"type": "stt_result", "text": "",
                      "error": f"Speech recognition failed: {e}. "
                               "Using browser speech recognition instead.",
                      "stt_source": "browser"})
                session["busy"] = False
                return
            push({"type": "stt_result", "text": text})
            session["busy"] = False

        def resume(session_id):
            """Worker thread: reopen one transcript under shared agent access."""
            try:
                with agent_lock:
                    ok = agent.resume(session_id)
                if ok:
                    push({"type": "session_resumed",
                          "events": session_log.read_session_events(session_id)})
                else:
                    push({"type": "error",
                          "message": "That session could not be found or resumed."})
            except Exception as e:
                push({"type": "error",
                      "message": f"Jarvis couldn't resume that session: {e}"})
            finally:
                session["busy"] = False

        # Tell the page which brain is active so it can show it in the UI.
        model = {
            "hosted": config.HOSTED_MODEL,
            "ollama": config.OLLAMA_MODEL,
            "claude": config.CLAUDE_MODEL,
        }.get(config.BRAIN, "?")
        briefing_text = None
        if not session_log.current_session_materialized():
            briefing_text = briefing.build_briefing()
            if briefing_text and not session_log.current_session_materialized():
                session_log.log_event("assistant_reply", text=briefing_text)
            else:
                briefing_text = None
        from jarvis.voice.tts import get_active_tts_engine

        push({
            "type": "hello",
            "brain": config.BRAIN,
            "model": model,
            "safety": config.SAFETY_LEVEL,
            "stt_source": effective_stt_source,
            "tts_engine": get_active_tts_engine(),
            "briefing": briefing_text,
        })

        try:
            while True:
                raw = await ws.receive()
                if raw.get("type") == "websocket.disconnect":
                    break
                if "bytes" in raw and raw["bytes"] is not None:
                    if effective_stt_source != "backend":
                        push({"type": "stt_result", "text": "",
                              "error": "Backend speech recognition is unavailable."})
                        continue
                    audio_bytes = raw["bytes"]
                    if session["busy"]:
                        push({
                            "type": "error",
                            "message": "Still working on the previous message — "
                                       "wait for it to finish.",
                        })
                        continue
                    session["busy"] = True
                    threading.Thread(target=transcribe_and_continue,
                                     args=(audio_bytes,), daemon=True).start()
                    continue
                if "text" not in raw or raw["text"] is None:
                    continue
                data = json.loads(raw["text"])
                kind = data.get("type")

                if kind == "user_message":
                    text = (data.get("text") or "").strip()
                    try:
                        images = _validate_images(data.get("images"))
                    except ValueError as e:
                        push({"type": "error", "message": str(e)})
                        continue
                    if not text and not images:
                        continue
                    if session["busy"]:
                        push({
                            "type": "error",
                            "message": "Still working on the previous message — "
                                       "wait for it to finish.",
                        })
                        continue
                    session["busy"] = True
                    threading.Thread(
                        target=run_turn, args=(text, images), daemon=True
                    ).start()

                elif kind == "new_session":
                    if session["busy"]:
                        push({
                            "type": "error",
                            "message": "Still working on the previous message — "
                                       "wait for it to finish.",
                        })
                        continue
                    session["busy"] = True
                    threading.Thread(target=reset_session, daemon=True).start()

                elif kind == "resume_session":
                    if session["busy"]:
                        push({
                            "type": "error",
                            "message": "Still working on the previous message — "
                                       "wait for it to finish.",
                        })
                        continue
                    session["busy"] = True
                    threading.Thread(target=resume, args=(data.get("id"),),
                                     daemon=True).start()

                elif kind == "confirm_response":
                    pending = session["pending"]
                    if pending is not None:
                        pending["approved"] = bool(data.get("approved"))
                        pending["event"].set()

                elif kind == "shutdown_request":
                    # Confirm through the normal outbound queue first. The
                    # brief delay gives its single sender time to flush before
                    # os._exit terminates every thread in this process.
                    push({"type": "shutting_down"})

                    def do_shutdown():
                        import os
                        import time

                        time.sleep(0.3)
                        session_log.close_session()
                        os._exit(0)

                    threading.Thread(target=do_shutdown, daemon=True).start()

        except WebSocketDisconnect:
            pass  # browser tab closed/refreshed
        finally:
            _page_clients -= 1
            # Never leave the engine blocked on a question nobody can answer:
            # a vanished browser counts as "declined".
            pending = session["pending"]
            if pending is not None:
                pending["event"].set()
            sender_task.cancel()

    return app


def run_server(agent, agent_lock: threading.Lock, host: str = HOST, port: int = PORT):
    """Run the web server (blocking). Call from a background thread to keep
    the console app usable at the same time."""
    import uvicorn

    uvicorn.run(create_app(agent, agent_lock), host=host, port=port,
                log_level="warning")


if __name__ == "__main__":
    # Standalone test mode: run just the web server without the voice/console
    # app —  python -m jarvis.web.server  (from the Jarvis folder)
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    from jarvis.agent import Agent

    print("Starting Jarvis web server (standalone test mode)…")
    try:
        standalone_agent = Agent()
    except Exception as e:
        print(f"\nJarvis couldn't start: {e}")
        print("Run 'python main.py --check' to diagnose the problem.")
        sys.exit(1)

    print(f"Open http://{HOST}:{PORT} in your browser. Press Ctrl+C to stop.")
    run_server(standalone_agent, threading.Lock())
