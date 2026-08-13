"""Safe tools for Kareem's own durable user memory."""

from kareem import memory
from kareem.tools import register


@register({
    "name": "remember_fact",
    "description": (
        "Save a short, durable fact about the user for ALL future conversations "
        "(name, preference, ongoing project) — e.g. when they say 'remember "
        "that...'. Not for one-off task details; use the tracker for those."
    ),
    "parameters": {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The durable fact to remember"}},
        "required": ["text"],
    },
})
def remember_fact(text: str) -> str:
    fact = memory.add_fact(text)
    if fact is None:
        return "I couldn't remember that fact. Please provide non-empty text."
    return f"Remembered [{fact['id']}] {fact['text']}."


@register({
    "name": "forget_fact",
    "description": "Remove a previously remembered fact using its id.",
    "parameters": {"type": "object", "properties": {
        "fact_id": {"type": "string", "description": "The fact's 8-character id"}},
        "required": ["fact_id"]},
})
def forget_fact(fact_id: str) -> str:
    return (f"Forgot remembered fact {fact_id}." if memory.remove_fact(fact_id)
            else f"I couldn't find remembered fact {fact_id}.")


@register({
    "name": "recall_facts",
    "description": "List everything currently remembered about the user.",
    "parameters": {"type": "object", "properties": {}},
})
def recall_facts() -> str:
    facts = memory.list_facts()
    if not facts:
        return "No remembered facts."
    return "\n".join(f"[{fact.get('id', '?')}] {fact.get('text', '')}" for fact in facts)
