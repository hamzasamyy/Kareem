"""Defensive JSON-backed storage for personal tracker items.

This is a best-effort subsystem: every public function catches malformed
input, damaged JSON, and disk errors so tracker trouble cannot interrupt a
Kareem conversation.
"""

import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path


logger = logging.getLogger("kareem")
_lock = threading.Lock()
_path = Path(__file__).resolve().parent.parent / "data" / "trackers.json"
_categories = {"todo", "calendar", "files", "custom"}
_default_names = {
    "todo": "To-dos",
    "calendar": "Calendar",
    "files": "Files & Projects",
    "custom": "Custom",
}


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_locked():
    try:
        data = json.loads(_path.read_text(encoding="utf-8"))
        items = data.get("items", [])
        return items if isinstance(items, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        return []


def _write_locked(items):
    _path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _path.with_name(_path.name + ".tmp")
    temp_path.write_text(
        json.dumps({"items": items}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temp_path, _path)


def add_item(category, text, due=None, tags=None, list_name=None):
    """Add one item and return it, or None on invalid input/failure."""
    try:
        if category not in _categories or not isinstance(text, str) or not text.strip():
            return None
        if due is not None:
            datetime.fromisoformat(due)
        clean_tags = [] if tags is None else [str(tag).strip() for tag in tags if str(tag).strip()]
        name = str(list_name).strip() if list_name is not None else ""
        iso = _now()
        item = {
            "id": uuid.uuid4().hex[:8],
            "category": category,
            "list_name": name or _default_names[category],
            "text": text.strip(),
            "status": "pending",
            "due": due,
            "tags": clean_tags,
            "created": iso,
            "updated": iso,
            "notified": False,
        }
        with _lock:
            items = _read_locked()
            items.append(item)
            _write_locked(items)
        return dict(item)
    except Exception:
        logger.debug("trackers.add_item failed", exc_info=True)
        return None


def list_items(category=None, status=None):
    """Return filtered items, with upcoming due items first then newest."""
    try:
        if category is not None and category not in _categories:
            return []
        if status is not None and status not in {"pending", "done"}:
            return []
        with _lock:
            items = [dict(item) for item in _read_locked() if isinstance(item, dict)]
        items = [item for item in items
                 if (category is None or item.get("category") == category)
                 and (status is None or item.get("status") == status)]
        # Stable two-pass ordering: newest first within each bucket, while
        # dated items remain ahead of undated items in chronological order.
        items.sort(key=lambda item: item.get("created") or "", reverse=True)
        items.sort(key=lambda item: (item.get("due") is None, item.get("due") or ""))
        return items
    except Exception:
        return []


def _update(item_id, change):
    try:
        if not isinstance(item_id, str):
            return False
        with _lock:
            items = _read_locked()
            item = next((value for value in items
                         if isinstance(value, dict) and value.get("id") == item_id), None)
            if item is None:
                return False
            change(item)
            item["updated"] = _now()
            _write_locked(items)
        return True
    except Exception:
        logger.debug("trackers._update failed", exc_info=True)
        return False


def complete_item(item_id):
    return _update(item_id, lambda item: item.update(status="done"))


def set_completed(item_id, completed=True):
    """Mark (or unmark) an item completed. Stamps/clears `completed_at`
    separately from the generic `updated` timestamp, so a later unrelated
    edit (e.g. mark_seen) never resets a completion-based value (e.g. how
    long an item has been sitting completed) that a caller built on top
    of `completed_at`."""
    def change(item):
        item["status"] = "done" if completed else "pending"
        item["completed_at"] = _now() if completed else None
    return _update(item_id, change)


def mark_seen(item_id, seen=True):
    """Mark (or unmark) an item as seen/acknowledged. Items created before
    this field existed simply have no "seen" key — callers should read it
    with `item.get("seen", False)`."""
    return _update(item_id, lambda item: item.update(seen=bool(seen)))


def update_text(item_id, text):
    """Overwrite an item's display text in place, leaving everything else
    (status, tags, due, seen) untouched. Used by GUC's finance sync to
    reflect a portal-detected change (balance/status update) on an
    existing tracked item without deleting and recreating it."""
    if not isinstance(text, str) or not text.strip():
        return False
    return _update(item_id, lambda item: item.update(text=text.strip()))


def remove_item(item_id):
    try:
        with _lock:
            items = _read_locked()
            kept = [item for item in items
                    if not (isinstance(item, dict) and item.get("id") == item_id)]
            if len(kept) == len(items):
                return False
            _write_locked(kept)
        return True
    except Exception:
        logger.debug("trackers.remove_item failed", exc_info=True)
        return False


def set_reminder(item_id, due_iso):
    try:
        datetime.fromisoformat(due_iso)
    except Exception:
        return False
    return _update(item_id, lambda item: item.update(due=due_iso, notified=False))


def _local_aware(value):
    # A stored naive timestamp is local wall-clock time; astimezone() with no
    # argument presumes system-local time and applies the correct DST offset
    # FOR THAT DATE. The previous code stamped on datetime.now()'s single
    # current offset, which is wrong for any due date on the other side of a
    # daylight-saving change (reminder fires an hour early/late). An already
    # tz-aware value is just converted to local, preserving its instant.
    return datetime.fromisoformat(value).astimezone()


def _is_due(item, now=None):
    """Return whether a dated item is due, using the tracker's local timezone rules."""
    current = now or datetime.now().astimezone()
    if current.tzinfo is None:
        current = current.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return bool(item.get("due") and _local_aware(item["due"]) <= current)


def due_reminders(now=None):
    try:
        due = []
        for item in list_items(status="pending"):
            try:
                if not item.get("notified") and _is_due(item, now):
                    due.append(item)
            except Exception:
                continue
        return due
    except Exception:
        return []


def mark_notified(item_id):
    return _update(item_id, lambda item: item.update(notified=True))


_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
}

_TIME_RE = re.compile(r"(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", re.I)


def _parse_time_of_day(text, fallback_hour, fallback_minute):
    """Parse a bare time-of-day fragment like '12am', '9:30pm', 'at 4pm', or
    '' (no time given at all — keep the fallback hour/minute so a bare
    weekday like 'next Thursday' lands at the current time of day, matching
    how the 'in N days' branch below behaves). None means unparseable."""
    text = text.strip()
    if not text:
        return fallback_hour, fallback_minute
    match = re.fullmatch(_TIME_RE, text)
    if not match:
        return None
    hour_text, minute_text, meridiem = match.groups()
    hour, minute = int(hour_text), int(minute_text or 0)
    if minute > 59 or (meridiem and not 1 <= hour <= 12) or (not meridiem and hour > 23):
        return None
    if meridiem:
        hour = (hour % 12) + (12 if meridiem.lower() == "pm" else 0)
    return hour, minute


def _resolve_relative_weekday(value, now):
    """Handle '(next|this|coming) <weekday>[ <time>]'.

    This exists because the 'dateparser' fallback below cannot: its relative-
    date parser matches the leading 'next' token, then fails outright on the
    weekday name instead of falling back to its absolute-date parser, so
    'next Thursday 12am' silently returns None from the library alone.
    'next <weekday>' skips today if today IS that weekday (7 days out, not
    0); bare or 'this'/'coming' <weekday> means the nearest occurrence,
    which can be today.
    """
    match = re.fullmatch(
        r"(?:(next|this|coming)\s+)?"
        r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
        r"(?:\s+(.*))?",
        value, re.I,
    )
    if not match:
        return None
    modifier, weekday_word, time_part = match.groups()
    target_idx = _WEEKDAYS[weekday_word.lower()]
    delta_days = (target_idx - now.weekday()) % 7
    if (modifier or "").lower() == "next" and delta_days == 0:
        delta_days = 7
    target_date = (now + timedelta(days=delta_days)).date()

    time_of_day = _parse_time_of_day(time_part or "", now.hour, now.minute)
    if time_of_day is None:
        return None
    hour, minute = time_of_day
    result = datetime.combine(target_date, datetime.min.time()).replace(
        hour=hour, minute=minute, tzinfo=now.tzinfo
    )
    return result.isoformat(timespec="seconds")


def _resolve_with_dateparser(value, now):
    """Broader natural-language fallback (e.g. 'August 3rd', 'the 25th', '3
    weeks from now') for phrasings the fast regexes above don't cover.
    Returns None (never raises) if the library isn't installed or can't
    parse the text — this is a best-effort last resort, not a requirement."""
    try:
        import dateparser
    except ImportError:
        return None
    parsed = dateparser.parse(
        value,
        settings={
            "PREFER_DATES_FROM": "future",
            "RELATIVE_BASE": now.replace(tzinfo=None),
            "RETURN_AS_TIMEZONE_AWARE": False,
        },
    )
    if parsed is None:
        return None
    return parsed.replace(tzinfo=now.tzinfo).isoformat(timespec="seconds")


def parse_due(text):
    """Resolve a due-date phrase to an ISO datetime string, or None if it
    genuinely can't be understood. Tries fast, predictable regexes first
    (ISO, 'in N units', '(today|tomorrow) at H(am/pm)', '(next|this)
    <weekday>'), then falls back to the 'dateparser' library for anything
    else — so the tool succeeds even when the model passes a natural-
    language phrase instead of a resolved ISO date."""
    try:
        value = str(text).strip()
        if not value:
            return None
        try:
            return datetime.fromisoformat(value).isoformat()
        except ValueError:
            pass

        now = datetime.now().astimezone()
        relative = re.fullmatch(r"in\s+(\d+)\s+(minute|hour|day)s?", value, re.I)
        if relative:
            amount = int(relative.group(1))
            delta = {"minute": timedelta(minutes=amount),
                     "hour": timedelta(hours=amount),
                     "day": timedelta(days=amount)}[relative.group(2).lower()]
            return (now + delta).isoformat(timespec="seconds")

        clock = re.fullmatch(
            r"(?:(today|tomorrow)\s+)?at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?",
            value, re.I,
        )
        if clock:
            day_word, hour_text, minute_text, meridiem = clock.groups()
            hour, minute = int(hour_text), int(minute_text or 0)
            if minute > 59 or (meridiem and not 1 <= hour <= 12) or (not meridiem and hour > 23):
                return None
            if meridiem:
                hour = (hour % 12) + (12 if meridiem.lower() == "pm" else 0)
            result = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if day_word and day_word.lower() == "tomorrow":
                result += timedelta(days=1)
            elif not day_word and result <= now:
                result += timedelta(days=1)
            return result.isoformat(timespec="seconds")

        weekday_result = _resolve_relative_weekday(value, now)
        if weekday_result is not None:
            return weekday_result

        return _resolve_with_dateparser(value, now)
    except Exception:
        return None
