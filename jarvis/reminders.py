"""Background delivery of tracker reminders."""

import threading
import time

from jarvis import trackers


_lock = threading.Lock()
_started = False


def start_reminder_thread(speak_fn=None) -> None:
    """Start the process-wide daemon reminder loop once. Never raises."""
    global _started
    try:
        with _lock:
            if _started:
                return
            _started = True

        def run():
            toast_fn = None
            toast_note_printed = False
            try:
                from win11toast import toast
                toast_fn = toast
            except Exception as error:
                print(f"Reminder desktop notifications unavailable ({error}); reminders will continue without toasts.")
                toast_note_printed = True
            while True:
                try:
                    reminders = trackers.due_reminders()
                except Exception as error:
                    print(f"Reminder check failed ({error}); trying again later.")
                    reminders = []
                for item in reminders:
                    try:
                        text = str(item.get("text") or "").strip()
                        item_id = item.get("id")
                        if not text or not item_id:
                            continue
                        if toast_fn is not None:
                            try:
                                toast_fn(title="Jarvis reminder", body=text)
                            except Exception as error:
                                if not toast_note_printed:
                                    print(f"Reminder desktop notifications unavailable ({error}); reminders will continue without toasts.")
                                    toast_note_printed = True
                                toast_fn = None
                        if speak_fn is not None:
                            try:
                                speak_fn(f"Reminder: {text}")
                            except Exception as error:
                                print(f"Reminder speech failed ({error}).")
                        trackers.mark_notified(item_id)
                    except Exception as error:
                        print(f"A reminder could not be delivered ({error}); continuing.")
                time.sleep(20)

        threading.Thread(target=run, daemon=True, name="jarvis-reminders").start()
    except Exception as error:
        print(f"Reminder service unavailable ({error}).")
