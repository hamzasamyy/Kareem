"""
Bridge letting a plain tool function (kareem/tools/*.py, no access to the
WebSocket or any per-connection state) push a UI action to whichever browser
tab is currently running the active turn.

Mirrors kareem.safety's set_ask_fn/_voice_ask_fn pattern exactly: the web
server installs a push function for the duration of each turn (see
kareem/web/server.py's run_turn), and tools call the module-level function
here without needing a reference to anything connection-specific. Console/
voice-only turns simply have no push function installed, so navigate()
harmlessly returns False instead of raising.
"""

import threading

_lock = threading.Lock()
_push_fn = None


def set_push_fn(fn):
    """Install (or clear, with None) the function used to push a message to
    the browser for the current turn. Returns the previous one so callers
    can restore it when their turn ends."""
    global _push_fn
    with _lock:
        previous = _push_fn
        _push_fn = fn
    return previous


def navigate(section: str) -> bool:
    """Ask the currently-connected browser tab to switch to `section`.
    Returns False if there's no active web connection for this turn (e.g.
    the request came from the console or voice-only loop, which have no
    browser tab to drive)."""
    with _lock:
        fn = _push_fn
    if fn is None:
        return False
    fn({"type": "navigate_app", "section": section})
    return True
