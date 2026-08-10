"""Scrapes the GUC Student Portal's Financial Balance page for outstanding
payment requests.

Verified against the real Portal (2026-07-18):
- `Financial/BalanceView_001.aspx` has one clean table
  (`#ContentPlaceHolderright_ContentPlaceHoldercontent_DG_PaymentRequest`)
  with columns: Reference | PaymentDescription | Currency | Amount |
  DueDate [Deadine] (GUC's own typo, not ours — kept verbatim as the real
  header text in case anything ever keys off it) | (blank — a per-row "Pay"
  action link, not scraped). `Reference` is a stable, unique GUC-issued id
  per payment request (e.g. "BERQTE-0000113981") — the natural dedupe key,
  far more reliable than fuzzy text matching used elsewhere in guc/.

This module only returns RAW scraped data — no writing to the tracker
happens here (that's guc/sync.py's job, same separation as the other
scrapers).
"""

from jarvis.guc import auth

BALANCE_URL = "https://apps.guc.edu.eg/student_ext/Financial/BalanceView_001.aspx"
_TABLE_SELECTOR = "#ContentPlaceHolderright_ContentPlaceHoldercontent_DG_PaymentRequest"


def get_payment_requests(page) -> list[dict]:
    """Returns each outstanding payment request row, e.g. {"Reference":
    "BERQTE-0000113981", "PaymentDescription": "BERLIN QTE", "Currency":
    "EUR", "Amount": "960", "DueDate [Deadine]": "Sunday,25-10-2026"}."""
    page.goto(BALANCE_URL, wait_until="networkidle", timeout=25000)
    page.wait_for_timeout(1000)
    table = page.locator(_TABLE_SELECTOR).first
    rows = table.locator("tr").all()
    if not rows:
        return []
    headers = [c.strip() for c in rows[0].locator("th, td").all_inner_texts()]
    if not any(headers):
        return []
    results = []
    for row in rows[1:]:
        cells = [c.strip() for c in row.locator("td").all_inner_texts()]
        if not cells or not any(cells):
            continue
        entry = dict(zip(headers, cells))
        if entry.get("Reference"):
            results.append(entry)
    return results


def scrape_finances() -> list[dict]:
    """One Portal login, then reads the payment-requests table. Returns
    the raw rows from get_payment_requests(). Raises RuntimeError (without
    retrying) if login fails — callers back off, not this function."""
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
            rows = get_payment_requests(page)
        finally:
            ctx.close()
            browser.close()
    return rows
