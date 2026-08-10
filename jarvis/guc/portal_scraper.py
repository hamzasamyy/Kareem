"""Scrapes the GUC Student Portal for the final exam schedule and the
portal's own notification feed.

Verified against the real Portal (2026-07-18):
- Exam Seats (`Exam/ViewExamSeat_01.aspx`) has one clean table (`#Table2`)
  with columns: Course Name - Season | Exam Day | Date | Start Time |
  End Time | Hall | Seat | Exam Type. This is GUC's own authoritative final
  exam schedule — structured, not free text, so it's the highest-confidence
  source Jarvis has for final exam deadlines.
- Notifications (`Main/Notifications.aspx`) has one table
  (`#...GridViewdata`) with columns: Actions | Title | Date | Staff |
  Importance — real descriptive titles (e.g. "Resend :: Final Exam
  Duration and Content"), unlike CMS's own notification feed which only
  says an announcement "has been updated" without the actual text.
- The weekly class-schedule page (`Scheduling/GroupSchedule.aspx`) was
  checked too but only has recurring class times, not one-off deadlines —
  out of scope for deadline detection, not scraped here.

This module only returns RAW scraped data — no parsing, confidence
scoring, deduplication, or writing to the tracker/calendar happens here.
"""

from jarvis.guc import auth

EXAM_SEATS_URL = "https://apps.guc.edu.eg/student_ext/Exam/ViewExamSeat_01.aspx"
NOTIFICATIONS_URL = "https://apps.guc.edu.eg/student_ext/Main/Notifications.aspx"


def _table_to_dicts(page, table_selector: str) -> list[dict]:
    """Generic ASP.NET GridView reader: first row = headers, rest = data."""
    table = page.locator(table_selector).first
    rows = table.locator("tr").all()
    if not rows:
        return []
    headers = [c.strip() for c in rows[0].locator("th, td").all_inner_texts()]
    if not any(headers):
        return []
    results = []
    for row in rows[1:]:
        cells = row.locator("td").all_inner_texts()
        cells = [c.strip() for c in cells]
        if not cells or not any(cells):
            continue
        results.append(dict(zip(headers, cells)))
    return results


def get_exam_seats(page) -> list[dict]:
    """Returns each row of the Exam Seats table, e.g. {"Course Name -
    Season", "Exam Day", "Date", "Start Time", "End Time", "Hall", "Seat",
    "Exam Type"}."""
    page.goto(EXAM_SEATS_URL, wait_until="networkidle", timeout=25000)
    page.wait_for_timeout(1000)
    return _table_to_dicts(page, "#Table2")


def get_notifications(page) -> list[dict]:
    """Returns each row of the portal's notification feed, e.g. {"Actions",
    "Title", "Date", "Staff", "Importance"}."""
    page.goto(NOTIFICATIONS_URL, wait_until="networkidle", timeout=25000)
    page.wait_for_timeout(1000)
    return _table_to_dicts(page, "table.dataTable")


def scrape_portal() -> dict:
    """One Portal login, then reads exam seats + notifications. Returns
    {"exam_seats": [...], "notifications": [...]}. Raises RuntimeError
    (without retrying) if login fails — callers back off, not this
    function."""
    if not auth.credentials_configured():
        raise RuntimeError("GUC_USERNAME/GUC_PASSWORD aren't set in .env.")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = auth.new_ntlm_context(browser, "portal")
        except Exception as exc:
            browser.close()
            raise RuntimeError(f"Portal login failed: {exc}") from exc
        page = ctx.new_page()
        try:
            exam_seats = get_exam_seats(page)
            notifications = get_notifications(page)
        finally:
            ctx.close()
            browser.close()
    return {"exam_seats": exam_seats, "notifications": notifications}
