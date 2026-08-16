"""
App/window control tools (Phase 2). Opening and focusing apps changes
nothing on disk, so these run without a safety confirmation — but every
call is still written to kareem.log.
"""

import subprocess

from kareem.safety import log_action
from kareem.tools import register

# Friendly names for apps whose executable name isn't obvious.
KNOWN_APPS = {
    "notepad": "notepad",
    "calculator": "calc",
    "calc": "calc",
    "paint": "mspaint",
    "chrome": "chrome",
    "google chrome": "chrome",
    "edge": "msedge",
    "microsoft edge": "msedge",
    "firefox": "firefox",
    "word": "winword",
    "excel": "excel",
    "powerpoint": "powerpnt",
    "file explorer": "explorer",
    "explorer": "explorer",
    "cmd": "cmd",
    "command prompt": "cmd",
    "powershell": "powershell",
    "terminal": "wt",
    "task manager": "taskmgr",
    "settings": "ms-settings:",
    "vs code": "code",
    "vscode": "code",
    "spotify": "spotify",
}


# Every section navigate_app can switch the web UI to. Kept as the single
# source of truth for both the tool's enum (below) and the description text,
# so the two can never drift apart.
APP_SECTIONS = {
    "chat": "the main conversation screen (closes any open panel)",
    "university": "the University/GUC tracker screen (Assignments, Quizzes, "
                  "Exams, Emails, Announcements, Finances, Completed tabs)",
    "history": "the conversation history panel (past sessions)",
    "trackers": "the to-do/reminders tracker panel",
    "memory": "the remembered-facts panel",
    "activity": "the activity feed sidebar (recent tool/turn log)",
}


@register({
    "name": "navigate_app",
    "description": (
        "Switch Kareem's OWN web UI to a different screen (e.g. 'open the "
        "university tab', 'go back to chat'). NOT for other websites/apps — "
        "use browser/app tools for those. No confirmation needed. Sections: "
        + "; ".join(f"'{k}' ({v})" for k, v in APP_SECTIONS.items())
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "section": {
                "type": "string",
                "enum": list(APP_SECTIONS.keys()),
                "description": "Which screen of Kareem's own web UI to switch to",
            },
        },
        "required": ["section"],
    },
})
def navigate_app(section: str) -> str:
    section = section.strip().lower()
    if section not in APP_SECTIONS:
        return f"Unknown section '{section}'. Valid sections: {', '.join(APP_SECTIONS)}."
    log_action("navigate_app", section)
    from kareem.web import bridge
    if bridge.navigate(section):
        return f"Switched the web interface to {section}."
    return (
        f"No web interface tab is currently open to navigate — this only "
        f"works when the user is talking to you through the website "
        f"(there's nothing to switch on the console/voice-only)."
    )


@register({
    "name": "open_app",
    "description": (
        "Open a desktop application on the user's Windows PC by name, "
        "e.g. 'notepad', 'chrome', 'calculator', 'excel'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The application to open"},
        },
        "required": ["name"],
    },
})
def open_app(name: str) -> str:
    target = KNOWN_APPS.get(name.strip().lower(), name.strip())
    log_action("open_app", target)
    try:
        # 'start' resolves apps registered with Windows (App Paths), URIs, and
        # anything on PATH — closest thing to typing the name in the Run box.
        subprocess.Popen(
            ["cmd", "/c", "start", "", target],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return f"Opened {name}."
    except Exception as e:
        return f"Couldn't open {name}: {e}"


@register({
    "name": "close_app",
    "description": (
        "Close a running desktop app by name (e.g. 'notepad', 'calculator'). "
        "Sends a graceful close request (not a force-kill) and then actually "
        "VERIFIES the process exited before reporting success; never "
        "reports success on a guess. Note: 'graceful' asks nicely, it "
        "doesn't guarantee an app's own 'save changes?' prompt blocks this — "
        "live-tested against Windows 11's Notepad with an unsaved edit, "
        "which closed anyway rather than pausing on a prompt; don't assume "
        "unsaved work is protected. Prefer this over browser_click/"
        "app_click/vision_click for closing an app: those click at a pixel "
        "position or an accessibility-tree control and can report 'clicked' "
        "successfully without the app actually closing (e.g. hitting a "
        "'Close Tab' control instead of the window's real Close in a "
        "tabbed app like Windows 11's Notepad) — this tool's whole point is "
        "not making that mistake. If the app doesn't close within a few "
        "seconds (e.g. it's waiting on an unsaved-changes prompt you can't "
        "see), this says so honestly instead of claiming success."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "The app to close, e.g. 'notepad'"},
        },
        "required": ["name"],
    },
})
def close_app(name: str) -> str:
    import time

    import psutil

    target = KNOWN_APPS.get(name.strip().lower(), name.strip())
    exe_name = target if target.lower().endswith(".exe") else f"{target}.exe"
    log_action("close_app", target)

    matches = [
        p for p in psutil.process_iter(["pid", "name"])
        if (p.info.get("name") or "").lower() == exe_name.lower()
    ]
    if not matches:
        return f"No running process found matching '{name}' (looked for {exe_name})."

    try:
        # No /F: a graceful WM_CLOSE-style request to the process's main
        # window, same as clicking its own close button, rather than an
        # unconditional force-kill (a much more destructive, separate
        # decision this tool deliberately doesn't make on its own). This is
        # a real but limited protection, not a guarantee — live-tested
        # against Windows 11's Notepad with unsaved text, and it closed
        # anyway instead of pausing on its own save-changes prompt. Classic
        # Win32 apps may honor this more reliably; don't rely on it either
        # way for anything the user would mind losing.
        subprocess.run(
            ["taskkill", "/IM", exe_name],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        return f"Couldn't send a close request to {name}: {e}"

    # Verify — this is the actual fix: don't trust that the close request was
    # sent, confirm the process is actually gone. taskkill's own exit code
    # only means "request delivered," not "process exited."
    deadline = time.time() + 6
    still_running = matches
    while time.time() < deadline:
        still_running = [p for p in still_running if p.is_running()]
        if not still_running:
            break
        time.sleep(0.3)

    if not still_running:
        return f"Closed {name}."
    return (
        f"{name} didn't actually close within 6 seconds — it may be "
        "waiting on an unsaved-changes prompt that isn't visible here, or "
        "the close request didn't register. Not reporting success. Check "
        "if there's a prompt to respond to, or ask the user before forcing "
        "it closed (that would discard unsaved changes)."
    )


@register({
    "name": "list_open_windows",
    "description": "List the titles of all windows currently open on the user's PC.",
    "parameters": {"type": "object", "properties": {}},
})
def list_open_windows() -> str:
    log_action("list_open_windows")
    try:
        import pygetwindow
    except ImportError:
        return "The pygetwindow package isn't installed (pip install -r requirements.txt)."

    titles = [t for t in pygetwindow.getAllTitles() if t.strip()]
    if not titles:
        return "No open windows found."
    return "Open windows:\n" + "\n".join(f"- {t}" for t in titles[:40])


@register({
    "name": "focus_window",
    "description": (
        "Bring a window to the front by (part of) its title. "
        "Use list_open_windows first if unsure of the exact title."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Part of the window title to match"},
        },
        "required": ["title"],
    },
})
def focus_window(title: str) -> str:
    log_action("focus_window", title)
    try:
        import pygetwindow
    except ImportError:
        return "The pygetwindow package isn't installed (pip install -r requirements.txt)."

    matches = pygetwindow.getWindowsWithTitle(title)
    if not matches:
        return f"No window found with '{title}' in its title."
    win = matches[0]
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
        return f"Focused window: {win.title}"
    except Exception as e:
        return f"Found the window but couldn't focus it: {e}"
