"""Google Calendar OAuth and API client plumbing."""

import json
import threading
from pathlib import Path


SCOPES = ["https://www.googleapis.com/auth/calendar"]
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CREDENTIALS_PATH = PROJECT_ROOT / "credentials.json"
TOKEN_PATH = PROJECT_ROOT / "token.json"

# The built service is cached for the process. Rebuilding it on every call
# re-read token.json, re-ran the refresh logic, AND made build() fetch the API
# discovery document over the network each time — a real per-operation latency
# hit. The cached service's underlying credentials still auto-refresh when the
# access token expires, so caching is safe for a long-running process.
_service = None
_service_lock = threading.Lock()


def _acquire_credentials():
    """Load, refresh, or first-time-authorize credentials, saving the token
    back to disk. Split out from the builder so the service can be cached."""
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if TOKEN_PATH.is_file():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Google Calendar's token.json is invalid — remove it and try a "
                "calendar command again to reconnect"
            ) from exc

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise RuntimeError(
                    "Google Calendar authorization could not be refreshed — remove "
                    "token.json and try again to reconnect"
                ) from exc
        else:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_PATH), SCOPES
                )
                creds = flow.run_local_server(port=0)
            except Exception as exc:
                raise RuntimeError(
                    "Google Calendar authorization did not complete — try the "
                    "calendar command again and finish consent in your browser"
                ) from exc
        try:
            TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(f"Google Calendar token could not be saved: {exc}") from exc

    return creds


def get_calendar_service():
    """Return an authorized Google Calendar v3 service (built once, cached).

    Imports are deliberately lazy so a machine without the optional Google
    packages or credentials can still start Kareem normally.
    """
    global _service
    if _service is not None:
        return _service

    if not CREDENTIALS_PATH.is_file():
        raise RuntimeError(
            "Google Calendar isn't set up — see README for how to get credentials.json"
        )

    try:
        from google.auth.transport.requests import Request  # noqa: F401
        from google.oauth2.credentials import Credentials  # noqa: F401
        from google_auth_oauthlib.flow import InstalledAppFlow  # noqa: F401
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google Calendar dependencies aren't installed — run "
            "pip install -r requirements.txt"
        ) from exc

    with _service_lock:
        # Another turn may have built it while we waited for the lock.
        if _service is not None:
            return _service
        creds = _acquire_credentials()
        try:
            _service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        except Exception as exc:
            raise RuntimeError(
                f"Google Calendar client could not be created: {exc}"
            ) from exc
        return _service
