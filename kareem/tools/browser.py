"""
General-purpose browser control (Layer 1) — lets the brain drive ANY
website by describing what it wants ("the search box", "the Sign In
button") rather than needing exact selectors or pixel coordinates.
Element-finding goes through Playwright's accessibility-tree locators
(get_by_role/get_by_label/get_by_text/...), which is far more reliable
than screenshot-based clicking — vision is Layer 3, a last resort, not
how this module works.

One Playwright browser/page persists across separate tool calls within
(and across) a conversation, so "open gmail" then later "click compose"
see the same page. Playwright's sync API is bound to the OS thread that
created it, but kareem/web/server.py spawns a NEW worker thread per chat
turn — so all real Playwright calls are funneled onto one dedicated
background thread (_BrowserWorker below) via a tiny call-and-wait queue,
regardless of which turn/thread issues the tool call. Runs headed
(a visible Chromium window) by default so the user can see what Kareem
is doing and step in if something looks wrong.
"""

import queue
import threading
from urllib.parse import quote

from kareem.safety import log_action
from kareem.tools import register

# Common site names -> URL, so "open gmail" doesn't need the user to say
# a full URL. Anything not listed here is treated as a URL/domain (Layer 1
# accepts a direct URL too) or, failing that, searched for on Google.
SITE_URLS = {
    "google": "https://www.google.com",
    "gmail": "https://mail.google.com",
    "youtube": "https://www.youtube.com",
    "github": "https://github.com",
    "amazon": "https://www.amazon.com",
    "wikipedia": "https://www.wikipedia.org",
    "reddit": "https://www.reddit.com",
    "twitter": "https://x.com",
    "x": "https://x.com",
    "linkedin": "https://www.linkedin.com",
    "outlook": "https://outlook.live.com",
    "netflix": "https://www.netflix.com",
    "maps": "https://maps.google.com",
    "google maps": "https://maps.google.com",
    "drive": "https://drive.google.com",
    "google drive": "https://drive.google.com",
    "facebook": "https://www.facebook.com",
    "instagram": "https://www.instagram.com",
}


class _BrowserWorker:
    """Owns the one Playwright browser/page. See module docstring for why
    every call is funneled onto a single dedicated thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._q = queue.Queue()
        self._thread = None
        self._start_error = None

    def _ensure_started(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._start_error = None
            ready = threading.Event()
            self._thread = threading.Thread(
                target=self._run, args=(ready,), daemon=True, name="kareem-browser"
            )
            self._thread.start()
            if not ready.wait(timeout=30):
                raise RuntimeError("The browser thread did not start within 30 seconds.")
        if self._start_error:
            raise RuntimeError(self._start_error)

    def _run(self, ready: threading.Event):
        page = None
        try:
            from playwright.sync_api import sync_playwright

            playwright = sync_playwright().start()
            browser = playwright.chromium.launch(headless=False)
            page = browser.new_page()
        except BaseException as e:
            self._start_error = (
                f"Couldn't start the browser ({e}). "
                "Fix: pip install playwright && playwright install chromium"
            )
            ready.set()
            return
        ready.set()
        while True:
            func, result_box, done = self._q.get()
            try:
                result_box["value"] = func(page)
            except BaseException as e:
                # Always deliver completion, including for non-Exception
                # failures such as SystemExit or KeyboardInterrupt.
                result_box["error"] = e
            finally:
                done.set()

    def call(self, func, timeout=30):
        """Run func(page) on the dedicated browser thread and return its
        result (or re-raise its exception) on the calling thread."""
        self._ensure_started()
        result_box = {}
        done = threading.Event()
        self._q.put((func, result_box, done))
        if not done.wait(timeout=timeout):
            raise RuntimeError("The browser action timed out.")
        if "error" in result_box:
            raise result_box["error"]
        return result_box.get("value")


_worker = _BrowserWorker()


def _resolve_url(url_or_site: str) -> str:
    value = url_or_site.strip()
    lowered = value.lower()
    if lowered in SITE_URLS:
        return SITE_URLS[lowered]
    if value.startswith("http://") or value.startswith("https://"):
        return value
    if "." in value and " " not in value:
        return f"https://{value}"
    return f"https://www.google.com/search?q={quote(value)}"


_EDITABLE_ROLES = ("textbox", "searchbox", "combobox", "spinbutton")
_CLICKABLE_ROLES = ("button", "link", "checkbox", "radio", "menuitem", "tab")


def _find_locator(page, description: str, prefer: str = "any"):
    """Try several accessibility-tree strategies and return the first that
    matches at least one element (locator, how many matched). None, 0 if
    nothing matched any strategy.

    `prefer` controls role search order: "editable" tries text-input-like
    roles FIRST (for browser_type — a same-named icon/decoration button
    next to a real input, e.g. a magnifying-glass "Search" button beside a
    "Search" textbox, must never win over the actual field); "clickable"
    tries button/link-like roles first (for browser_click); "any" is the
    original click-biased order, used by browser_read."""
    description = description.strip()
    role_order = {
        "editable": _EDITABLE_ROLES + _CLICKABLE_ROLES,
        "clickable": _CLICKABLE_ROLES + _EDITABLE_ROLES,
        "any": _CLICKABLE_ROLES + _EDITABLE_ROLES,
    }[prefer]
    strategies = [lambda role=role: page.get_by_role(role, name=description, exact=False)
                  for role in role_order]
    strategies += [
        lambda: page.get_by_label(description, exact=False),
        lambda: page.get_by_placeholder(description, exact=False),
        lambda: page.get_by_text(description, exact=False),
    ]
    for strategy in strategies:
        try:
            locator = strategy()
            count = locator.count()
        except Exception:
            continue
        if count == 0:
            continue
        # A description often matches several DOM elements (e.g. a hidden
        # mobile-nav duplicate, or several same-named links) — DOM order
        # isn't a reliable tiebreaker, so prefer the first one that's
        # actually VISIBLE on screen right now over locator.first's blind
        # document order. Checked up to 10 to prevent pathological pages
        # from doing this scan against hundreds of matches.
        for i in range(min(count, 10)):
            candidate = locator.nth(i)
            try:
                if candidate.is_visible(timeout=1000):
                    return candidate, count
            except Exception:
                continue
        return locator.first, count
    return None, 0


@register({
    "name": "browser_open",
    "description": (
        "Open a website in Kareem's browser — a URL, a domain, or a common "
        "site name ('gmail', 'youtube', 'github'). Reuses the same browser "
        "tab across calls, so a later browser_click/browser_type/browser_read "
        "acts on whatever page this navigated to. Read-only/navigation, no "
        "confirmation needed. "
        "This opens a real, visible browser window and is slow — NOT for "
        "a plain lookup (a fact, a score, an article): use web_search + "
        "fetch_page for that instead. Reserve browser_open for when the "
        "task needs interaction afterward (clicking, typing, a login "
        "flow) or content fetch_page can't read."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url_or_site": {
                "type": "string",
                "description": "A URL, domain, or common site name to open",
            },
        },
        "required": ["url_or_site"],
    },
})
def browser_open(url_or_site: str) -> str:
    log_action("browser_open", url_or_site)
    target = _resolve_url(url_or_site)

    def _do(page):
        page.goto(target, wait_until="domcontentloaded", timeout=20000)
        return page.title()

    try:
        title = _worker.call(_do)
        return f"Opened {target} — page title: {title!r}"
    except Exception as e:
        return f"Couldn't open {url_or_site}: {e}"


@register({
    "name": "browser_click",
    "description": (
        "Click something on the currently open page, described in plain "
        "language (e.g. 'the Sign In button'). Finds it via accessible "
        "roles/labels/text, not pixel coordinates — call browser_open first "
        "if no page is open. Try vision_click as a last resort if this "
        "can't find the target. Asks the user to confirm first for send/"
        "submit/delete-looking clicks; refuses outright for financial "
        "transactions, permanent deletion, or a CAPTCHA/bot-check."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Plain-language description of what to click",
            },
        },
        "required": ["description"],
    },
})
def browser_click(description: str) -> str:
    from kareem.safety import classify_click_intent, guard, refuse_if_hard_blocked_click

    blocked = refuse_if_hard_blocked_click("browser_click", description)
    if blocked:
        return blocked

    def _do_click():
        def _do(page):
            locator, count = _find_locator(page, description, prefer="clickable")
            if locator is None:
                return None
            locator.scroll_into_view_if_needed(timeout=5000)
            locator.click(timeout=5000)
            return count

        try:
            count = _worker.call(_do)
        except Exception as e:
            return f"Couldn't click '{description}': {e}"
        if count is None:
            return (
                f"Couldn't find anything matching '{description}' on the "
                "current page via its accessible roles/labels/text."
            )
        extra = f" (matched {count} elements, used the first)" if count > 1 else ""
        return f"Clicked '{description}'.{extra}"

    if classify_click_intent(description) == "risky":
        return guard(
            "web_action_click",
            f"Click '{description}' on the current page (looks like it "
            "sends, submits, confirms, or deletes something)",
            _do_click,
        )
    log_action("browser_click", description)
    return _do_click()


@register({
    "name": "browser_type",
    "description": (
        "Type text into a field on the currently open page, described in "
        "plain language (e.g. 'the search box', 'the email field'). Finds "
        "it the same way as browser_click. This only fills the field — it "
        "does NOT submit/send anything by itself; call browser_click on "
        "the actual submit/send/search button afterward for that. NEVER "
        "use this to enter a password, card number, bank account, or any "
        "other credential into a website's own login/payment form — that "
        "is refused outright, not just confirmed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Plain-language description of the field to type into",
            },
            "text": {"type": "string", "description": "The text to type"},
        },
        "required": ["description", "text"],
    },
})
def browser_type(description: str, text: str) -> str:
    from kareem.safety import refuse_if_credential_like

    blocked = refuse_if_credential_like("browser_type", description, text)
    if blocked:
        return blocked

    log_action("browser_type", f"{description!r} <- {'*' * len(text)}")

    def _do(page):
        locator, count = _find_locator(page, description, prefer="editable")
        if locator is None:
            return None
        locator.scroll_into_view_if_needed(timeout=5000)
        locator.click(timeout=5000)
        locator.fill(text, timeout=5000)
        return count

    try:
        count = _worker.call(_do)
    except Exception as e:
        return f"Couldn't type into '{description}': {e}"
    if count is None:
        return f"Couldn't find a field matching '{description}' on the current page."
    return f"Typed into '{description}'."


@register({
    "name": "browser_read",
    "description": (
        "Read visible text from the currently open page — either one "
        "described element ('the price', 'the article headline') or the "
        "whole page if description is omitted. Use this to check what's "
        "actually on the page before deciding what to click/type next."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "What to read; omit for the whole visible page",
            },
        },
        "required": [],
    },
})
def browser_read(description: str = "") -> str:
    log_action("browser_read", description or "(whole page)")

    def _do(page):
        if not description or description.strip().lower() in ("page", "whole page", "everything", ""):
            return page.inner_text("body")
        locator, count = _find_locator(page, description)
        if locator is None:
            return None
        return locator.inner_text(timeout=5000)

    try:
        text = _worker.call(_do)
    except Exception as e:
        return f"Couldn't read '{description}': {e}"
    if text is None:
        return f"Couldn't find anything matching '{description}' to read."
    text = " ".join(text.split())
    limit = 3000
    if len(text) > limit:
        text = text[:limit] + " …[truncated]"
    return text or "(no visible text found)"
