"""GUC login plumbing for CMS, the Student Portal, and Mail/OWA.

Verified against the real GUC systems (2026-07-18):
- CMS and the Student Portal use IIS Windows Integrated Authentication
  (WWW-Authenticate: Negotiate / NTLM) — there is no HTML login form at all.
  Playwright/Chromium handles the NTLM handshake automatically when the
  browser context is created with `http_credentials`; every request in that
  context is then transparently authenticated.
- Mail/OWA uses a normal HTML form (`#username`, `#password`, POST to
  `/owa/auth.owa`), but the visible "Sign in" control is a styled
  `<div class="signinbutton" onclick="clkLgn()">` layered OVER the real
  (hidden) `<input type=submit>` — click the div, not the input, or the
  click is silently swallowed by the overlay.

Sessions on all three systems are short-lived, so every check cycle logs in
fresh; nothing is persisted across runs. Credentials come only from
GUC_USERNAME / GUC_PASSWORD in .env and are never logged or written anywhere.

Login cooldown: logging into the same GUC system repeatedly in a short
window is exactly the pattern GUC's own monitoring (and Exchange's own
per-mailbox session cap) is built to catch — we hit that cap for real while
building this, from a burst of manual test logins. MIN_LOGIN_INTERVAL_SECONDS
below is a hard floor enforced here in code (persisted to a small state
file, so it holds across separate runs too, not just within one process) —
callers cannot accidentally bypass it by retrying quickly, whether that
retry comes from a bug, an impatient click, or repeated manual testing.
"""

import json
import os
import time
from pathlib import Path

CMS_HOME_URL = "https://cms.guc.edu.eg/apps/student/HomePageStn"
PORTAL_URL = "https://apps.guc.edu.eg/student_ext/index.aspx"
MAIL_URL = "https://mail.guc.edu.eg/owa/#path=/mail/msgfolderroot"

MIN_LOGIN_INTERVAL_SECONDS = 60

_STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "guc_login_state.json"


def _credentials():
    """Read GUC credentials from the environment. Never cache/log them."""
    from dotenv import load_dotenv
    load_dotenv()
    return os.getenv("GUC_USERNAME"), os.getenv("GUC_PASSWORD")


def credentials_configured() -> bool:
    username, password = _credentials()
    return bool(username) and bool(password)


def _read_login_times() -> dict:
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _enforce_cooldown(system: str) -> None:
    """Raises RuntimeError (never sleeps/retries) if `system` was logged
    into too recently. Records this attempt's timestamp either way, so a
    burst of calls can't each slip in under the wire."""
    times = _read_login_times()
    now = time.time()
    last = times.get(system)
    times[system] = now
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(times), encoding="utf-8")
    except OSError:
        pass  # best-effort persistence; never block a login over a disk hiccup

    if last is not None:
        elapsed = now - last
        if elapsed < MIN_LOGIN_INTERVAL_SECONDS:
            wait_left = MIN_LOGIN_INTERVAL_SECONDS - elapsed
            raise RuntimeError(
                f"Refusing to log into GUC {system} again so soon "
                f"(last attempt {elapsed:.0f}s ago; minimum gap is "
                f"{MIN_LOGIN_INTERVAL_SECONDS}s). Wait about {wait_left:.0f}s "
                "and try again — this cooldown exists because repeated "
                "logins in a short window are exactly what GUC's account "
                "monitoring and Exchange's session limits are built to "
                "catch."
            )


def new_ntlm_context(browser, system: str):
    """A browser context authenticated for CMS/Portal (NTLM/Negotiate).
    `system` is "cms" or "portal" — used only to key the login cooldown."""
    username, password = _credentials()
    if not username or not password:
        raise RuntimeError(
            "GUC_USERNAME/GUC_PASSWORD aren't set in .env — see .env.example."
        )
    _enforce_cooldown(system)
    return browser.new_context(http_credentials={"username": username, "password": password})


def login_mail(browser):
    """Log into Mail/OWA via its real HTML form. Returns (context, page)
    already on the logged-in inbox. Raises RuntimeError on failure —
    callers should NOT retry in a loop; surface the failure and back off."""
    username, password = _credentials()
    if not username or not password:
        raise RuntimeError(
            "GUC_USERNAME/GUC_PASSWORD aren't set in .env — see .env.example."
        )
    _enforce_cooldown("mail")
    ctx = browser.new_context()
    page = ctx.new_page()
    try:
        page.goto(MAIL_URL, wait_until="networkidle", timeout=25000)
        page.fill("#username", username)
        page.fill("#password", password)
        page.click(".signinbutton")
        page.wait_for_load_state("networkidle", timeout=25000)
        page.wait_for_timeout(1500)
        if "errorfe.aspx" in page.url or "SessionLimit" in page.url or "TooManyObjectsOpened" in page.url:
            raise RuntimeError(
                "Exchange refused the login with a session-limit error "
                "(too many recent sessions on this mailbox) — this usually "
                "clears on its own within a few minutes. Do not retry "
                "immediately."
            )
        if "auth/logon.aspx" in page.url or "/owa/" not in page.url:
            raise RuntimeError("Mail/OWA login did not reach the inbox — check credentials.")
    except Exception as exc:
        ctx.close()
        raise RuntimeError(f"Mail/OWA login failed: {exc}") from exc
    return ctx, page
