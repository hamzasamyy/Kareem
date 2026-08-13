"""Safe tools for Kareem's own personal tracker data."""

from kareem import trackers
from kareem.tools import register


_DUE_HELP = (
    "Prefer passing the resolved ISO 8601 date/time you already worked out "
    "(e.g. when you told the user the resolved date during confirmation) — "
    "don't re-send their raw phrase. If you don't have one, natural language "
    "also works as a fallback: 'in N minutes/hours/days', 'at 3pm', 'today at "
    "4:30pm', 'tomorrow at 9am', 'next Thursday', 'next Thursday 12am'."
)


@register({
    "name": "tracker_add",
    "description": (
        "Add an item to the user's personal to-do, reminder, calendar, project, "
        f"or custom-list tracking. {_DUE_HELP}"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["todo", "calendar", "files", "custom"]},
            "text": {"type": "string", "description": "The item to remember"},
            "due": {"type": "string", "description": _DUE_HELP},
            "tags": {"type": "array", "items": {"type": "string"}},
            "list_name": {"type": "string", "description": "Custom list display name"},
        },
        "required": ["category", "text"],
    },
})
def tracker_add(category: str, text: str, due=None, tags=None, list_name=None) -> str:
    due_iso = trackers.parse_due(due) if due is not None else None
    if due is not None and due_iso is None:
        return f"I couldn't understand that due time. Please use a clearer time. {_DUE_HELP}"
    item = trackers.add_item(category, text, due_iso, tags, list_name)
    if item is None:
        return "I couldn't add that tracker item. Check the category and item text."
    due_note = f" due {item['due']}" if item.get("due") else ""
    return f"Added [{item['id']}] {item['text']}{due_note}."


@register({
    "name": "tracker_list",
    "description": "List the user's personal to-do, reminder, calendar, project, or custom-list items concisely.",
    "parameters": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["todo", "calendar", "files", "custom"]},
            "status": {"type": "string", "enum": ["pending", "done"]},
        },
    },
})
def tracker_list(category=None, status=None) -> str:
    items = trackers.list_items(category, status)
    if not items:
        return "No matching tracker items."
    lines = []
    for item in items[:20]:
        marker = "x" if item.get("status") == "done" else " "
        due = f" — due {item['due']}" if item.get("due") else ""
        lines.append(f"[{item.get('id', '?')}] [{marker}] {item.get('text', '')}{due}")
    if len(items) > 20:
        lines.append(f"…and {len(items) - 20} more.")
    return "\n".join(lines)


@register({
    "name": "tracker_complete",
    "description": "Mark one personal tracker item complete using its 8-character id.",
    "parameters": {"type": "object", "properties": {
        "item_id": {"type": "string", "description": "The tracker item's 8-character id"}},
        "required": ["item_id"]},
})
def tracker_complete(item_id: str) -> str:
    return (f"Completed tracker item {item_id}." if trackers.complete_item(item_id)
            else f"I couldn't find tracker item {item_id}.")


@register({
    "name": "tracker_remove",
    "description": "Remove one item from the user's personal tracker using its 8-character id.",
    "parameters": {"type": "object", "properties": {
        "item_id": {"type": "string", "description": "The tracker item's 8-character id"}},
        "required": ["item_id"]},
})
def tracker_remove(item_id: str) -> str:
    return (f"Removed tracker item {item_id}." if trackers.remove_item(item_id)
            else f"I couldn't find tracker item {item_id}.")


@register({
    "name": "tracker_set_reminder",
    "description": (
        "Set or replace the reminder time on a personal tracker item. " + _DUE_HELP
    ),
    "parameters": {"type": "object", "properties": {
        "item_id": {"type": "string", "description": "The tracker item's 8-character id"},
        "when": {"type": "string", "description": _DUE_HELP}},
        "required": ["item_id", "when"]},
})
def tracker_set_reminder(item_id: str, when: str) -> str:
    due_iso = trackers.parse_due(when)
    if due_iso is None:
        return f"I couldn't understand that reminder time. Please use a clearer time. {_DUE_HELP}"
    if not trackers.set_reminder(item_id, due_iso):
        return f"I couldn't find tracker item {item_id}."
    return f"Reminder for {item_id} set to {due_iso}."
