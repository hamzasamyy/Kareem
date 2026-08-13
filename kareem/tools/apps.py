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
