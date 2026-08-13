"""Scrapes the GUC CMS for enrolled courses, each course's announcement
text, and its weekly content items (assignments/quizzes/exams/etc.).

Verified against the real CMS (2026-07-18):
- Enrolled-course links live in the (visually collapsed but DOM-present)
  sidebar nav on the CMS home page: `a[href*='CourseViewStn.aspx']`.
- Each course page has ONE rich-text announcement block in a stable,
  ASP.NET-server-control-ID'd container:
  `#ContentPlaceHolderright_ContentPlaceHoldercontent_desc`. This is where
  instructors post FAQs, deadline changes, links, etc. — free text, not
  structured, so parsing/confidence-scoring happens downstream (not here).
- Weekly content items live in `.weeksdata` cards, each with a
  "WEEK: YYYY-M-D" header and one `div[id^='content']` per item; each
  item's `<strong>` text looks like "3 - Practice Assignment 10
  (Assignment )" — a free-text title that already includes GUC's own
  type tag (Assignment / Exam / Quiz / *Solution variants) in parens.

This module only returns RAW scraped data — no parsing, confidence
scoring, deduplication, or writing to the tracker/calendar happens here.
"""

from kareem.guc import auth

CMS_BASE = "https://cms.guc.edu.eg"


def get_enrolled_courses(page) -> list[dict]:
    """Visits the CMS home page and returns [{"name", "url"}] for each
    enrolled course, with FULL (untruncated) names.

    The "Courses enrolled in:" table's rows are [_, full_name, status,
    season, id, sid] — the sidebar nav has the same courses as real links
    but CSS-truncates their visible text, so the table is the name source
    and the table's own id/sid cells build the URL directly (no need to
    cross-reference the sidebar links at all)."""
    page.goto(auth.CMS_HOME_URL, wait_until="networkidle", timeout=25000)
    courses = []
    for row in page.locator("table tr").all():
        cells = row.locator("td").all()
        if len(cells) != 6:
            continue
        try:
            texts = [c.inner_text().strip() for c in cells]
        except Exception:
            continue
        name, status, season, course_id, sid = texts[1], texts[2], texts[3], texts[4], texts[5]
        if not (name and course_id.isdigit() and sid.isdigit()):
            continue
        courses.append({
            "name": name,
            "status": status,
            "season": season,
            "url": f"/apps/student/CourseViewStn.aspx?id={course_id}&sid={sid}",
        })
    return courses


def get_course_announcement(page, course_url: str) -> str:
    """Navigates to a course page and returns its announcement block's raw
    text (empty string if the course has none)."""
    full_url = course_url if course_url.startswith("http") else f"{CMS_BASE}{course_url}"
    page.goto(full_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(1500)
    try:
        el = page.locator("#ContentPlaceHolderright_ContentPlaceHoldercontent_desc").first
        return el.inner_text().strip()
    except Exception:
        return ""


def get_course_content_items(page) -> list[dict]:
    """Reads the currently-loaded course page's weekly content list.
    Call right after get_course_announcement() (same page, no extra nav)."""
    items = []
    weeks = page.locator(".weeksdata").all()
    for week in weeks:
        try:
            header = week.locator("text=/WEEK:/i").first.inner_text().strip()
        except Exception:
            header = ""
        content_divs = week.locator("div[id^='content']").all()
        for div in content_divs:
            try:
                title = div.locator("strong").first.inner_text().strip()
            except Exception:
                continue
            if title:
                items.append({"week": header, "title": title})
    return items


def scrape_cms() -> list[dict]:
    """One CMS login, then one pass over every enrolled course. Returns
    [{"course", "url", "announcement", "content_items"}]. Raises
    RuntimeError (without retrying) if login itself fails — callers are
    responsible for backing off, not looping here."""
    if not auth.credentials_configured():
        raise RuntimeError("GUC_USERNAME/GUC_PASSWORD aren't set in .env.")

    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx = auth.new_ntlm_context(browser, "cms")
        except Exception as exc:
            browser.close()
            raise RuntimeError(f"CMS login failed: {exc}") from exc
        page = ctx.new_page()
        try:
            courses = get_enrolled_courses(page)
            for course in courses:
                announcement = get_course_announcement(page, course["url"])
                content_items = get_course_content_items(page)
                results.append({
                    "course": course["name"],
                    "url": course["url"],
                    "announcement": announcement,
                    "content_items": content_items,
                })
        finally:
            ctx.close()
            browser.close()
    return results
