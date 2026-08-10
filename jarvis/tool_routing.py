"""Lightweight intent routing for tool schemas.

Every added feature registers more tools, and jarvis.agent used to hand the
brain's chat() call the FULL tool_specs list on every single message —
including "hello" — which adds real latency to every request regardless of
whether any tool is even plausibly relevant. This does a fast, local
keyword match (no extra model call) to guess which tool CATEGORIES are
relevant to the message, and only those categories' schemas get sent.

Keep CATEGORIES in sync with jarvis/tools/*.py — a tool whose name isn't
listed here is treated as "unknown" and always included (see
filter_tool_specs), so forgetting to categorize a new tool degrades to the
old always-send-everything behavior for that tool rather than silently
hiding it.
"""

import re

CATEGORIES: dict[str, set[str]] = {
    # Always sent regardless of the message — basic chat/memory/navigation,
    # web search, AND the real Google Calendar tools. web_search/fetch_page
    # used to be a routable "web" category gated on keywords like
    # "search"/"google"/"news" — but a real query ("the world cup finished
    # now, who won and what was the score") hit none of those keywords, so
    # search was never offered and Jarvis confidently answered from stale
    # training data — and was wrong. A wrong confident answer is worse than
    # the fixed, small cost of always including this one tool's schema, so
    # it isn't keyword-gated at all anymore. See jarvis/tools/web.py.
    #
    # Calendar tools hit the SAME failure mode: "put an appointment tomorrow
    # at 5pm" matched no "university" keyword at all ("appointment" wasn't
    # one, and a bare date/time phrase isn't either), so calendar_add_event
    # was silently excluded from tools= — the hosted model tried to call it
    # anyway (a very natural tool for that request) and the provider rejected
    # the call with "Tool call validation failed: ... not in request.tools",
    # which (via the round-0 Ollama fallback, offered the SAME excluded tool
    # list) bottomed out in a generic "I couldn't produce an answer for
    # that" with no calendar action ever taken. Enumerating every phrasing of
    # a calendar request is the same losing game keyword-matching web search
    # was; these 4 schemas are small, so always including them is cheaper
    # than a silently-failed calendar action. See jarvis/tools/calendar.py.
    "core": {"navigate_app", "remember_fact", "recall_facts", "forget_fact",
              "web_search", "fetch_page",
              "calendar_add_event", "calendar_list_events",
              "calendar_find_event", "calendar_delete_event"},

    # University/schedule: GUC portal and the personal tracker
    # (assignments/deadlines/reminders live together in daily use). Real
    # Google Calendar tools moved to "core" above — see the comment there.
    "university": {
        "guc_check_now",
        "tracker_add", "tracker_list", "tracker_complete",
        "tracker_remove", "tracker_set_reminder",
    },

    "browser": {"browser_open", "browser_click", "browser_type", "browser_read"},

    "desktop": {
        "open_app", "list_open_windows", "focus_window",
        "app_click", "app_type", "app_read", "vision_click",
        "run_command", "shutdown_or_restart", "run_python", "run_claude_code",
    },

    "files": {"list_folder", "find_files", "read_file", "move_file", "delete_file"},
}

ALWAYS_ON = {"core"}

# Substrings matched against the lowercased message. A category "fires" if
# any of its keywords appears anywhere in the text.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "university": (
        "guc", "assignment", "due", "deadline", "course", "grade", "quiz",
        "exam", "calendar", "event", "schedule", "meeting", "remind",
        "reminder", "tracker", "task", "todo", "to-do", "finance", "tuition",
    ),
    "browser": (
        "browser", "chrome", "edge", "firefox", "website", "webpage",
        " url", "link", " tab", "scroll",
    ),
    "desktop": (
        "open ", "launch", "app", "application", "window", "click",
        "type ", "screen", "desktop", "shutdown", "restart",
        "run command", "run python", "script", "automate",
    ),
    "files": (
        "file", "folder", "directory", "document", "download", "delete",
        "move ", "rename",
    ),
}


# Whole-word / phrase matching. A bare substring test made "app" fire on
# "happy"/"apparently"/"happen", "due" on "subdued", "grade" on "upgrade",
# "event" on "eventually" — dragging whole tool categories (including the
# risky desktop tools) into messages that never meant them. \b boundaries
# match the intended word, or a multi-word phrase like "run command", without
# matching inside a larger word. Keywords are stripped first, so the old
# hand-padded " url" / " tab" / "open " spellings keep working via real word
# boundaries instead of fragile leading/trailing spaces.
_PATTERNS: dict[str, "re.Pattern[str]"] = {
    category: re.compile(
        r"\b(?:"
        + "|".join(re.escape(keyword.strip()) for keyword in keywords if keyword.strip())
        + r")\b",
        re.IGNORECASE,
    )
    for category, keywords in _KEYWORDS.items()
}


def categories_for(text: str) -> set[str]:
    """Guess which tool categories are plausibly relevant to `text`.

    Always includes ALWAYS_ON. A message that matches no keyword at all
    (e.g. "hello", small talk) gets ALWAYS_ON only — that's the point of
    this module: a plain greeting shouldn't pay for the full tool schema.
    The trade-off is an oddly-phrased actionable request that hits no
    keyword falls back to core-only tools for that one turn; the model can
    still ask a clarifying question rather than silently fail."""
    haystack = text or ""
    matched = {
        category for category, pattern in _PATTERNS.items()
        if pattern.search(haystack)
    }
    return matched | ALWAYS_ON


def filter_tool_specs(tool_specs: list[dict], text: str) -> list[dict]:
    """Return only the tool specs whose category is relevant to `text`."""
    categories = categories_for(text)
    relevant_names = set()
    for category in categories:
        relevant_names |= CATEGORIES.get(category, set())
    known_names = {name for names in CATEGORIES.values() for name in names}
    return [
        spec for spec in tool_specs
        if spec["name"] not in known_names or spec["name"] in relevant_names
    ]
