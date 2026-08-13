"""Turns raw GUC scraped data (CMS/Portal/Mail) into structured deadline
candidates with a confidence level, plus a separate "announcements" bucket
for scraped items that don't carry real deadline signal. Pure parsing —
never logs in, never writes to the tracker or Google Calendar (that's
guc/sync.py).

Classification philosophy (revised — the original version was too loose,
routing things like reservation links and code-hint notes into "needs
review" just because they mentioned "assignment" or "milestone" in
passing): a line only becomes a DEADLINE CANDIDATE (high confidence or
needs-review) if it has BOTH a type keyword (quiz/assignment/exam/final)
AND at least one date-shaped token somewhere in it. A type-keyword line
with NO date token at all — a game-room reservation link, a clarification
about abstract classes, a course-email address — carries no scheduling
signal and is not a deadline; it becomes an ANNOUNCEMENT instead (still
surfaced, just not treated as actionable). Nothing is ever silently
dropped: every type-keyword match ends up in exactly one of
high-confidence / needs-review ("date unclear" when a date token exists
but can't be pinned down) / announcements.
"""

import re
from datetime import datetime

TYPE_KEYWORDS = [
    ("final", "final"),
    ("midterm", "exam"),
    ("quiz", "quiz"),
    ("assignment", "assignment"),
    ("milestone", "assignment"),
    ("project", "assignment"),
    ("exam", "exam"),
]

# Titles/lines containing these are reference/practice material, not live
# deadlines — GUC's own CMS labels past exams/solutions this way.
NOISE_MARKERS = ["solution", "sample", "makeup", "revision", "practice"]

# Substrings checked as simple prefixes so real-world typos ("staring" for
# "starting", seen verbatim in actual GUC announcements) still match —
# "week star" is a prefix of both "week starting" and "week staring".
AMBIGUOUS_PHRASES = [
    "week star", "the week of", "between", " or ", "tbd", "to be announced",
]

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}
_MONTH_PATTERN = "|".join(MONTHS.keys())

# "15th of March, 2026" / "15 March 2026" / "15-March-2026"
_DATE_DAY_FIRST_RE = re.compile(
    r"(\d{1,2})(?:st|nd|rd|th)?\s*(?:-|of|,)?\s*(" + _MONTH_PATTERN + r")\.?,?\s*(\d{4})?",
    re.IGNORECASE,
)
# "March 15th, 2026" / "April 8th" — real GUC phrasing this project's own
# scraped data actually contains and the day-first pattern above cannot
# match (verified against real examples like "next Wednesday, April 8th").
_DATE_MONTH_FIRST_RE = re.compile(
    r"\b(" + _MONTH_PATTERN + r")\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b(?:,?\s*(\d{4}))?",
    re.IGNORECASE,
)
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2})\s*(am|pm)?", re.IGNORECASE)
_SLASH_DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")


def _find_dates_in_line(line: str, default_year: int) -> list[dict]:
    found = []
    seen_spans = set()

    def _add(match, day_i, month_i, year_str):
        span = match.span()
        if span in seen_spans:
            return
        try:
            year_i = int(year_str) if year_str else default_year
            dt = datetime(year_i, month_i, day_i)
        except ValueError:
            return
        seen_spans.add(span)
        found.append({"matched": match.group(0), "date": dt, "explicit_year": bool(year_str)})

    for m in _DATE_DAY_FIRST_RE.finditer(line):
        day, month_name, year_str = m.group(1), m.group(2), m.group(3)
        _add(m, int(day), MONTHS[month_name.lower()], year_str)

    for m in _DATE_MONTH_FIRST_RE.finditer(line):
        month_name, day, year_str = m.group(1), m.group(2), m.group(3)
        _add(m, int(day), MONTHS[month_name.lower()], year_str)

    for m in _SLASH_DATE_RE.finditer(line):
        day_i, month_i = int(m.group(1)), int(m.group(2))
        year_str = m.group(3)
        if month_i > 12 or day_i > 31:
            continue
        if year_str and len(year_str) == 2:
            year_str = str(int(year_str) + 2000)
        _add(m, day_i, month_i, year_str)

    return found


def _find_time_in_line(line: str):
    m = _TIME_RE.search(line)
    if not m:
        return None
    hour, minute, meridiem = int(m.group(1)), int(m.group(2)), m.group(3)
    if meridiem:
        hour = (hour % 12) + (12 if meridiem.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def _classify_type(line: str):
    lowered = line.lower()
    for keyword, item_type in TYPE_KEYWORDS:
        # word-boundary match — "exam" must not match inside "example"
        if re.search(r"\b" + re.escape(keyword) + r"\b", lowered):
            return item_type
    return None


def _looks_like_noise(line: str) -> bool:
    lowered = line.lower()
    if any(marker in lowered for marker in NOISE_MARKERS):
        return True
    # Old-archive listings (course archives commonly list years well before
    # the current one, e.g. "midterm spring 2011", "final 2014"). But a line
    # can ALSO mention a past year only in passing while stating a real
    # current/future deadline ("…covering 2019 material, due 5 January 2026") —
    # calling that archive-noise silently DROPPED a genuine deadline (bug F3).
    # So treat the year signal as noise only when EVERY explicit year in the
    # line is in the past; a line carrying any current/future year is kept for
    # normal date parsing and scoring downstream.
    years = [int(match.group(0)) for match in re.finditer(r"\b(?:19|20)\d{2}\b", lowered)]
    if years and all(year < datetime.now().year for year in years):
        return True
    return False


def _looks_ambiguous(line: str) -> bool:
    lowered = line.lower()
    return any(phrase in lowered for phrase in AMBIGUOUS_PHRASES)


def find_candidates_in_text(text: str, course: str, source: str, default_year: int = None) -> dict:
    """Scans free text (a CMS announcement, a Mail message body, etc.) line
    by line for "type keyword (+ date)" matches. Returns {"candidates":
    [...], "announcements": [...]} — candidates still need score_candidate()
    to get their confidence; announcements are already final (Seen/Dismiss
    only, no due date, no confidence concept)."""
    if not text:
        return {"candidates": [], "announcements": []}
    if default_year is None:
        default_year = datetime.now().year

    candidates, announcements = [], []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or len(line) > 400:
            continue
        item_type = _classify_type(line)
        if item_type is None:
            continue
        if _looks_like_noise(line):
            continue

        dates = _find_dates_in_line(line, default_year)
        if not dates:
            # A type keyword with zero date-shaped tokens anywhere in the
            # line is not a deadline — a reservation link, a code hint, a
            # course-logistics note. Surfaced as an FYI announcement
            # instead of cluttering the review queue.
            announcements.append({
                "title": line[:140],
                "course": course,
                "type": item_type,
                "source": source,
                "raw_text": line,
            })
            continue

        time_of_day = _find_time_in_line(line)
        ambiguous_phrasing = _looks_ambiguous(line)

        due = None
        if len(dates) == 1 and not ambiguous_phrasing:
            d = dates[0]["date"]
            due = d.replace(hour=time_of_day[0], minute=time_of_day[1]) if time_of_day else d.replace(hour=23, minute=59)

        candidates.append({
            "title": line[:140],
            "course": course,
            "type": item_type,
            "due": due.isoformat() if due else None,
            "source": source,
            "raw_text": line,
            "date_matches": len(dates),
            "explicit_year": dates[0]["explicit_year"] if len(dates) == 1 else False,
            "ambiguous_phrasing": ambiguous_phrasing,
        })
    return {"candidates": candidates, "announcements": announcements}


def score_candidate(candidate: dict) -> dict:
    """Assigns confidence ("high"/"low") + a plain-English reason. Mutates
    and returns the same dict with "confidence"/"confidence_reason" added.
    By construction (see find_candidates_in_text) every candidate reaching
    this function already has at least one date token — a line with none
    became an announcement instead, so "due is None" here means a date
    token existed but couldn't be pinned to one unambiguous moment, which
    is exactly the "date unclear" case, not "no signal at all"."""
    reasons = []
    if candidate.get("due") is None:
        confidence = "low"
        if candidate.get("ambiguous_phrasing"):
            reasons.append("date unclear — phrased as a range/placeholder (e.g. 'week starting', 'TBD')")
        elif candidate.get("date_matches", 0) > 1:
            reasons.append("date unclear — more than one date mentioned on the same line")
        else:
            reasons.append("date unclear")
    elif candidate.get("date_matches", 0) > 1:
        confidence = "low"
        reasons.append("date unclear — more than one date mentioned on the same line")
    else:
        confidence = "high"
        reasons.append("clear type, course, and a single unambiguous dated line")

    candidate["confidence"] = confidence
    candidate["confidence_reason"] = "; ".join(reasons)
    return candidate


def from_exam_seats(exam_seats: list[dict]) -> list[dict]:
    """Portal's Exam Seats table is GUC's own authoritative, already-
    structured final exam schedule — always high confidence, no text
    parsing needed."""
    results = []
    for row in exam_seats:
        course = row.get("Course Name - Season", "").strip()
        date_str = row.get("Date", "")
        start_time = row.get("Start Time", "")
        try:
            date_part = datetime.strptime(date_str, "%d - %B - %Y")
            time_part = datetime.strptime(start_time, "%I:%M:%S %p")
            due = date_part.replace(hour=time_part.hour, minute=time_part.minute)
        except ValueError:
            due = None
        item = {
            "title": f"Final Exam — {course}",
            "course": course,
            "type": "final",
            "due": due.isoformat() if due else None,
            "source": "portal_exam_seats",
            "raw_text": (f"{course} | {row.get('Exam Day', '')} {date_str} "
                         f"{start_time}-{row.get('End Time', '')} | Hall "
                         f"{row.get('Hall', '')} Seat {row.get('Seat', '')}"),
            "date_matches": 1 if due else 0,
            "explicit_year": True,
            "ambiguous_phrasing": False,
        }
        if due is None:
            item["confidence"] = "low"
            item["confidence_reason"] = "exam seat row had an unparseable date/time format"
        else:
            item["confidence"] = "high"
            item["confidence_reason"] = "GUC's own structured exam schedule — course, date, and time all explicit"
        results.append(item)
    return results


def parse_all(cms_results: list[dict] = None, portal_results: dict = None,
               mail_results: list[dict] = None) -> dict:
    """Combines every source into scored candidates + announcements. Any of
    the three inputs may be omitted (None) if that source wasn't scraped
    this cycle. Returns {"candidates": [...], "announcements": [...]}."""
    all_candidates, all_announcements = [], []

    for course_data in (cms_results or []):
        course = course_data.get("course", "")
        result = find_candidates_in_text(
            course_data.get("announcement", ""), course, "cms_announcement"
        )
        for candidate in result["candidates"]:
            all_candidates.append(score_candidate(candidate))
        all_announcements.extend(result["announcements"])

    if portal_results:
        all_candidates.extend(from_exam_seats(portal_results.get("exam_seats", [])))
        # Portal notification TITLES never carry a parseable date (checked
        # against real data: 0/26 title-only matches produced one) and are
        # largely redundant with what CMS announcements + Exam Seats already
        # cover. Their body text isn't scraped (Step 3 only reads the
        # list), so there's nothing more to parse here without opening
        # each one individually — left as a possible future enhancement.

    for msg in (mail_results or []):
        course = ""  # mail rarely states the course cleanly in the subject/body
        text = f"{msg.get('subject', '')}\n{msg.get('body', '')}"
        result = find_candidates_in_text(text, course, "mail")
        for candidate in result["candidates"]:
            all_candidates.append(score_candidate(candidate))
        all_announcements.extend(result["announcements"])

    return {"candidates": all_candidates, "announcements": all_announcements}
