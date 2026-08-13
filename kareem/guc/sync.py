"""Writes scored GUC deadline candidates (from guc/parser.py) to the local
tracker and, for high-confidence items only, to Google Calendar.

Reuses what already exists rather than inventing anything new:
- kareem.trackers's "custom" category + "University" list_name (Step 4).
- kareem.tools.calendar_auth.get_calendar_service() — the SAME Google
  Calendar OAuth plumbing the calendar_add_event/calendar_list_events
  tools already use, called directly here (this is an internal caller,
  not the LLM, so there's no need to go through the text-formatted tool
  wrapper — calling the API client directly avoids a fragile round trip
  through calendar_list_events's human-readable text output).
- kareem.guc.dedupe for duplicate checking against the local tracker.

High-confidence items: tracker + Google Calendar (with a 60-minute popup
reminder), skipped if a duplicate is found in EITHER place (the tracker
item could have been completed/deleted while the calendar event still
exists, or vice versa — both are checked independently).
Needs-review items: tracker only, tagged "needs_review" so the web UI
(Step 6) can render them in a distinct section with Approve/Dismiss.
Never raises out of the top-level sync_candidates() — one bad item is
logged and skipped, not allowed to abort the whole run.
"""

from datetime import datetime, timedelta

from kareem import trackers
from kareem.guc import dedupe

LIST_NAME = "University"
REMINDER_MINUTES_BEFORE = 60


def _local_timezone_name() -> str:
    from tzlocal import get_localzone_name
    return get_localzone_name()


def _item_text(candidate: dict) -> str:
    course = candidate.get("course", "").strip()
    title = candidate.get("title", "").strip()[:150]
    return f"{course} — {title}" if course else title


def _course_tag(candidate: dict) -> str:
    course = "".join(ch.lower() for ch in candidate.get("course", "") if ch.isalnum())
    return f"course:{course}" if course else "course:"


def _type_tag(candidate: dict) -> str:
    """One of assignment/quiz/exam/final (see guc/parser.py's
    TYPE_KEYWORDS) — lets the web UI route items into the Assignments/
    Quizzes/Exams sections without re-deriving type from free text."""
    item_type = (candidate.get("type") or "").strip().lower()
    return f"type:{item_type}" if item_type else "type:"


def _source_tag(candidate: dict) -> str:
    """cms_announcement / portal_exam_seats / mail / etc (see guc/
    parser.py) — lets the web UI route mail-sourced items into the Emails
    section regardless of their detected type."""
    source = (candidate.get("source") or "").strip().lower()
    return f"source:{source}" if source else "source:"


def _calendar_has_matching_event(service, summary: str, due_iso: str) -> bool:
    """True if a Google Calendar event with an overlapping title already
    exists on the same day as `due_iso`."""
    try:
        due = datetime.fromisoformat(due_iso)
    except ValueError:
        return False
    day_start = due.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    try:
        result = service.events().list(
            calendarId="primary",
            timeMin=day_start.astimezone().isoformat(),
            timeMax=day_end.astimezone().isoformat(),
            singleEvents=True,
        ).execute()
    except Exception:
        return False  # can't verify -> don't block on an unrelated API hiccup
    needle = "".join(ch.lower() for ch in summary if ch.isalnum())
    for event in result.get("items", []):
        existing = "".join(ch.lower() for ch in event.get("summary", "") if ch.isalnum())
        if not existing or not needle:
            continue
        shorter, longer = sorted([existing, needle], key=len)
        if shorter and shorter in longer:
            return True
    return False


def _create_calendar_event(calendar_service, summary: str, due_iso: str, description: str = "") -> bool:
    """Inserts one Google Calendar event with the standard GUC reminder.
    Returns True on success, False on any failure (never raises)."""
    try:
        start_dt = datetime.fromisoformat(due_iso)
        end_dt = start_dt + timedelta(hours=1)
        tz_name = _local_timezone_name()
        body = {
            "summary": summary[:200],
            "description": description[:1000],
            "start": {"dateTime": start_dt.isoformat(), "timeZone": tz_name},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": tz_name},
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": REMINDER_MINUTES_BEFORE}],
            },
        }
        calendar_service.events().insert(calendarId="primary", body=body).execute()
        return True
    except Exception:
        return False


def add_high_confidence_item(candidate: dict, calendar_service) -> dict:
    """Adds one high-confidence candidate to the tracker + (if a calendar
    service is available) Google Calendar. Never raises."""
    result = {"tracker": False, "calendar": False, "skipped_duplicate": False, "error": None}
    try:
        if dedupe.is_duplicate(candidate):
            result["skipped_duplicate"] = True
            return result

        due = candidate.get("due")
        text = _item_text(candidate)

        if calendar_service is not None and due:
            if _calendar_has_matching_event(calendar_service, text, due):
                result["skipped_duplicate"] = True
                return result

        item = trackers.add_item(
            "custom", text, due=due,
            tags=["guc", "auto_added", _course_tag(candidate),
                  _type_tag(candidate), _source_tag(candidate)],
            list_name=LIST_NAME,
        )
        result["tracker"] = item is not None

        if calendar_service is not None and due:
            ok = _create_calendar_event(calendar_service, text, due, candidate.get("raw_text", ""))
            result["calendar"] = ok
            if not ok:
                result["error"] = "calendar write failed"
    except Exception as exc:
        result["error"] = str(exc)
    return result


def add_needs_review_item(candidate: dict) -> dict:
    """Adds one needs-review candidate to the tracker only (no calendar
    write). Never raises."""
    result = {"tracker": False, "error": None}
    try:
        if dedupe.is_duplicate(candidate):
            result["skipped_duplicate"] = True
            return result
        text = _item_text(candidate)
        item = trackers.add_item(
            "custom", text, due=candidate.get("due"),
            tags=["guc", "needs_review", _course_tag(candidate),
                  _type_tag(candidate), _source_tag(candidate)],
            list_name=LIST_NAME,
        )
        result["tracker"] = item is not None
        result["item"] = item
    except Exception as exc:
        result["error"] = str(exc)
    return result


def sync_candidates(candidates: list[dict]) -> dict:
    """Top-level entry point: writes every candidate to the right place(s)
    and returns a summary for logging. Never raises."""
    try:
        from kareem.tools.calendar_auth import get_calendar_service
        calendar_service = get_calendar_service()
        calendar_error = None
    except Exception as exc:
        calendar_service = None
        calendar_error = str(exc)

    high = [c for c in candidates if c.get("confidence") == "high"]
    low = [c for c in candidates if c.get("confidence") == "low"]

    auto_added, skipped_duplicates, failures = 0, 0, 0
    for candidate in high:
        r = add_high_confidence_item(candidate, calendar_service)
        if r.get("skipped_duplicate"):
            skipped_duplicates += 1
        elif r.get("tracker"):
            auto_added += 1
        else:
            failures += 1

    flagged = 0
    for candidate in low:
        r = add_needs_review_item(candidate)
        if r.get("skipped_duplicate"):
            skipped_duplicates += 1
        elif r.get("tracker"):
            flagged += 1
        else:
            failures += 1

    return {
        "auto_added": auto_added,
        "flagged_for_review": flagged,
        "skipped_duplicates": skipped_duplicates,
        "failures": failures,
        "calendar_available": calendar_service is not None,
        "calendar_error": calendar_error,
    }


def _find_university_item(item_id: str) -> dict | None:
    for item in dedupe.get_existing_university_items():
        if item.get("id") == item_id:
            return item
    return None


def approve_review_item(item_id: str) -> dict:
    """User-approved a needs-review item from the web UI: re-tags it as
    auto_added and, if it has a due date, adds it to Google Calendar too
    (needs-review items are tracker-only until approved). Implemented as
    remove-then-readd rather than an in-place tracker update, since
    kareem.trackers has no update-tags primitive and adding one wasn't
    worth extending that shared module for — this stays entirely inside
    the isolated guc/ package.

    Returns {"ok": bool, "calendar": bool, "error": str|None}."""
    item = _find_university_item(item_id)
    if item is None:
        return {"ok": False, "calendar": False, "error": "item not found"}
    if "needs_review" not in item.get("tags", []):
        return {"ok": False, "calendar": False, "error": "item is not a needs-review item"}

    text = item.get("text", "")
    due = item.get("due")
    course_tag = next((t for t in item.get("tags", []) if t.startswith("course:")), "course:")
    type_tag = next((t for t in item.get("tags", []) if t.startswith("type:")), "type:")
    source_tag = next((t for t in item.get("tags", []) if t.startswith("source:")), "source:")

    calendar_ok = False
    if due:
        try:
            from kareem.tools.calendar_auth import get_calendar_service
            service = get_calendar_service()
            calendar_ok = _create_calendar_event(service, text, due)
        except Exception:
            calendar_ok = False

    trackers.remove_item(item_id)
    trackers.add_item(
        "custom", text, due=due,
        tags=["guc", "auto_added", course_tag, type_tag, source_tag],
        list_name=LIST_NAME,
    )
    return {"ok": True, "calendar": calendar_ok, "error": None}


def _parse_finance_due(value: str) -> str | None:
    """Parses GUC's "Weekday,DD-MM-YYYY" format (e.g. "Sunday,25-10-2026")
    into an ISO date string, or None if it doesn't match."""
    try:
        _, date_part = value.split(",", 1)
        dt = datetime.strptime(date_part.strip(), "%d-%m-%Y")
        return dt.replace(hour=23, minute=59).isoformat()
    except Exception:
        return None


def sync_finances(rows: list[dict]) -> dict:
    """Writes payment-request rows (from guc/finance_scraper.py) to the
    tracker. Finance items are informational, not deadline candidates —
    they skip guc/parser.py entirely (the data is already structured and
    authoritative) and guc/dedupe.py's fuzzy text matching (GUC's own
    "Reference" id is a far more reliable key, stored as a
    `finance_ref:<reference>` tag). No calendar write. Finance items are
    fully read-only in the UI (no Seen/Completed/Delete) — this scrape is
    the ONLY thing that can ever change what one displays: a reference
    that already exists gets its displayed text updated in place if the
    portal's amount/status changed, rather than skipped outright. Never
    raises."""
    existing_by_ref = {
        tag.split(":", 1)[1]: item
        for item in dedupe.get_existing_university_items()
        for tag in item.get("tags", [])
        if tag.startswith("finance_ref:")
    }
    added, updated, skipped, failures = 0, 0, 0, 0
    for row in rows:
        try:
            reference = (row.get("Reference") or "").strip()
            if not reference:
                continue
            description = (row.get("PaymentDescription") or "Payment").strip()
            currency = (row.get("Currency") or "").strip()
            amount = (row.get("Amount") or "").strip()
            due = _parse_finance_due(row.get("DueDate [Deadine]") or "")
            text = f"Finance — {description}: {amount} {currency}".strip()

            existing = existing_by_ref.get(reference)
            if existing is not None:
                if existing.get("text") != text:
                    if trackers.update_text(existing["id"], text):
                        updated += 1
                    else:
                        failures += 1
                else:
                    skipped += 1
                continue

            item = trackers.add_item(
                "custom", text, due=due,
                tags=["guc", "finance", f"finance_ref:{reference}"],
                list_name=LIST_NAME,
            )
            if item is not None:
                added += 1
            else:
                failures += 1
        except Exception:
            failures += 1
    return {"added": added, "updated": updated, "skipped_duplicates": skipped, "failures": failures}


def sync_announcements(announcements: list[dict]) -> dict:
    """Writes low-signal scraped items (from guc/parser.py's
    `find_candidates_in_text` "announcements" bucket — a type-keyword
    match with no date token, e.g. a reservation link or a code-hint note)
    to the tracker. These are FYI-only: no due date, no Completed/auto-
    delete semantics, just Seen (mark_seen). Deduped via
    guc/dedupe.py's existing
    undated-item path (same course + title overlap). Never raises."""
    existing = dedupe.get_existing_university_items()
    added, skipped, failures = 0, 0, 0
    for candidate in announcements:
        try:
            if dedupe.is_duplicate(candidate, existing):
                skipped += 1
                continue
            text = _item_text(candidate)
            item = trackers.add_item(
                "custom", text, due=None,
                tags=["guc", "announcement", _course_tag(candidate),
                      _type_tag(candidate), _source_tag(candidate)],
                list_name=LIST_NAME,
            )
            if item is not None:
                added += 1
                existing.append(item)
            else:
                failures += 1
        except Exception:
            failures += 1
    return {"added": added, "skipped_duplicates": skipped, "failures": failures}


def dismiss_review_item(item_id: str) -> dict:
    """User-dismissed a needs-review item: discard it entirely (not moved
    anywhere, per the spec — Dismiss just removes it)."""
    item = _find_university_item(item_id)
    if item is None:
        return {"ok": False, "error": "item not found"}
    if "needs_review" not in item.get("tags", []):
        return {"ok": False, "error": "item is not a needs-review item"}
    ok = trackers.remove_item(item_id)
    return {"ok": ok, "error": None if ok else "remove failed"}


# ---------------------------------------------------------------- sections
# Single source of truth for which of the University screen's tabs an item
# belongs to — the web layer (status endpoint, bulk actions) MUST call
# section_for_item() rather than re-deriving the same routing separately,
# so the two can never disagree about what's in "Assignments" vs "Exams"
# vs "Completed" etc.

SECTIONS = ("assignments", "quizzes", "exams", "emails", "announcements",
            "finances", "completed")

# Sections whose items never move to Completed even when marked done:
# finance is fully read-only (no Completed concept at all — see
# sync_finances above) and announcements are FYI-only with no due date to
# have completed a deadline for.
_NEVER_COMPLETES = ("finances", "announcements")


def _category_for_item(item: dict) -> str:
    """The item's ORIGINAL category, ignoring completion status — this is
    what section_for_item() moved out of once an item is marked done, but
    it's still needed to label a Completed-tab item with where it came
    from ("Assignment", "Exam", ...)."""
    tags = item.get("tags", [])
    if "finance" in tags:
        return "finances"
    if "announcement" in tags:
        return "announcements"
    source = next((t.split(":", 1)[1] for t in tags if t.startswith("source:")), "")
    if source == "mail":
        return "emails"
    item_type = next((t.split(":", 1)[1] for t in tags if t.startswith("type:")), "")
    if item_type == "assignment":
        return "assignments"
    if item_type == "quiz":
        return "quizzes"
    if item_type in ("exam", "final"):
        return "exams"
    return "other"


def section_for_item(item: dict) -> str:
    """Routes one University tracker item into a tab id (see SECTIONS
    above), or "other" if nothing matches (still shown somewhere by the
    web UI, never silently dropped).

    A completed item moves OUT of its original tab and INTO "completed" —
    this is the entire move-not-delete mechanism: since the web layer
    always re-fetches /api/guc/status right after marking something
    completed, and this function is the single routing authority that
    endpoint uses, flipping `status` to "done" is all it takes for an item
    to reappear under the Completed tab instead of its original one. Use
    _category_for_item() above to recover what that original tab was for
    display labeling once an item lands here."""
    category = _category_for_item(item)
    if item.get("status") == "done" and category not in _NEVER_COMPLETES:
        return "completed"
    return category


def _items_in_scope(scope: str) -> list[dict]:
    """`scope` is "all" or one of SECTIONS. Only ever returns items that
    are actually visible on the University screen (auto_added or finance —
    NOT needs_review, which has its own Approve/Dismiss flow, not Seen/
    Completed). Announcement items ARE included (they have Seen/Dismiss
    like everything else here) but bulk_set_completed below already skips
    them the same way it skips finance items, since announcements have no
    Completed concept."""
    items = [
        item for item in dedupe.get_existing_university_items()
        if ("auto_added" in item.get("tags", []) or "finance" in item.get("tags", [])
            or "announcement" in item.get("tags", []))
    ]
    if scope == "all":
        return items
    return [item for item in items if section_for_item(item) == scope]


def bulk_mark_seen(scope: str) -> dict:
    """Marks every eligible item in `scope` as seen. Finance items are
    excluded — Finance is fully read-only, its displayed status changes
    only via sync_finances() detecting a real portal change, never a
    manual toggle of any kind. Never raises."""
    count = 0
    for item in _items_in_scope(scope):
        if "finance" in item.get("tags", []):
            continue
        try:
            if trackers.mark_seen(item["id"], True):
                count += 1
        except Exception:
            pass
    return {"marked_seen": count}


def bulk_set_completed(scope: str) -> dict:
    """Marks every eligible item in `scope` completed. Finance AND
    announcement items are excluded — Finance has no Completed concept at
    all (fully read-only, see sync_finances above), and announcements are
    FYI-only with no due date to have "completed" a deadline for in the
    first place. Marking an item completed here is what moves it into the
    Completed tab: see section_for_item()'s docstring — no separate move
    step is needed, the routing itself is status-driven."""
    count = 0
    for item in _items_in_scope(scope):
        tags = item.get("tags", [])
        if "finance" in tags or "announcement" in tags:
            continue
        try:
            if trackers.set_completed(item["id"], True):
                count += 1
        except Exception:
            pass
    return {"marked_completed": count}
