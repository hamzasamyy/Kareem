"""Tools for the user's real Google Calendar."""

from datetime import datetime, time, timedelta

from kareem import trackers
from kareem.errors import user_safe_error
from kareem.safety import guard
from kareem.tools import register
from kareem.tools.calendar_auth import get_calendar_service


_DUE_HELP = (
    "Times may be ISO 8601, 'in N minutes/hours/days', 'at 3pm', "
    "'today at 4:30pm', or 'tomorrow at 9am'."
)


def _error(exc):
    if isinstance(exc, RuntimeError):
        return f"Google Calendar isn't connected: {user_safe_error(exc)}"
    return f"Google rejected that request: {user_safe_error(exc)}"


def _parse_datetime(value):
    parsed = trackers.parse_due(value)
    if parsed is None:
        return None
    result = datetime.fromisoformat(parsed)
    if result.tzinfo is None:
        result = result.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return result


def _timezone_name():
    from tzlocal import get_localzone_name
    return get_localzone_name()


def _event_lines(events):
    lines = []
    for event in events[:20]:
        start = event.get("start", {})
        raw = start.get("dateTime") or start.get("date") or "unknown time"
        try:
            shown = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone().strftime(
                "%a %b %d, %I:%M %p"
            )
        except (ValueError, TypeError):
            shown = raw
        lines.append(f"{shown} — {event.get('summary', '(untitled)')} [id: {event.get('id', '?')}]")
    if len(events) > 20:
        lines.append(f"…and {len(events) - 20} more.")
    return "\n".join(lines)


@register({
    "name": "calendar_add_event",
    "description": "Create an event on the user's real Google Calendar. "
                    "See the 'start' parameter for accepted time formats.",
    "parameters": {"type": "object", "properties": {
        "summary": {"type": "string", "description": "Event title"},
        "start": {"type": "string", "description": _DUE_HELP},
        "end": {"type": "string", "description": "Optional end time, same formats as 'start'."},
        "description": {"type": "string"},
        "location": {"type": "string"},
    }, "required": ["summary", "start"]},
})
def calendar_add_event(summary: str, start: str, end=None, description=None, location=None) -> str:
    try:
        start_dt = _parse_datetime(start)
        if start_dt is None:
            return f"I couldn't understand the event start. {_DUE_HELP}"
        end_dt = _parse_datetime(end) if end is not None else start_dt + timedelta(hours=1)
        if end is not None and end_dt is None:
            return f"I couldn't understand the event end. {_DUE_HELP}"
        if end_dt <= start_dt:
            return "The event end must be after its start."
        timezone_name = _timezone_name()
        body = {
            "summary": summary,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": timezone_name},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": timezone_name},
        }
        if description:
            body["description"] = description
        if location:
            body["location"] = location
        event = get_calendar_service().events().insert(
            calendarId="primary", body=body
        ).execute()
        return (f"Added '{event.get('summary', summary)}' to Google Calendar "
                f"[id: {event.get('id', '?')}]. {event.get('htmlLink', '')}".rstrip())
    except Exception as exc:
        return _error(exc)


def _range_bounds(label):
    normalized = (label or "today").strip().lower()
    now = datetime.now().astimezone()
    today = datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)
    if normalized == "today":
        return normalized, today, today + timedelta(days=1)
    if normalized == "yesterday":
        start = today - timedelta(days=1)
        return normalized, start, today
    if normalized == "tomorrow":
        start = today + timedelta(days=1)
        return normalized, start, start + timedelta(days=1)
    if normalized == "this week":
        start = today - timedelta(days=today.weekday())
        return normalized, start, start + timedelta(days=7)
    if normalized == "last week":
        this_week_start = today - timedelta(days=today.weekday())
        start = this_week_start - timedelta(days=7)
        return normalized, start, this_week_start
    if normalized in {"this weekend", "the weekend", "weekend"}:
        # Saturday 00:00 through Monday 00:00 of the CURRENT week
        # (datetime.weekday(): Monday=0 ... Saturday=5, Sunday=6) — if today
        # IS already Sat/Sun this still covers the rest of the ongoing
        # weekend rather than jumping to next week's.
        saturday = today - timedelta(days=today.weekday()) + timedelta(days=5)
        return "this weekend", saturday, saturday + timedelta(days=2)
    if normalized in {"next 7 days", "next week"}:
        return normalized, now, now + timedelta(days=7)

    # Anything else: resolve it as a single date/time with the SAME
    # natural-language parsing tracker_add and calendar_add_event's own
    # start/end use (_parse_datetime above wraps trackers.parse_due) — a
    # specific date or phrase ("2026-07-25", "next Thursday", "August 3rd")
    # becomes a one-day range covering that whole day. This is what makes
    # "yesterday"/"last week"/"this weekend" above the exception rather than
    # the rule: those 3 need bespoke start/end math no generic single-date
    # parse can produce, but anything parse_due already understands falls
    # through to here for free instead of needing its own branch.
    anchor = _parse_datetime(normalized)
    if anchor is not None:
        day_start = datetime.combine(anchor.date(), time.min, tzinfo=anchor.tzinfo)
        return normalized, day_start, day_start + timedelta(days=1)

    raise ValueError(
        "Use 'today', 'yesterday', 'tomorrow', 'this week', 'last week', "
        "'this weekend', 'next 7 days', or a specific date."
    )


@register({
    "name": "calendar_list_events",
    "description": "List events on the user's real Google Calendar for a date range. "
                    "See the 'range' parameter for accepted values.",
    "parameters": {"type": "object", "properties": {
        "range": {"type": "string", "description": "Defaults to today. Also accepts "
                  "'yesterday', 'tomorrow', 'this week', 'last week', 'this weekend', "
                  "'next 7 days', or a specific date/phrase (e.g. '2026-07-25', "
                  "'next Thursday', 'August 3rd')."},
    }},
})
def calendar_list_events(range=None) -> str:
    try:
        label, start, end = _range_bounds(range)
        result = get_calendar_service().events().list(
            calendarId="primary", timeMin=start.isoformat(), timeMax=end.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=250,
        ).execute()
        events = result.get("items", [])
        return _event_lines(events) if events else f"Nothing on your calendar for {label}."
    except ValueError as exc:
        return str(exc)
    except Exception as exc:
        return _error(exc)


@register({
    "name": "calendar_find_event",
    "description": "Search upcoming Google Calendar events by text and return Google's event ids for follow-up actions.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "Text in the event"},
    }, "required": ["query"]},
})
def calendar_find_event(query: str) -> str:
    try:
        now = datetime.now().astimezone()
        result = get_calendar_service().events().list(
            calendarId="primary", q=query, timeMin=now.isoformat(),
            timeMax=(now + timedelta(days=90)).isoformat(), singleEvents=True,
            orderBy="startTime", maxResults=250,
        ).execute()
        events = result.get("items", [])
        return _event_lines(events) if events else f"No upcoming calendar events matched '{query}'."
    except Exception as exc:
        return _error(exc)


@register({
    "name": "calendar_delete_event",
    "description": "Delete one real Google Calendar event after confirmation. event_id must be Google's full event id returned by calendar_add_event, calendar_list_events, or calendar_find_event; it is not an 8-character tracker id.",
    "parameters": {"type": "object", "properties": {
        "event_id": {"type": "string", "description": "Google's full Calendar event id, not a tracker id"},
    }, "required": ["event_id"]},
})
def calendar_delete_event(event_id: str) -> str:
    def _do_delete():
        try:
            get_calendar_service().events().delete(
                calendarId="primary", eventId=event_id
            ).execute()
            return f"Deleted Google Calendar event {event_id}."
        except Exception as exc:
            return _error(exc)

    try:
        return guard(
            "delete_calendar_event",
            f"Delete Google Calendar event '{event_id}'",
            _do_delete,
        )
    except Exception as exc:
        return _error(exc)
