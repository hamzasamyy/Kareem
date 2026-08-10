"""Single-instance enforcement for Jarvis on Windows.

Uses a named kernel mutex so a second `python main.py` (or a leftover
process from a crashed run) is detected and refused up front, instead of
piling up duplicate wake-word/hotkey/GUC-scheduler threads alongside the
instance that's already running.
"""

import ctypes
import sys
import time

_DEFAULT_MUTEX_NAME = "Global\\JarvisSingleInstanceMutex"
_ERROR_ALREADY_EXISTS = 183

# Kept alive for the process lifetime so the OS holds the lock until this
# process exits (normally or via os._exit) — closing/GC'ing it early would
# release the mutex while Jarvis is still running.
_mutex_handle = None


def acquire(retry_seconds: float = 3.0, poll_interval: float = 0.3,
            name: str = _DEFAULT_MUTEX_NAME) -> bool:
    """Try to become the one running Jarvis instance.

    Returns True if this process now holds the lock, False if another
    Jarvis process already holds it. Retries for up to `retry_seconds`
    (a few seconds by default) so "Restart Jarvis" — which starts the new
    process slightly before the old one exits — doesn't spuriously fail.

    Windows-only (uses a named kernel mutex — ctypes.windll doesn't exist on
    other platforms). On any other OS this prints a note and returns True
    immediately rather than raising: single-instance protection only guards
    against duplicate background threads if you start Jarvis twice, which is
    a nice-to-have, not something that should block Jarvis from running at
    all on a platform this check doesn't support yet.
    """
    if sys.platform != "win32":
        print("(single-instance protection is Windows-only — skipping; "
              "avoid starting Jarvis twice from separate terminals.)")
        return True

    global _mutex_handle
    deadline = time.monotonic() + retry_seconds
    kernel32 = ctypes.windll.kernel32
    while True:
        handle = kernel32.CreateMutexW(None, False, name)
        last_error = kernel32.GetLastError()
        if handle and last_error != _ERROR_ALREADY_EXISTS:
            _mutex_handle = handle
            return True
        if handle:
            kernel32.CloseHandle(handle)
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll_interval)
