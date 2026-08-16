"""
Desktop UI Automation control (Layer 2) — lets the brain click/type/read
INSIDE a focused desktop app by describing controls in plain language
("the Save button", "the file name field"), via Windows UI Automation
(pywinauto's uia backend) instead of blind coordinate clicks or raw
key-sending. Reliable for anything with accessible UI elements — the same
principle as kareem/tools/browser.py's Layer 1, one level down the stack
(native Win32/WinForms/WPF/UWP controls instead of a DOM). Vision (Layer
3) is the fallback for controls this can't find, not the default.

Named app_control.py (not apps.py, which already holds open_app/
focus_window) to keep "open an app" and "operate inside an app" separate.
"""

from kareem.safety import log_action
from kareem.tools import register


def _find_window(app_name: str):
    """Best-effort match of a top-level window by (part of) its title,
    case-insensitive, preferring one that's visible right now. Returns a
    pywinauto UIAWrapper or None."""
    from pywinauto import Desktop

    needle = app_name.strip().lower()
    try:
        windows = Desktop(backend="uia").windows()
    except Exception:
        return None
    matches = []
    for w in windows:
        try:
            title = w.window_text() or ""
            if needle in title.lower() and w.is_visible():
                matches.append(w)
        except Exception:
            continue
    if not matches:
        return None
    # Prefer an exact-ish match (needle is most of the title) over a loose
    # substring hit buried in a long title.
    matches.sort(key=lambda w: len(w.window_text() or ""))
    return matches[0]


def _find_controls(window, description: str, editable_only: bool = False):
    """Search a window's descendants for controls whose visible text or
    accessible name contains `description` (case-insensitive). Returns a
    list, most-likely match first (editable controls first if
    editable_only, since a click/read target and a type target favor
    different control kinds — mirrors browser.py's editable-vs-clickable
    role ordering)."""
    description = description.strip().lower()
    if not description:
        return []
    try:
        descendants = window.descendants()
    except Exception:
        descendants = []

    editable_types = {"Edit", "ComboBox", "Document", "SpinButton"}
    hits = []
    for ctrl in descendants:
        try:
            if not ctrl.is_visible():
                continue
            texts = [ctrl.window_text() or ""]
            name = getattr(getattr(ctrl, "element_info", None), "name", "") or ""
            texts.append(name)
            ctrl_type = getattr(getattr(ctrl, "element_info", None), "control_type", "") or ""
        except Exception:
            continue
        if any(description in t.lower() for t in texts if t):
            hits.append((ctrl, ctrl_type in editable_types))

    if editable_only:
        hits.sort(key=lambda pair: not pair[1])  # editable-typed first
    return [ctrl for ctrl, _ in hits]


@register({
    "name": "app_click",
    "description": (
        "Click a control (button, menu item, checkbox, tab, ...) in an open "
        "desktop app window, described in plain language (e.g. 'the Save "
        "button'). Finds it via Windows UI Automation, not pixel "
        "coordinates. Use list_open_windows first if unsure of the exact "
        "window title; try vision_click as a last resort if this can't find "
        "the target. Asks the user to confirm first for send/submit/delete-"
        "looking clicks; refuses outright for financial transactions, "
        "permanent deletion, or a CAPTCHA/bot-check. "
        "For CLOSING an app, use close_app instead, not this — clicking a "
        "'close'-labeled control here can report success without the app "
        "actually closing (e.g. it hits a 'Close Tab' control instead of "
        "the window's real close, in a tabbed app like Windows 11's "
        "Notepad); close_app verifies the process actually exited."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "(Part of) the app's window title"},
            "control_description": {"type": "string", "description": "Plain-language description of the control to click"},
        },
        "required": ["app_name", "control_description"],
    },
})
def app_click(app_name: str, control_description: str) -> str:
    from kareem.safety import classify_click_intent, guard, refuse_if_hard_blocked_click

    blocked = refuse_if_hard_blocked_click("app_click", control_description)
    if blocked:
        return blocked

    def _do_click():
        window = _find_window(app_name)
        if window is None:
            return f"Couldn't find an open, visible window matching '{app_name}'."
        try:
            window.set_focus()
        except Exception:
            pass
        controls = _find_controls(window, control_description)
        if not controls:
            return f"Couldn't find a control matching '{control_description}' in {app_name}."
        try:
            controls[0].click_input()
            return f"Clicked '{control_description}' in {app_name}."
        except Exception as e:
            return f"Found '{control_description}' in {app_name} but couldn't click it: {e}"

    if classify_click_intent(control_description) == "risky":
        return guard(
            "app_action_click",
            f"Click '{control_description}' in {app_name} (looks like it "
            "sends, submits, confirms, or deletes something)",
            _do_click,
        )
    log_action("app_click", f"{app_name} -> {control_description}")
    return _do_click()


@register({
    "name": "app_type",
    "description": (
        "Type text into a field inside a currently open desktop app "
        "window, described in plain language (e.g. 'the file name field', "
        "'the search box'). This only types the text — it does NOT press "
        "Enter/Submit; use app_click on the actual button for that. NEVER "
        "use this to enter a password or other credential — that is "
        "refused outright, not just confirmed."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "(Part of) the app's window title"},
            "field_description": {"type": "string", "description": "Plain-language description of the field to type into"},
            "text": {"type": "string", "description": "The text to type"},
        },
        "required": ["app_name", "field_description", "text"],
    },
})
def app_type(app_name: str, field_description: str, text: str) -> str:
    from kareem.safety import refuse_if_credential_like

    blocked = refuse_if_credential_like("app_type", field_description, text)
    if blocked:
        return blocked

    log_action("app_type", f"{app_name} -> {field_description} <- {'*' * len(text)}")
    window = _find_window(app_name)
    if window is None:
        return f"Couldn't find an open, visible window matching '{app_name}'."
    try:
        window.set_focus()
    except Exception:
        pass
    controls = _find_controls(window, field_description, editable_only=True)
    if not controls:
        return f"Couldn't find a field matching '{field_description}' in {app_name}."
    ctrl = controls[0]
    try:
        try:
            ctrl.set_edit_text(text)
        except Exception:
            # Not every editable control exposes set_edit_text (e.g. some
            # custom/WPF fields) — fall back to click + real keystrokes.
            ctrl.click_input()
            ctrl.type_keys(text, with_spaces=True)
        return f"Typed into '{field_description}' in {app_name}."
    except Exception as e:
        return f"Found '{field_description}' in {app_name} but couldn't type into it: {e}"


@register({
    "name": "app_read",
    "description": (
        "Read visible text from inside a currently open desktop app "
        "window — either one described control ('the status bar', 'the "
        "title') or the whole window if region_description is omitted. "
        "Use this to check what's actually in the app before deciding "
        "what to click/type next."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "app_name": {"type": "string", "description": "(Part of) the app's window title"},
            "region_description": {"type": "string", "description": "What to read; omit for the whole window"},
        },
        "required": ["app_name"],
    },
})
def app_read(app_name: str, region_description: str = "") -> str:
    log_action("app_read", f"{app_name} -> {region_description or '(whole window)'}")
    window = _find_window(app_name)
    if window is None:
        return f"Couldn't find an open, visible window matching '{app_name}'."

    if not region_description or region_description.strip().lower() in ("window", "whole window", "everything", ""):
        try:
            parts = []
            for ctrl in window.descendants():
                try:
                    if not ctrl.is_visible():
                        continue
                    t = ctrl.window_text()
                    if t and t.strip():
                        parts.append(t.strip())
                except Exception:
                    continue
            # Repeated labels can be real data (for example duplicate list
            # items), so preserve them rather than globally de-duplicating.
            text = "\n".join(parts)
        except Exception as e:
            return f"Couldn't read {app_name}: {e}"
    else:
        controls = _find_controls(window, region_description)
        if not controls:
            return f"Couldn't find anything matching '{region_description}' in {app_name} to read."
        try:
            text = controls[0].window_text()
        except Exception as e:
            return f"Couldn't read '{region_description}' in {app_name}: {e}"

    text = (text or "").strip()
    limit = 3000
    if len(text) > limit:
        text = text[:limit] + " …[truncated]"
    return text or "(no visible text found)"
