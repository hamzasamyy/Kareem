"""Per-launch conversation logging for Kareem.

Callers use :func:`log_event` with these event shapes:

* ``user_message``: ``text``
* ``assistant_reply``: ``text``
* ``tool_call``: ``name``, ``args`` (a dict)
* ``tool_result``: ``name``, ``result`` (a string), ``ok`` (a bool)
* ``confirmation_asked``: ``action``, ``description``
* ``confirmation_answered``: ``action``, ``description``, ``approved``, ``source``
* ``session_start``: ``brain``, ``model``
* ``session_end``: no extra fields
* ``session_resumed``: no extra fields

Public lifecycle helpers include :func:`start_session`, :func:`rotate_session`,
:func:`resume_session`, and :func:`close_session`.

This module deliberately depends only on the standard library. Logging is a
best-effort subsystem: no disk, encoding, or serialization problem is ever
allowed to interrupt a Kareem conversation.
"""

import atexit
import json
import os
import shutil
import stat
import threading
import uuid
from datetime import datetime
from pathlib import Path


_lock = threading.Lock()
_started = False
_enabled = False
_ended = False
_session_id = ""
_folder_name = ""
_session_dir = None
_start_iso = ""
_message_count = 0
_summary = ""
_atexit_registered = False
_materialized = False
_brain = ""
_model = ""


def _now():
    """Return local time in both machine-readable and transcript forms."""
    now = datetime.now().astimezone()
    return now.isoformat(timespec="seconds"), now.strftime("%Y-%m-%d %H:%M:%S")


def _brain_and_model():
    """Read configuration lazily so importing this module cannot make a cycle."""
    from kareem import config

    brain = config.BRAIN
    models = {
        "hosted": config.HOSTED_MODEL,
        "ollama": config.OLLAMA_MODEL,
        "claude": config.CLAUDE_MODEL,
    }
    return brain, models.get(brain, "unknown")


def _begin_session_locked() -> None:
    """Prepare a fresh session in memory while the caller holds ``_lock``."""
    global _started, _enabled, _ended, _session_id, _folder_name, _session_dir
    global _start_iso, _message_count, _summary, _atexit_registered
    global _materialized, _brain, _model

    # Reset all in-memory bookkeeping first. If disk setup fails, a later
    # rotation can retry without inheriting counts or paths from an old log.
    _started = True
    _enabled = False
    _ended = False
    _session_id = ""
    _folder_name = ""
    _session_dir = None
    _start_iso = ""
    _message_count = 0
    _summary = ""
    _materialized = False
    _brain = ""
    _model = ""
    try:
        brain, model = _brain_and_model()
        iso, _ = _now()
        session_id = uuid.uuid4().hex[:8]
        folder_name = f"{datetime.fromisoformat(iso).strftime('%Y%m%d_%H%M%S')}_{session_id}"
        sessions_dir = Path(__file__).resolve().parent.parent / "sessions"
        session_dir = sessions_dir / folder_name

        _session_id = session_id
        _folder_name = folder_name
        _session_dir = session_dir
        _start_iso = iso
        _brain = brain
        _model = model
        _enabled = True
        if not _atexit_registered:
            atexit.register(_end_session)
            _atexit_registered = True
    except Exception as e:
        _enabled = False
        print(f"(session logging unavailable: {e})")


def start_session() -> None:
    """Prepare this process's new session. A second call is a no-op."""
    try:
        with _lock:
            if _started:
                return
            _begin_session_locked()
            try:
                _prune_empty_sessions_locked()
            except Exception:
                pass
    except Exception:
        pass


def rotate_session() -> None:
    """Close the current log and begin a fresh session. This never raises."""
    global _ended
    try:
        with _lock:
            if _started and _enabled and _materialized and not _ended:
                iso, _ = _now()
                _write_event_locked("session_end", iso, {})
                _ended = True
            _begin_session_locked()
    except Exception:
        # Conversation reset must work even if optional disk logging cannot.
        pass


def resume_session(session_id: str) -> bool:
    """Reopen an indexed session and append future events to its old log.

    Paths come only from the index and are still checked against the sessions
    folder. Like every public logging operation, this is best-effort and never
    lets a disk or malformed-data problem escape into the conversation.
    """
    global _started, _enabled, _ended, _session_id, _folder_name, _session_dir
    global _start_iso, _message_count, _summary, _materialized
    try:
        if not isinstance(session_id, str) or not __import__("re").fullmatch(
                r"[0-9a-fA-F]{8}", session_id):
            return False

        with _lock:
            sessions_dir = Path(__file__).resolve().parent.parent / "sessions"
            data = json.loads((sessions_dir / "index.json").read_text(encoding="utf-8"))
            entries = data.get("sessions", [])
            if not isinstance(entries, list):
                return False
            entry = next((item for item in entries
                          if isinstance(item, dict) and item.get("id") == session_id), None)
            if entry is None or not isinstance(entry.get("dir"), str):
                return False
            target_dir = (sessions_dir / entry["dir"]).resolve()
            if (target_dir.parent != sessions_dir.resolve()
                    or not target_dir.is_dir()
                    or not (target_dir / "session.jsonl").is_file()):
                return False

            # Reopening the session already owned by this process is a true
            # no-op: do not add duplicate markers or close it accidentally.
            if (_started and _enabled and _materialized and not _ended
                    and _session_id == session_id):
                return True

            if _started and _enabled and _materialized and not _ended:
                iso, _ = _now()
                _write_event_locked("session_end", iso, {})
                _ended = True

            _started = True
            _session_id = session_id
            _folder_name = target_dir.name
            _session_dir = target_dir
            _start_iso = str(entry.get("start") or "")
            _message_count = int(entry.get("message_count") or 0)
            _summary = str(entry.get("summary") or "")
            _materialized = True
            _enabled = True
            _ended = False
            iso, _ = _now()
            _write_event_locked("session_resumed", iso, {})
            return True
    except Exception:
        return False


def log_event(event_type: str, **fields) -> None:
    """Append one structured and human-readable event. This never raises."""
    try:
        with _lock:
            if not _started or not _enabled or _ended:
                return
            if not _materialize_session_locked():
                return
            iso, _ = _now()
            _write_event_locked(event_type, iso, fields)
    except Exception:
        pass


def _write_event_locked(event_type, iso, fields):
    """Write an event while the caller holds ``_lock``."""
    global _message_count, _summary
    clean_fields = dict(fields)
    if event_type == "tool_result":
        result = str(clean_fields.get("result", ""))
        if len(result) > 2000:
            result = result[:2000] + " … [truncated]"
        clean_fields["result"] = result

    event = {"ts": iso, "type": event_type, **clean_fields}
    with (_session_dir / "session.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    rendered = _render_event(event_type, iso, clean_fields)
    if rendered:
        with (_session_dir / "transcript.md").open("a", encoding="utf-8") as handle:
            handle.write(rendered)

    if event_type == "user_message":
        _message_count += 1
        if not _summary:
            _summary = str(clean_fields.get("text", ""))[:80]
    if event_type in {"user_message", "assistant_reply", "session_start", "session_end",
                      "session_resumed"}:
        _update_index_locked(iso, event_type)


def _materialize_session_locked():
    """Create the prepared session's on-disk representation once."""
    global _materialized
    if _materialized:
        return True
    try:
        _session_dir.mkdir(parents=True, exist_ok=False)
        display = datetime.fromisoformat(_start_iso).strftime("%Y-%m-%d %H:%M:%S")
        header = (
            f"# Kareem session {_session_id}\n"
            f"Started: {display} — brain: {_brain} ({_model})\n\n---\n"
        )
        (_session_dir / "transcript.md").write_text(header, encoding="utf-8")
        _materialized = True
        _write_event_locked(
            "session_start", _start_iso, {"brain": _brain, "model": _model}
        )
        return True
    except Exception:
        return False


def _render_event(event_type, iso, fields):
    time = datetime.fromisoformat(iso).strftime("%H:%M:%S")
    if event_type == "user_message":
        return f"\n**You** ({time}): {fields.get('text', '')}\n"
    if event_type == "assistant_reply":
        return f"\n**Kareem** ({time}): {fields.get('text', '')}\n"
    if event_type == "tool_call":
        args = fields.get("args", {})
        parts = ", ".join(f"{key}={value!r}" for key, value in args.items())
        line = f"> tool call: {fields.get('name', '')}({parts})"
        return "\n" + line[:300] + "\n"
    if event_type == "tool_result":
        status = "ok" if fields.get("ok") else "FAILED"
        result = str(fields.get("result", ""))[:300]
        return f"\n> tool result ({fields.get('name', '')}, {status}): {result}\n"
    if event_type == "confirmation_asked":
        return f"\n> [CONFIRM?] {fields.get('description', '')}\n"
    if event_type == "confirmation_answered":
        answer = "APPROVED" if fields.get("approved") else "DECLINED"
        return f"\n> [CONFIRM] {fields.get('description', '')} -> {answer}\n"
    if event_type == "session_end":
        display = datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M:%S")
        return f"\n---\nSession ended: {display}\n"
    if event_type == "session_resumed":
        display = datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M:%S")
        return f"\n---\nSession resumed: {display}\n"
    return ""


def _update_index_locked(iso, event_type):
    sessions_dir = _session_dir.parent
    index_path = sessions_dir / "index.json"
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(data.get("sessions"), list):
            raise ValueError("invalid sessions index")
    except Exception:
        data = {"sessions": []}

    entry = next((item for item in data["sessions"] if item.get("id") == _session_id), None)
    if entry is None:
        entry = {
            "id": _session_id,
            "dir": _folder_name,
            "start": _start_iso,
            "end": None,
            "message_count": 0,
            "summary": "",
        }
        data["sessions"].append(entry)
    entry["message_count"] = _message_count
    entry["summary"] = _summary
    # Message events refresh counts and summary, but only a proper close marks
    # the session as ended. This keeps the current index entry visibly live.
    if event_type == "session_end":
        entry["end"] = iso
    elif event_type == "session_resumed":
        entry["end"] = None

    temp_path = sessions_dir / "index.json.tmp"
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, index_path)


def _clear_readonly(func, path, exc_info):
    """shutil.rmtree onerror hook: some folders (e.g. left behind by a killed
    Kareem process) end up marked read-only on Windows, which blocks deletion
    with no amount of retrying. Clear the bit once and retry the failed op."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except Exception:
        pass


def _safe_rmtree(path) -> bool:
    """Delete a directory, tolerating OneDrive/AV file locks (transient — a
    short retry clears them) and Windows read-only folders (permanent — needs
    the attribute cleared, not just a retry). Returns True only if the folder
    is actually gone afterward — callers must not drop bookkeeping for one
    that's still on disk, or it becomes an untracked orphan forever."""
    import time

    for attempt in range(3):
        shutil.rmtree(path, onerror=_clear_readonly)
        if not path.exists():
            return True
        time.sleep(0.05 * (attempt + 1))
    return not path.exists()


def _jsonl_has_user_message(path):
    """Return conservatively whether a JSONL file contains a user message."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                    if isinstance(event, dict) and event.get("type") == "user_message":
                        return True
                except Exception:
                    pass
    except Exception:
        pass
    return False


def _prune_empty_sessions_locked():
    """Remove legacy empty sessions while the caller holds ``_lock``."""
    sessions_dir = Path(__file__).resolve().parent.parent / "sessions"
    if not sessions_dir.is_dir():
        return 0
    sessions_root = sessions_dir.resolve()
    active_dir = _session_dir.resolve() if _session_dir is not None else None
    index_path = sessions_dir / "index.json"
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
        entries = data.get("sessions", [])
        if not isinstance(entries, list):
            return 0
    except Exception:
        data = {"sessions": []}
        entries = data["sessions"]

    kept = []
    removed = 0
    indexed_dirs = set()
    index_changed = False
    for entry in entries:
        dirname = entry.get("dir") if isinstance(entry, dict) else None
        if isinstance(dirname, str):
            indexed_dirs.add(dirname)
        candidate = (sessions_dir / dirname).resolve() if isinstance(dirname, str) else None
        safe = candidate is not None and candidate.parent == sessions_root
        empty_entry = isinstance(entry, dict) and entry.get("message_count") == 0
        if (safe and candidate != active_dir and empty_entry
                and not _jsonl_has_user_message(candidate / "session.jsonl")
                and _safe_rmtree(candidate)):
            removed += 1
            index_changed = True
        else:
            kept.append(entry)

    for candidate in sessions_dir.iterdir():
        if (not candidate.is_dir() or candidate.name in indexed_dirs
                or candidate.resolve() == active_dir):
            continue
        if (not _jsonl_has_user_message(candidate / "session.jsonl")
                and _safe_rmtree(candidate)):
            removed += 1

    if index_changed:
        data["sessions"] = kept
        temp_path = sessions_dir / "index.json.tmp"
        temp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp_path, index_path)
    return removed


def prune_empty_sessions() -> int:
    """Remove empty indexed and orphaned session folders. This never raises."""
    try:
        with _lock:
            return _prune_empty_sessions_locked()
    except Exception:
        return 0


def delete_session(session_id: str) -> bool:
    """Permanently remove one indexed, inactive session. This never raises.

    The caller supplies an id, never a path. The directory name is recovered
    from the index and checked against the sessions root before anything is
    removed. The index entry is deliberately kept until the folder is known
    to be gone, so a transient Windows/OneDrive lock cannot create an orphan.
    """
    try:
        if not isinstance(session_id, str) or not __import__("re").fullmatch(
                r"[0-9a-fA-F]{8}", session_id):
            return False

        with _lock:
            sessions_dir = Path(__file__).resolve().parent.parent / "sessions"
            index_path = sessions_dir / "index.json"
            data = json.loads(index_path.read_text(encoding="utf-8"))
            entries = data.get("sessions", [])
            if not isinstance(entries, list):
                return False
            entry = next((item for item in entries
                          if isinstance(item, dict) and item.get("id") == session_id), None)
            if entry is None or not isinstance(entry.get("dir"), str):
                return False

            # A prepared live session may not have reached disk yet, but it is
            # still owned by this process and must never be deleted underneath
            # the writer. Starting a new session is the safe way to move on.
            if _started and _enabled and session_id == _session_id:
                return False

            target_dir = (sessions_dir / entry["dir"]).resolve()
            if target_dir.parent != sessions_dir.resolve() or not target_dir.is_dir():
                return False
            if not _safe_rmtree(target_dir) or target_dir.exists():
                return False

            data["sessions"] = [item for item in entries if item is not entry]
            temp_path = sessions_dir / "index.json.tmp"
            temp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(temp_path, index_path)
            return True
    except Exception:
        return False


def _end_session():
    """Best-effort atexit callback; safe if shutdown has partially begun."""
    global _ended
    try:
        with _lock:
            if not _enabled or not _materialized or _ended:
                return
            iso, _ = _now()
            _write_event_locked("session_end", iso, {})
            _ended = True
    except Exception:
        pass


def close_session() -> None:
    """Explicitly close the current session, with the atexit handler's effect.

    Call this before a deliberate process exit where atexit cannot be trusted
    to run (notably ``os._exit`` on Windows). ``_end_session`` is guarded and
    idempotent, so an ordinary later shutdown remains safe.
    """
    _end_session()


def current_session_id() -> str:
    """Return this process's active logging id, or an empty string.

    The lock keeps this consistent with ``start_session`` if a web request
    arrives while logging is being initialised.
    """
    try:
        with _lock:
            return _session_id if (_started and _enabled) else ""
    except Exception:
        return ""


def current_session_materialized() -> bool:
    """Return whether the active session has at least one logged event."""
    try:
        with _lock:
            return bool(_materialized) if (_started and _enabled) else False
    except Exception:
        return False


def read_session_events(session_id: str) -> list[dict]:
    """Read a session's JSONL events without trusting the supplied id as a path.

    Session ids are looked up in the index, and even the indexed directory is
    checked to ensure it remains beneath ``sessions``. Logging is best-effort,
    so a missing file, damaged index, or malformed JSON line never escapes.
    """
    try:
        if not isinstance(session_id, str) or not __import__("re").fullmatch(
                r"[0-9a-fA-F]{8}", session_id):
            return []

        with _lock:
            sessions_dir = Path(__file__).resolve().parent.parent / "sessions"
            data = json.loads((sessions_dir / "index.json").read_text(encoding="utf-8"))
            entries = data.get("sessions", [])
            if not isinstance(entries, list):
                return []
            entry = next((item for item in entries
                          if isinstance(item, dict) and item.get("id") == session_id), None)
            if entry is None or not isinstance(entry.get("dir"), str):
                return []

            session_dir = (sessions_dir / entry["dir"]).resolve()
            if session_dir.parent != sessions_dir.resolve():
                return []

            events = []
            with (session_dir / "session.jsonl").open("r", encoding="utf-8") as handle:
                for line in handle:
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict):
                            events.append(event)
                    except Exception:
                        pass
            return events
    except Exception:
        return []


def search_sessions(query: str, limit: int = 30) -> list[dict]:
    """Case-insensitively search message text across indexed sessions."""
    try:
        if not isinstance(query, str) or not query.strip():
            return []
        stripped_query = query.strip()
        needle = stripped_query.casefold()
        result_limit = max(0, int(limit))
        if result_limit == 0:
            return []

        with _lock:
            sessions_dir = Path(__file__).resolve().parent.parent / "sessions"
            sessions_root = sessions_dir.resolve()
            data = json.loads((sessions_dir / "index.json").read_text(encoding="utf-8"))
            entries = data.get("sessions", [])
            if not isinstance(entries, list):
                return []

            matches = []
            for entry in entries:
                if not isinstance(entry, dict) or not isinstance(entry.get("dir"), str):
                    continue
                session_dir = (sessions_dir / entry["dir"]).resolve()
                if session_dir.parent != sessions_root:
                    continue
                first_text = None
                first_position = -1
                match_count = 0
                try:
                    with (session_dir / "session.jsonl").open(
                            "r", encoding="utf-8") as handle:
                        for line in handle:
                            try:
                                event = json.loads(line)
                            except Exception:
                                continue
                            if (not isinstance(event, dict)
                                    or event.get("type") not in {
                                        "user_message", "assistant_reply"
                                    }
                                    or not isinstance(event.get("text"), str)):
                                continue
                            text = event["text"]
                            position = text.casefold().find(needle)
                            if position < 0:
                                continue
                            match_count += 1
                            if first_text is None:
                                first_text = text
                                first_position = position
                except Exception:
                    continue
                if first_text is None:
                    continue
                if len(first_text) <= 160:
                    snippet = first_text
                else:
                    start = max(0, first_position - 80)
                    end = min(len(first_text), first_position + len(stripped_query) + 80)
                    snippet = (("…" if start else "") + first_text[start:end]
                               + ("…" if end < len(first_text) else ""))
                matches.append({
                    "id": entry.get("id"), "dir": entry.get("dir"),
                    "start": entry.get("start"), "summary": entry.get("summary"),
                    "snippet": snippet, "match_count": match_count,
                })
            matches.sort(key=lambda item: str(item.get("start") or ""), reverse=True)
            return matches[:result_limit]
    except Exception:
        return []
