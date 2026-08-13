"""
Safety gate — every risky/destructive action Jarvis can take must be routed
through `confirm()` in this file before it runs. This is the single place
that decides what counts as "risky" and how confirmation is collected
(typed y/n for text mode, spoken "yes" for voice mode).

Edit RISKY_PATTERNS to change what Jarvis treats as dangerous.
"""

import logging
from pathlib import Path

from jarvis import config

# ----------------------------------------------------------------------------
# Logging — every action (safe or risky, confirmed or declined) is recorded.
# ----------------------------------------------------------------------------

_log_path = Path(__file__).resolve().parent.parent / config.LOG_FILE
logger = logging.getLogger("jarvis")
logger.setLevel(logging.INFO)
logger.propagate = False  # keep library noise (httpx etc.) out of jarvis.log
if not logger.handlers:
    # utf-8-sig so Notepad and PowerShell display the log correctly
    _handler = logging.FileHandler(_log_path, encoding="utf-8-sig")
    _handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(_handler)


def log_action(action: str, detail: str = "", confirmed: bool | None = None):
    """Write a line to jarvis.log describing an action Jarvis took (or was asked to take)."""
    status = ""
    if confirmed is True:
        status = " [CONFIRMED]"
    elif confirmed is False:
        status = " [DECLINED]"
    logger.info("%s%s%s", action, f" — {detail}" if detail else "", status)


# ----------------------------------------------------------------------------
# Risk classification
# ----------------------------------------------------------------------------

# Categories of action that always require explicit user confirmation before
# running, regardless of SAFETY_LEVEL. Add/remove entries here to change
# what Jarvis treats as "risky" — this list is meant to be edited by you.
RISKY_ACTIONS = {
    "delete_file": "delete a file or folder",
    "delete_calendar_event": "delete a Google Calendar event",
    "move_file": "move or rename a file or folder",
    "run_shell_command": "run a shell/system command",
    "shutdown_or_restart": "shut down or restart the computer",
    "edit_registry": "edit the Windows registry",
    "run_downloaded_file": "execute a downloaded or unknown program",
    "run_code": "execute a Python code snippet",
    "run_claude_code": "delegate a coding task to Claude Code (it can edit files and run commands)",
    "web_action_click": "click something on a website that looks like it sends/submits/confirms/deletes something",
    "app_action_click": "click something in a desktop app that looks like it sends/submits/confirms/deletes something",
    "vision_action_click": "click something (located via screenshot) that looks like it sends/submits/confirms/deletes something",
}

# Actions considered safe/read-only and allowed to run without confirmation
# even in "normal" safety mode: opening an app, web search, reading a file,
# listing a folder. (Documented here for clarity; these simply never call
# confirm() from their tool implementations.)


def is_risky(action: str) -> bool:
    """Return True if `action` (a key from RISKY_ACTIONS) requires confirmation."""
    return action in RISKY_ACTIONS


# ----------------------------------------------------------------------------
# Hard blocks — categorically refused, never just confirmed. These differ
# from RISKY_ACTIONS above: no confirmation can unlock them, because Jarvis
# has no secure way to handle them at all (see the general command
# execution build's safety-wiring step for the full policy: credentials
# entered into ad-hoc forms, financial trades/purchases/transfers,
# permanent data deletion, bypassing CAPTCHA/bot-detection).
# ----------------------------------------------------------------------------

_CREDENTIAL_FIELD_HINTS = (
    "password", "passcode", "pin", "card number", "credit card", "debit card",
    "cvv", "cvc", "security code", "bank account", "routing number", "iban",
    "swift code", "ssn", "social security",
)


def refuse_if_credential_like(source: str, field_description: str, value: str = "") -> str | None:
    """Hard-block entering something that looks like a password/card/bank
    credential into an ad-hoc web form or desktop field. Returns a refusal
    message if blocked, or None if it's fine to proceed. This is a hard
    limit, not a confirmation gate — existing .env-based logins (GUC etc.)
    are a separate, explicit mechanism and are unaffected."""
    haystack = field_description.lower()
    if any(hint in haystack for hint in _CREDENTIAL_FIELD_HINTS):
        log_action(source, f"REFUSED — credential-like field: {field_description}")
        return (
            f"Refused: '{field_description}' looks like a password/card/bank "
            "credential field. Kareem never enters credentials into ad-hoc "
            "forms — please enter this yourself. (Existing .env-based logins "
            "for GUC etc. are a separate, explicit mechanism and are unaffected.)"
        )
    return None


# ----------------------------------------------------------------------------
# Click-intent classification — for the general command execution build's
# browser_click/app_click/vision_click tools (jarvis/tools/browser.py,
# app_control.py, vision.py). A single generic "click this description"
# tool can't be pre-classified as risky/safe the way a fixed tool name
# can (RISKY_ACTIONS above), so these tools classify the TARGET
# description at call time instead. Keyword-based and deliberately
# conservative: ambiguous descriptions default to "risky" (confirm first)
# rather than silently letting a real send/submit/purchase through.
# ----------------------------------------------------------------------------

_HARD_BLOCK_FINANCIAL = (
    "buy now", "purchase", "place order", "confirm order", "checkout",
    "pay now", "send payment", "send money", "transfer funds", "wire transfer",
    "confirm payment", "complete payment", "buy ", "subscribe and pay",
)
_FINANCIAL_NAVIGATION_EXCEPTIONS = (
    "purchase history", "order history", "payment history", "transaction history",
)
_HARD_BLOCK_DELETION = (
    "delete forever", "permanently delete", "empty trash", "delete account",
    "erase all", "factory reset", "delete permanently",
)
_HARD_BLOCK_CAPTCHA = (
    "captcha", "recaptcha", "i'm not a robot", "i am not a robot",
    "verify you're human", "verify you are human", "bot check", "bot detection",
)

_RISKY_CLICK_HINTS = (
    "send", "submit", "confirm", "post", "publish", "share", "delete", "remove",
    "unsubscribe", "cancel subscription", "sign out", "log out", "reply all",
)

_HARD_BLOCK_CLICK_MESSAGES = {
    "hard_block_financial": (
        "Refused: '{description}' looks like a financial trade, purchase, "
        "or money transfer. Kareem never completes these — please do this "
        "yourself."
    ),
    "hard_block_deletion": (
        "Refused: '{description}' looks like PERMANENT deletion — actual "
        "irreversible removal, not the University-style 'move to "
        "Completed'. Kareem never does this — please do this yourself."
    ),
    "hard_block_captcha": (
        "Refused: '{description}' looks like solving/bypassing a CAPTCHA "
        "or bot-detection check. Kareem never does this — please do this "
        "yourself."
    ),
}


def classify_click_intent(description: str) -> str:
    """Classify a plain-language click TARGET description. Returns one of
    "hard_block_financial" / "hard_block_deletion" / "hard_block_captcha"
    (categorically refused — see refuse_if_hard_blocked_click), "risky"
    (must go through guard() before clicking), or "free" (proceeds
    without asking, same as any other read-only/navigation action)."""
    haystack = description.lower()
    financial_matches = [h for h in _HARD_BLOCK_FINANCIAL if h in haystack]
    purchase_is_navigation = any(
        h in haystack for h in _FINANCIAL_NAVIGATION_EXCEPTIONS
    )
    if any(h != "purchase" for h in financial_matches) or (
        "purchase" in financial_matches and not purchase_is_navigation
    ):
        return "hard_block_financial"
    if any(h in haystack for h in _HARD_BLOCK_DELETION):
        return "hard_block_deletion"
    if any(h in haystack for h in _HARD_BLOCK_CAPTCHA):
        return "hard_block_captcha"
    if any(h in haystack for h in _RISKY_CLICK_HINTS):
        return "risky"
    return "free"


def refuse_if_hard_blocked_click(source: str, description: str) -> str | None:
    """Hard-block a click whose description matches a categorically
    off-limits intent. Returns a refusal message if blocked, or None if
    the click may proceed (still possibly through the normal risky-click
    confirmation gate — see classify_click_intent)."""
    kind = classify_click_intent(description)
    if kind.startswith("hard_block"):
        log_action(source, f"REFUSED ({kind}) — {description}")
        return _HARD_BLOCK_CLICK_MESSAGES[kind].format(description=description)
    return None


# When a voice command is being processed, the voice loop installs a
# "speak the question, listen for yes/no" function here so confirmations
# happen out loud instead of blocking on the keyboard. None = use input().
_voice_ask_fn = None


def set_ask_fn(fn):
    """Install (or clear, with None) the function used to ask for confirmation.
    Returns the previously installed function so callers (e.g. the web layer)
    can restore it when their turn is over."""
    global _voice_ask_fn
    previous = _voice_ask_fn
    _voice_ask_fn = fn
    return previous


# Accepted "yes" answers for a typed/console (or web-button) confirmation.
# The voice path (jarvis/voice/controller.py::_voice_confirm) already
# normalizes a spoken reply down to a bare "yes"/"no" before confirm() sees
# it, so this set only needs to cover what a person actually TYPES — broadened
# past "y"/"yes" so a typed "yeah"/"ok"/"sure" isn't silently treated as a
# decline (bug F7). Matched by exact token after strip()+lower(), never as a
# substring, so "no"/"nope"/"cancel" always remain declines.
_AFFIRMATIVE_RESPONSES = {
    "y", "yes", "yeah", "yep", "yup", "yea", "sure", "ok", "okay",
    "confirm", "go ahead", "do it",
}


def confirm(action: str, description: str, ask_fn=None) -> bool:
    """
    Ask the user to confirm a risky action before it happens.

    action: one of the keys in RISKY_ACTIONS (used for logging/classification)
    description: a precise, plain-English sentence describing exactly what is
                 about to happen, e.g. "Delete C:\\Users\\hamza\\old_notes.txt"
    ask_fn: optional callable(prompt: str) -> str, used to get the user's
            response. If not given, falls back to console input(). The voice
            loop passes in a function that speaks the prompt and listens for
            a spoken "yes"/"no" instead.

    Returns True if the user confirmed, False otherwise. Every call is logged.
    """
    prompt = f"Kareem wants to: {description}\nProceed? (y/n): "

    from jarvis import session_log
    session_log.log_event("confirmation_asked", action=action, description=description)
    source = "console" if ask_fn is None and _voice_ask_fn is None else "unknown"
    ask_fn = ask_fn or _voice_ask_fn
    if ask_fn is not None:
        response = ask_fn(prompt)
    else:
        print(f"\n[CONFIRM] {description}")
        response = input("Proceed? (y/n): ")

    confirmed = str(response).strip().lower() in _AFFIRMATIVE_RESPONSES
    session_log.log_event(
        "confirmation_answered", action=action, description=description,
        approved=confirmed, source=source,
    )
    log_action(action, description, confirmed=confirmed)
    return confirmed


def guard(action: str, description: str, func, *args, ask_fn=None, **kwargs):
    """
    Convenience wrapper: if `action` is risky, ask for confirmation first;
    only calls func(*args, **kwargs) if confirmed (or the action is safe).

    Every tool that performs a risky operation should call this instead of
    calling confirm()/the function directly, so the safety logic stays in
    exactly one place.
    """
    if is_risky(action):
        if not confirm(action, description, ask_fn=ask_fn):
            return (
                "The user DECLINED this action. Do not retry it or call this "
                "tool again — acknowledge the cancellation and move on."
            )
    else:
        log_action(action, description)

    return func(*args, **kwargs)
