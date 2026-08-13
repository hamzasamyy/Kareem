"""Create Kareem launch shortcuts.

  python scripts/create_desktop_shortcut.py
      Desktop "Kareem" shortcut -> SILENT ALWAYS-ON listener (background, no
      window — the way Kareem normally runs). Runs pythonw main.py --background.

  python scripts/create_desktop_shortcut.py --simple
      Desktop "Kareem (Simple Mode)" shortcut -> opens Kareem in your browser;
      close that browser tab and Kareem quits. A separate, distinctly-named
      icon from the always-on "Kareem" above. Runs run_kareem_simple.bat.

  python scripts/create_desktop_shortcut.py --autostart
      Start the silent always-on listener automatically at Windows login
      (Startup "Kareem" shortcut).

  python scripts/create_desktop_shortcut.py --remove-autostart
      Remove the silent always-on listener from Windows Startup.
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


def _create_silent_shortcut(shortcut_path: Path) -> bool:
    """Silent always-on listener: pythonw (no console) + main.py --background."""
    if not PYTHONW.exists():
        print(f"Couldn't find pythonw.exe next to your Python install ({PYTHONW}). "
              "Make sure Python was installed normally.")
        return False
    main_py = PROJECT_ROOT / "main.py"
    return _create_shortcut(
        shortcut_path, PYTHONW, f'"{main_py}" --background',
        "Start Kareem (silent always-on listener, no console window)",
    )


def main():
    parser = argparse.ArgumentParser(
        description="Create Kareem launch shortcuts. With no flag, a Desktop "
                    "'Kareem' shortcut for the SILENT always-on listener is created."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--simple", action="store_true",
                       help="instead, create a separate Desktop 'Kareem (Simple "
                            "Mode)' shortcut that opens in the browser and quits "
                            "when you close the tab")
    group.add_argument("--autostart", action="store_true",
                       help="start the silent always-on listener at Windows login")
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
        if not _create_silent_shortcut(startup_shortcut):
            sys.exit(1)
        print("Kareem's silent listener will now start when you sign in to Windows.")
        return

    if args.simple:
        # Simple-mode Desktop icon — a SEPARATE, distinctly-named shortcut, so
        # the plain "Kareem" icon stays the silent always-on listener.
        if not SIMPLE_LAUNCHER.exists():
            print(f"Couldn't find {SIMPLE_LAUNCHER}. Make sure run_kareem_simple.bat "
                  "is in the Kareem folder.")
            sys.exit(1)
        shortcut_path = _desktop_dir() / "Kareem (Simple Mode).lnk"
        if not _create_shortcut(
            shortcut_path, SIMPLE_LAUNCHER, "",
            "Open Kareem in your browser — closing the tab quits Kareem",
        ):
            sys.exit(1)
        print("Double-click 'Kareem (Simple Mode)' to open Kareem in your browser. "
              "Close that browser tab to quit.")
        return

    # Default: the plain "Kareem" Desktop icon = silent always-on listener.
    shortcut_path = _desktop_dir() / "Kareem.lnk"
    if not _create_silent_shortcut(shortcut_path):
        sys.exit(1)
    print("Double-click 'Kareem' on your Desktop to start the silent background "
          "listener. (For the browser version, run this with --simple.)")


if __name__ == "__main__":
    main()
