"""Create shortcuts that launch Kareem silently in the background.

Usage:
  python scripts/create_desktop_shortcut.py
  python scripts/create_desktop_shortcut.py --autostart
  python scripts/create_desktop_shortcut.py --remove-autostart
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHONW = Path(sys.executable).with_name("pythonw.exe")


def _ps_string(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _desktop_dir() -> Path:
    """Find the real Desktop, including the common OneDrive redirection."""
    candidates = [Path.home() / "Desktop"]
    if os.environ.get("OneDrive"):
        candidates.insert(0, Path(os.environ["OneDrive"]) / "Desktop")
    return next((path for path in candidates if path.is_dir()), candidates[-1])


def _create_shortcut(shortcut_path: Path) -> bool:
    main_py = PROJECT_ROOT / "main.py"
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    ps_script = "\n".join([
        "$WshShell = New-Object -ComObject WScript.Shell",
        f"$Shortcut = $WshShell.CreateShortcut({_ps_string(shortcut_path)})",
        f"$Shortcut.TargetPath = {_ps_string(PYTHONW)}",
        f"$Shortcut.Arguments = {_ps_string(f'\"{main_py}\" --background')}",
        f"$Shortcut.WorkingDirectory = {_ps_string(PROJECT_ROOT)}",
        '$Shortcut.Description = "Start Kareem (background, no console window)"',
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
        description="Create Kareem background-launch shortcuts. With no flag, "
                    "a Desktop shortcut is created."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--autostart", action="store_true",
                       help="start Kareem automatically at Windows login")
    group.add_argument("--remove-autostart", action="store_true",
                       help="remove Kareem from Windows Startup")
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

    if not PYTHONW.exists():
        print(f"Couldn't find pythonw.exe next to your Python install ({PYTHONW}). "
              "Make sure Python was installed normally.")
        sys.exit(1)

    shortcut_path = (startup_shortcut if args.autostart
                     else _desktop_dir() / "Kareem.lnk")
    if not _create_shortcut(shortcut_path):
        sys.exit(1)
    if args.autostart:
        print("Kareem will now start silently when you sign in to Windows.")
    else:
        print("Double-click it any time to start Kareem in the background.")


if __name__ == "__main__":
    main()
