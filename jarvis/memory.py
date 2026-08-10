"""Defensive JSON-backed storage for durable user facts.

This is a best-effort subsystem: every public function catches malformed
input, damaged JSON, and disk errors so memory trouble cannot interrupt a
Jarvis conversation.
"""

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path


logger = logging.getLogger("jarvis")
_lock = threading.Lock()
_path = Path(__file__).resolve().parent.parent / "data" / "memory.json"


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read_locked():
    try:
        data = json.loads(_path.read_text(encoding="utf-8"))
        facts = data.get("facts", [])
        return facts if isinstance(facts, list) else []
    except FileNotFoundError:
        return []
    except Exception:
        return []


def _write_locked(facts):
    _path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _path.with_name(_path.name + ".tmp")
    temp_path.write_text(
        json.dumps({"facts": facts}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(temp_path, _path)


def add_fact(text):
    """Add or refresh one durable fact and return it, or None on failure."""
    try:
        if not isinstance(text, str) or not text.strip():
            return None
        clean_text = text.strip()
        with _lock:
            facts = _read_locked()
            fact = next((value for value in facts
                         if isinstance(value, dict)
                         and str(value.get("text", "")).strip().lower() == clean_text.lower()), None)
            if fact is None:
                fact = {
                    "id": uuid.uuid4().hex[:8],
                    "text": clean_text,
                    "created": _now(),
                }
                facts.append(fact)
            else:
                fact["created"] = _now()
            _write_locked(facts)
        return dict(fact)
    except Exception:
        logger.debug("memory.add_fact failed", exc_info=True)
        return None


def list_facts(limit=None):
    """Return remembered facts newest first, optionally capped."""
    try:
        with _lock:
            facts = [dict(fact) for fact in _read_locked() if isinstance(fact, dict)]
        facts.sort(key=lambda fact: fact.get("created") or "", reverse=True)
        if limit is None:
            return facts
        limit = int(limit)
        return facts[:max(0, limit)]
    except Exception:
        return []


def remove_fact(fact_id):
    """Remove one durable fact by id."""
    try:
        with _lock:
            facts = _read_locked()
            kept = [fact for fact in facts
                    if not (isinstance(fact, dict) and fact.get("id") == fact_id)]
            if len(kept) == len(facts):
                return False
            _write_locked(kept)
        return True
    except Exception:
        logger.debug("memory.remove_fact failed", exc_info=True)
        return False


def format_memory_block(limit=15):
    """Format recent facts as a compact system-prompt block."""
    try:
        facts = list_facts(limit)
        if not facts:
            return ""
        lines = ["Known facts about the user, remembered from earlier conversations:"]
        lines.extend(f"- {fact.get('text', '')}" for fact in facts)
        return "\n".join(lines)
    except Exception:
        return ""
