"""Checks parsed GUC candidates against what's already in the local
tracker's "University" list, so re-running a check cycle doesn't add the
same deadline twice. Google Calendar's own dedup happens in guc/sync.py
(Step 5) — that needs a live API call, this only needs the tracker, which
is already local.

Matching is deliberately loose (same due date + overlapping title text)
rather than exact-string matching, since the same real deadline can get
scraped with slightly different wording from CMS vs Mail vs a re-run of
the same source after an instructor edits the announcement.
"""

from jarvis import trackers

LIST_NAME = "University"


def _normalize(text: str) -> str:
    return "".join(ch.lower() for ch in (text or "") if ch.isalnum())


def _same_day(due_a: str, due_b: str) -> bool:
    if not due_a or not due_b:
        return False
    return due_a[:10] == due_b[:10]  # ISO strings: YYYY-MM-DD prefix


def get_existing_university_items() -> list[dict]:
    items = trackers.list_items(category="custom")
    return [i for i in items if i.get("list_name") == LIST_NAME]


def is_duplicate(candidate: dict, existing_items: list[dict] = None) -> bool:
    """True if `candidate` (a scored item from guc/parser.py) matches an
    already-tracked University item closely enough to skip re-adding.

    Dated candidates (most high-confidence items) must match an existing
    item on the SAME DAY plus overlapping title text. Undated candidates
    (most needs-review items — the same ambiguous CMS line gets scraped
    again verbatim every check cycle until an instructor edits it) fall
    back to title-overlap alone within the same course, since there's no
    date to narrow on and the whole point is to stop that exact line from
    piling up duplicate tracker entries cycle after cycle."""
    if existing_items is None:
        existing_items = get_existing_university_items()

    cand_title = _normalize(candidate.get("title", ""))
    cand_course = _normalize(candidate.get("course", ""))
    if not cand_title:
        return False

    for item in existing_items:
        item_title = _normalize(item.get("text", ""))
        if not item_title:
            continue
        shorter, longer = sorted([cand_title, item_title], key=len)
        title_overlaps = bool(shorter) and shorter in longer

        if candidate.get("due"):
            if _same_day(candidate["due"], item.get("due") or "") and title_overlaps:
                return True
        else:
            # No date to compare — require the course to line up too
            # (via the tag we store it under) so items from different
            # courses that happen to share generic wording don't collide.
            item_course_tag = next(
                (t for t in item.get("tags", []) if t.startswith("course:")), ""
            )
            same_course = (not cand_course) or item_course_tag == f"course:{cand_course}"
            if title_overlaps and same_course:
                return True
    return False


def dedupe_candidates(candidates: list[dict]) -> tuple[list[dict], list[dict]]:
    """Splits candidates into (new_items, already_tracked_items)."""
    existing = get_existing_university_items()
    new_items, duplicates = [], []
    for candidate in candidates:
        if is_duplicate(candidate, existing):
            duplicates.append(candidate)
        else:
            new_items.append(candidate)
    return new_items, duplicates
