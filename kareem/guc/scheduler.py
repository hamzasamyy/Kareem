"""Runs one GUC "check cycle" — scrape CMS/Portal/Mail, parse, sync — and
a background thread that repeats it every GUC_CHECK_INTERVAL_MINUTES
(randomized +/-5 min so it isn't perfectly robotic), plus a small
persisted history of past cycles and per-system login status for the web
UI (Step 6).

Reuses guc/{cms_scraper,portal_scraper,mail_scraper,parser,sync}.py
entirely — this module only orchestrates them and remembers what happened
across cycles. Each system's scrape is isolated in its own try/except:
one system being down (or hitting its own login cooldown from guc/auth.py)
never stops the other two from being checked.
"""

import json
import random
import threading
from datetime import datetime
from pathlib import Path

from kareem import config, session_log, trackers
from kareem.guc import auth, cms_scraper, portal_scraper, mail_scraper, finance_scraper, parser, sync

_HISTORY_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "guc_check_history.json"
_MAX_HISTORY = 20

_lock = threading.Lock()
_state = {
    "last_checked": None,
    "login_status": {"cms": None, "portal": None, "mail": None},  # None=never tried
    "login_errors": {"cms": None, "portal": None, "mail": None},
    "in_progress": False,
}


def _read_history() -> list[dict]:
    try:
        return json.loads(_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _append_history(entry: dict) -> None:
    history = _read_history()
    history.append(entry)
    history = history[-_MAX_HISTORY:]
    try:
        _HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HISTORY_PATH.write_text(json.dumps(history), encoding="utf-8")
    except OSError:
        pass  # best-effort; never let history persistence break a check


def _record_login(system: str, ok: bool, error: str | None) -> None:
    with _lock:
        _state["login_status"][system] = ok
        _state["login_errors"][system] = error


def get_status() -> dict:
    """Snapshot for the web UI: last-checked time, per-system login
    status/errors, whether a check is currently running, and recent
    check-cycle history (newest last)."""
    with _lock:
        snapshot = {
            "last_checked": _state["last_checked"],
            "login_status": dict(_state["login_status"]),
            "login_errors": dict(_state["login_errors"]),
            "in_progress": _state["in_progress"],
        }
    snapshot["history"] = _read_history()
    return snapshot


def _scrape_portal_and_finances() -> tuple[dict, list[dict]]:
    """One Portal NTLM login, reused for exam seats + notifications
    (portal_scraper) AND the payment-requests table (finance_scraper) —
    see the comment in run_check_cycle() for why this can't be two
    separate scrape_*() calls. Raises RuntimeError on login failure,
    exactly like the standalone scrapers do."""
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
            exam_seats = portal_scraper.get_exam_seats(page)
            notifications = portal_scraper.get_notifications(page)
            finance_rows = finance_scraper.get_payment_requests(page)
        finally:
            ctx.close()
            browser.close()
    return {"exam_seats": exam_seats, "notifications": notifications}, finance_rows


def run_check_cycle() -> dict:
    """One full check cycle. Returns a summary dict and, on success, also
    records it to the persisted history and logs it via session_log
    (same pattern as other Kareem activity). Never raises — every failure
    mode (missing credentials, one system's login failing, a scraping
    error) is caught and reported in the returned dict instead."""
    if not auth.credentials_configured():
        return {"ok": False, "error": "GUC_USERNAME/GUC_PASSWORD not set in .env"}

    with _lock:
        if _state["in_progress"]:
            return {"ok": False, "error": "a GUC check is already running"}
        _state["in_progress"] = True

    started = datetime.now().astimezone()
    cms_results, portal_results, mail_results = None, None, None
    finance_rows = []
    finance_summary = {"added": 0, "skipped_duplicates": 0, "failures": 0}
    systems_checked = []

    try:
        try:
            cms_results = cms_scraper.scrape_cms()
            _record_login("cms", True, None)
            systems_checked.append("cms")
        except Exception as exc:
            _record_login("cms", False, str(exc))

        # One Portal login covers exam seats + notifications + finances —
        # calling scrape_portal() and finance_scraper.scrape_finances()
        # back-to-back would each try their own login and the second would
        # hit guc/auth.py's per-system cooldown and fail for no real reason.
        try:
            portal_results, finance_rows = _scrape_portal_and_finances()
            _record_login("portal", True, None)
            systems_checked.append("portal")
        except Exception as exc:
            _record_login("portal", False, str(exc))

        try:
            mail_results = mail_scraper.scrape_mail()
            _record_login("mail", True, None)
            systems_checked.append("mail")
        except Exception as exc:
            _record_login("mail", False, str(exc))

        parsed = parser.parse_all(
            cms_results=cms_results, portal_results=portal_results, mail_results=mail_results,
        )
        candidates = parsed["candidates"]
        announcements = parsed["announcements"]
        sync_summary = sync.sync_candidates(candidates)
        announcement_summary = sync.sync_announcements(announcements)
        if finance_rows:
            finance_summary = sync.sync_finances(finance_rows)

        finished = datetime.now().astimezone()
        entry = {
            "time": finished.isoformat(),
            "systems_checked": systems_checked,
            "items_found": len(candidates),
            "items_auto_added": sync_summary.get("auto_added", 0),
            "items_flagged": sync_summary.get("flagged_for_review", 0),
            "announcements_added": announcement_summary.get("added", 0),
            "finance_items_added": finance_summary.get("added", 0),
            "finance_items_updated": finance_summary.get("updated", 0),
            "duration_seconds": round((finished - started).total_seconds(), 1),
        }
        _append_history(entry)

        with _lock:
            _state["last_checked"] = finished.isoformat()

        try:
            session_log.log_event("guc_check", **entry)
        except Exception:
            pass  # logging must never break the actual check

        return {"ok": True, **entry, "calendar_available": sync_summary.get("calendar_available")}
    finally:
        with _lock:
            _state["in_progress"] = False


_stop_event = None
_thread = None


def _loop(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        interval_minutes = getattr(config, "GUC_CHECK_INTERVAL_MINUTES", 45)
        jitter_minutes = random.uniform(-5, 5)
        delay_seconds = max(60.0, (interval_minutes + jitter_minutes) * 60)
        if stop_event.wait(delay_seconds):
            break
        try:
            run_check_cycle()
        except Exception as exc:
            print(f"(GUC background check failed: {exc})")


def start_background_loop() -> bool:
    """Starts the periodic check loop as a daemon thread (waits one full
    interval before the first automatic check — the "Check now" button
    covers the immediate case). Safe to call even without GUC credentials
    configured; each cycle just reports that and skips. Returns False if
    a loop is already running."""
    global _stop_event, _thread
    if _thread is not None and _thread.is_alive():
        return False
    _stop_event = threading.Event()
    _thread = threading.Thread(
        target=_loop, args=(_stop_event,), daemon=True, name="kareem-guc-checker"
    )
    _thread.start()
    return True
