"""User-facing error sanitization.

One place that turns ANY exception into a short, human-readable string safe to
show the user — never a raw JSON/dict error body, request URL, or nested
exception repr. Used at every surface point that reports a failure (the web
server, the console loop, the calendar tool, the hosted brain), so the
"raw provider error leaked into the UI" bug (F1) can't recur one surface at a
time.

Imports nothing from the rest of kareem, so it's safe to import anywhere.
"""


def user_safe_error(exc) -> str:
    """Short, human-readable text from `exc`, guaranteed to contain no '{' or
    '<' (so a raw dict/JSON body, request URL, or HTML fragment can never reach
    a user-facing string). Falls back to the exception's type name if nothing
    clean can be extracted.

    Handles the exception shapes Kareem actually surfaces:
      * Google API errors (googleapiclient HttpError) expose a clean `.reason`.
      * OpenAI-compatible SDK errors carry the provider message in a parsed
        `.body` dict ({"error": {"message": ...}}) or a `.message` attribute.
      * Anything else: the clean leading prose of str(exc), with any
        '{...}'/'<...>' tail dropped.
    """
    return _trim_blob(_extract_message(exc)) or exc.__class__.__name__


def _extract_message(exc) -> str:
    """Best candidate message string from `exc`, before blob-trimming."""
    reason = getattr(exc, "reason", None)               # googleapiclient HttpError
    if isinstance(reason, str) and reason.strip():
        return reason
    body = getattr(exc, "body", None)                   # openai-compatible SDK error
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            return error["message"]
        if isinstance(error, str) and error.strip():
            return error
        if isinstance(body.get("message"), str):
            return body["message"]
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message.strip() and "{" not in message and "<" not in message:
        return message
    return str(exc)


def _trim_blob(text: str) -> str:
    """Keep only the clean leading prose of `text`, dropping any '{...}' or
    '<...>' tail (a JSON/dict error body, a request URL, an HTML fragment, or a
    nested exception repr). Returns '' if nothing clean remains."""
    text = (text or "").strip()
    cut = min((index for index in (text.find("{"), text.find("<")) if index != -1), default=-1)
    if cut != -1:
        text = text[:cut].rstrip(" :-—")
    return text
