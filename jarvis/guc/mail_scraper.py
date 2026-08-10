"""Scans recent Mail/OWA messages for assignment/quiz/final-related
announcements.

Verified against the real Mail/OWA (2026-07-18): GUC's OWA serves the
"Light" (table-based) UI, not the modern SPA — much easier to scrape
reliably. The inbox message list is `table.lvw`, one `<tr>` per message,
with From/Subject/Received/Size columns. Each subject is an
`<a onclick="onClkRdMsg(...)">` (not a real href) — Playwright has to
actually click it to open the message; the opened message's sender,
sent-date, and body then render as plain page text.

To avoid opening every message (slow, and unnecessary Exchange load), this
scans the cheap subject list first and only opens the ones whose subject
already looks relevant. Recent inbox messages only (OWA Light's default
page size), matching "scan recent messages" in scope — not a full mailbox
crawl.

This module only returns RAW scraped data — no parsing, confidence
scoring, deduplication, or writing to the tracker/calendar happens here.
"""

from jarvis.guc import auth

KEYWORDS = ["deadline", "due", "quiz", "final", "assignment", "exam", "submission", "milestone"]


def _subject_looks_relevant(subject: str) -> bool:
    lowered = subject.lower()
    return any(k in lowered for k in KEYWORDS)


def list_inbox_subjects(page) -> list[dict]:
    """Reads the inbox message-list table without opening anything.
    Returns [{"from", "subject", "received"}]."""
    rows = page.locator("table.lvw tr").all()
    messages = []
    for row in rows:
        try:
            text = row.inner_text().strip()
        except Exception:
            continue
        if not text or text.startswith("From"):
            continue  # header row
        parts = [p.strip() for p in text.split("\t") if p.strip()]
        if len(parts) < 2:
            continue
        messages.append({
            "from": parts[0],
            "subject": parts[1],
            "received": parts[2] if len(parts) > 2 else "",
        })
    return messages


def open_message_body(page, subject: str) -> str:
    """Clicks the first inbox row matching `subject` and returns the
    opened message's full page text (sender/date/body all included —
    downstream parsing pulls out what it needs)."""
    link = page.locator("table.lvw a[onclick*='onClkRdMsg']", has_text=subject).first
    link.click()
    page.wait_for_timeout(1500)
    return page.locator("body").inner_text()


def scrape_mail() -> list[dict]:
    """One Mail/OWA login, then reads recent inbox subjects and opens the
    body of every keyword-relevant one. Returns [{"from", "subject",
    "received", "body"}]. Raises RuntimeError (without retrying) if login
    fails — callers back off, not this function."""
    if not auth.credentials_configured():
        raise RuntimeError("GUC_USERNAME/GUC_PASSWORD aren't set in .env.")

    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            ctx, page = auth.login_mail(browser)
        except Exception as exc:
            browser.close()
            raise RuntimeError(f"Mail login failed: {exc}") from exc
        try:
            messages = list_inbox_subjects(page)
            for msg in messages:
                if not _subject_looks_relevant(msg["subject"]):
                    continue
                # Opening a message replaces the list view with the reading
                # pane, so table.lvw's rows are gone until we go back to the
                # inbox — re-navigate before every open, not just the first.
                page.goto(auth.MAIL_URL, wait_until="networkidle", timeout=25000)
                page.wait_for_timeout(1000)
                body = open_message_body(page, msg["subject"])
                results.append({**msg, "body": body})
        finally:
            ctx.close()
            browser.close()
    return results
