"""Create Kareem launch shortcuts.

Two different shortcuts, for two different ways of running Kareem:

  python scripts/create_desktop_shortcut.py
      Desktop "Kareem" shortcut -> SIMPLE MODE. Double-click it and Kareem
      opens in your browser; close that browser tab and Kareem quits. This is
      the easy, one-sitting way to use Kareem. (Runs run_kareem_simple.bat.)

  python scripts/create_desktop_shortcut.py --autostart
      Startup "Kareem" shortcut -> SILENT ALWAYS-ON listener that starts when
      you sign in to Windows, with no window and no auto-quit. (Runs
      pythonw main.py --background.)

  python scripts/create_desktop_shortcut.py --remove-autostart
      Remove the silent always-on Startup shortcut.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHONW = Path(sys.executable).with_name("pythonw.exe")
SIMPLE_LAUNCHER = PROJECT_ROOT / "run_kareem_simple.bat"


def _ps_string(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _desktop_dir() -> Path:
    """Find the real Desktop, including the common OneDrive redirection."""
    candidates = [Path.home() / "Desktop"]
    if os.environ.get("OneDrive"):
        candidates.insert(0, Path(os.environ["OneDrive"]) / "Desktop")
    return next((path for path in candidates if path.is_dir()), candidates[-1])


def _create_shortcut(shortcut_path: Path, target: Path | str, arguments: str,
                     description: str) -> bool:
    """Write a .lnk pointing at `target` with `arguments`, working dir =
    PROJECT_ROOT. Returns True on success."""
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    ps_script = "\n".join([
        "$WshShell = New-Object -ComObject WScript.Shell",
        f"$Shortcut = $WshShell.CreateShortcut({_ps_string(shortcut_path)})",
        f"$Shortcut.TargetPath = {_ps_string(target)}",
        f"$Shortcut.Arguments = {_ps_string(arguments)}",
        f"$Shortcut.WorkingDirectory = {_ps_string(PROJECT_ROOT)}",
        f"$Shortcut.Description = {_ps_string(description)}",
        "$Shortcut.Save()",
    ])
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True, text=True,
    )
    if result.returncode == 0 and shortcut_path.exists():
        print(f"Created: {shortcut_path}")
        return True
    print("Couldn't create the shortcut. PowerShell said:")
    print(result.stderr or result.stdout)
    return False


def main():
    parser = argparse.ArgumentParser(
        description="Create Kareem launch shortcuts. With no flag, a Desktop "
                    "'Kareem' shortcut for SIMPLE MODE (opens in the browser, "
                    "closing the tab quits) is created."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--autostart", action="store_true",
                       help="instead, add the SILENT always-on listener to "
                            "Windows Startup (runs at login, no window)")
    group.add_argument("--remove-autostart", action="store_true",
                       help="remove the silent always-on listener from Startup")
    args = parser.parse_args()

    appdata = os.environ.get("APPDATA")
    if not appdata:
        print("Couldn't find the Windows APPDATA folder.")
        sys.exit(1)
    startup_shortcut = (Path(appdata) / "Microsoft" / "Windows" / "Start Menu" /
                        "Programs" / "Startup" / "Kareem.lnk")

    if args.remove_autostart:
        if startup_shortcut.exists():
            startup_shortcut.unlink()
            print(f"Removed: {startup_shortcut}")
        else:
            print("Kareem autostart was already disabled.")
        return

    if args.autostart:
        # Silent always-on listener: pythonw (no console) + --background.
        if not PYTHONW.exists():
            print(f"Couldn't find pythonw.exe next to your Python install "
                  f"({PYTHONW}). Make sure Python was installed normally.")
            sys.exit(1)
        main_py = PROJECT_ROOT / "main.py"
        ok = _create_shortcut(
            startup_shortcut, PYTHONW, f'"{main_py}" --background',
            "Start Kareem's always-on listener (background, no window)",
        )
        if not ok:
            sys.exit(1)
        print("Kareem's silent listener will now start when you sign in to Windows.")
        return

    # Default: SIMPLE-MODE Desktop shortcut (opens the browser; close the tab
    # to quit). Points at run_kareem_simple.bat, which picks Python 3.12 the
    # same way run_kareem.bat does.
    if not SIMPLE_LAUNCHER.exists():
        print(f"Couldn't find {SIMPLE_LAUNCHER}. Make sure run_kareem_simple.bat "
              "is in the Kareem folder.")
        sys.exit(1)
    shortcut_path = _desktop_dir() / "Kareem.lnk"
    if not _create_shortcut(
        shortcut_path, SIMPLE_LAUNCHER, "",
        "Open Kareem in your browser — closing the tab quits Kareem",
    ):
        sys.exit(1)
    print("Double-click 'Kareem' on your Desktop to open it in the browser. "
          "Close that browser tab to quit.")


if __name__ == "__main__":
    main()
