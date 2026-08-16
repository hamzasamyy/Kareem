"""Approximate Claude API token-usage/cost tracking (Section 2 of the
post-Claude-switch task).

There was no existing "ElevenLabs-style" usage-logging pattern anywhere in
Kareem to match (verified: zero references to ElevenLabs in this codebase —
TTS here is Kokoro/Piper/pyttsx3) — the user chose to have this designed
fresh, matching Kareem's own conventions instead:
  - a `[claude]` console log line per call, same style as brain.py's
    existing `[groq]`/`[ollama]`/`[tools]` round-trip timing lines
  - a JSON-backed running total, same atomic-write pattern as
    kareem/trackers.py (data/ dir, tmp-file + os.replace, best-effort/
    never raises)
  - also written to kareem.log via safety.log_action so it shows up
    alongside everything else in the Activity log

Numbers here are approximate, not a billing system: pricing is a hardcoded
snapshot (Claude Haiku 4.5: $1.00 / $5.00 per million input/output tokens,
per Anthropic's published rates) and will silently under/over-estimate if
config.CLAUDE_MODEL is changed to a different tier without updating
_PRICING below.
"""

import json
import os
import threading
from datetime import date
from pathlib import Path

_lock = threading.Lock()
_path = Path(__file__).resolve().parent.parent / "data" / "claude_usage.json"

# $ per million (input, output) tokens. Extend this if CLAUDE_MODEL changes
# to a different tier — see kareem/config.py's CLAUDE_MODEL setting.
_PRICING = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
    "claude-sonnet-5": (3.00, 15.00),
    "claude-opus-5": (5.00, 25.00),
}
_DEFAULT_PRICING = (1.00, 5.00)  # unrecognized model id: assume Haiku-tier rates

# In-memory running total for the current process — resets on restart by
# design (a "session" is one run of Kareem); the day total below is the
# persisted figure that survives restarts.
_session_input_tokens = 0
_session_output_tokens = 0


def _price_for(model: str) -> tuple[float, float]:
    return _PRICING.get(model, _DEFAULT_PRICING)


def _estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _price_for(model)
    return (input_tokens / 1_000_000) * in_rate + (output_tokens / 1_000_000) * out_rate


def _read_day_totals() -> dict:
    try:
        data = json.loads(_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _write_day_totals(data: dict) -> None:
    _path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = _path.with_name(_path.name + ".tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp_path, _path)


def record(model: str, input_tokens: int, output_tokens: int) -> None:
    """Log one Claude API call's approximate token usage/cost, and update
    the running session total (in-memory) and running day total (persisted
    to data/claude_usage.json). Best-effort — never raises; a bookkeeping
    failure here must never interrupt a real conversation turn."""
    global _session_input_tokens, _session_output_tokens
    try:
        input_tokens = int(input_tokens or 0)
        output_tokens = int(output_tokens or 0)
        call_cost = _estimate_cost_usd(model, input_tokens, output_tokens)

        with _lock:
            _session_input_tokens += input_tokens
            _session_output_tokens += output_tokens
            session_cost = _estimate_cost_usd(
                model, _session_input_tokens, _session_output_tokens
            )

            today = date.today().isoformat()
            data = _read_day_totals()
            day = data.get(today) or {"input_tokens": 0, "output_tokens": 0}
            day["input_tokens"] = day.get("input_tokens", 0) + input_tokens
            day["output_tokens"] = day.get("output_tokens", 0) + output_tokens
            data[today] = day
            _write_day_totals(data)
            day_cost = _estimate_cost_usd(model, day["input_tokens"], day["output_tokens"])

            session_total = _session_input_tokens + _session_output_tokens
            day_total = day["input_tokens"] + day["output_tokens"]

        print(
            f"  [claude] {input_tokens} in / {output_tokens} out tokens "
            f"(~${call_cost:.4f}) — session: {session_total} tok (~${session_cost:.4f}), "
            f"today: {day_total} tok (~${day_cost:.4f})"
        )

        from kareem.safety import log_action
        log_action(
            "claude_usage",
            f"{input_tokens} in / {output_tokens} out tokens (~${call_cost:.4f} "
            f"est.) — session ~${session_cost:.4f}, today ~${day_cost:.4f}",
        )
    except Exception:
        # Bookkeeping is best-effort only — never let it break a real turn.
        pass
