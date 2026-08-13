"""Deterministic proactive briefing for a newly started Kareem session."""

from datetime import datetime

from kareem import trackers


def build_briefing() -> str | None:
    """Compose an AT-MOST-one-line mention of what's due, or ``None``.

    Deliberately never lists item titles/dates — a full recitation read
    aloud unprompted (especially once the GUC "University" list is in the
    same tracker) is exactly the naggy behavior this must avoid. If the
    user wants details they ask ("what's due this week?") or open the
    relevant panel; this only earns its keep as a single short nudge.
    Never raises.
    """
    try:
        now = datetime.now().astimezone()
        due_items = [
            item for item in trackers.list_items(status="pending")
            if trackers._is_due(item, now)
        ]
        if not due_items:
            return None

        time_of_day = "morning" if now.hour < 12 else "afternoon" if now.hour < 18 else "evening"
        count = len(due_items)

        needs_review_count = 0
        try:
            from kareem.guc import dedupe as guc_dedupe
            needs_review_count = sum(
                1 for item in guc_dedupe.get_existing_university_items()
                if "needs_review" in item.get("tags", [])
            )
        except Exception:
            needs_review_count = 0

        sentence = f"Good {time_of_day}. You have {count} thing{'s' if count != 1 else ''} due."
        if needs_review_count:
            sentence += (
                f" {needs_review_count} university item"
                f"{'s' if needs_review_count != 1 else ''} need{'s' if needs_review_count == 1 else ''} review."
            )
        return sentence
    except Exception:
        return None
