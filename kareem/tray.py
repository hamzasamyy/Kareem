"""System tray icon: a visible sign Kareem is running, with a Quit option.

Degrades gracefully — if pystray/Pillow aren't installed or the platform
can't show a tray icon, this simply doesn't start.
"""

import threading
from pathlib import Path


def start_tray_icon(launch_cmd: list[str] | None = None) -> bool:
    """Start the tray icon in a daemon thread; never raise if unavailable.

    launch_cmd is the exact [sys.executable] + sys.argv this process was
    started with — "Restart Kareem" reuses it so a silent (pythonw
    --background) launch stays silent across a restart.
    """
    try:
        import pystray
        from PIL import Image, ImageDraw
    except Exception:
        return False

    def make_image():
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, size - 4, size - 4), fill=(34, 211, 238, 255))
        return image

    def quit_kareem(icon, item):
        from kareem import session_log
        icon.stop()
        try:
            session_log.close_session()
        except Exception:
            pass
        import os
        os._exit(0)

    def restart_kareem(icon, item):
        from kareem import session_log
        icon.stop()
        try:
            session_log.close_session()
        except Exception:
            pass
        if launch_cmd:
            try:
                import subprocess
                project_root = Path(__file__).resolve().parent.parent
                # The new process retries acquiring the single-instance lock
                # for a few seconds (see kareem/singleton.py), so it's fine
                # that this one hasn't released it yet.
                subprocess.Popen(launch_cmd, cwd=str(project_root))
            except Exception:
                pass
        import os
        os._exit(0)

    def open_web(icon, item):
        try:
            from kareem.web import server as web_server_module
            web_server_module.open_page()
        except Exception:
            pass

    try:
        menu = pystray.Menu(
            pystray.MenuItem("Kareem is running", None, enabled=False),
            pystray.MenuItem("Open Web UI", open_web),
            pystray.MenuItem("Restart Kareem", restart_kareem),
            pystray.MenuItem("Quit", quit_kareem),
        )
        icon = pystray.Icon("kareem", make_image(), "Kareem", menu)
        threading.Thread(target=icon.run, daemon=True, name="kareem-tray").start()
        return True
    except Exception:
        return False
