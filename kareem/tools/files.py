"""
File tools.
- Read-only operations (list/find/read) run without confirmation.
- move_file and delete_file are DESTRUCTIVE and always go through the
  safety gate (kareem.safety.guard), which asks the user to confirm first.
"""

import shutil
from pathlib import Path

from kareem.safety import guard, log_action
from kareem.tools import register

MAX_RESULTS = 50
MAX_READ_CHARS = 6000


@register({
    "name": "list_folder",
    "description": "List the files and subfolders inside a folder on the user's PC.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": r"Folder path, e.g. C:\Users\yourname\Downloads",
            },
        },
        "required": ["path"],
    },
})
def list_folder(path: str) -> str:
    log_action("list_folder", path)
    folder = Path(path).expanduser()
    if not folder.exists():
        return f"That folder doesn't exist: {folder}"
    if not folder.is_dir():
        return f"That path is a file, not a folder: {folder}"

    entries = sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    if not entries:
        return f"{folder} is empty."

    lines = []
    for p in entries[:MAX_RESULTS]:
        if p.is_dir():
            lines.append(f"[folder] {p.name}")
        else:
            kb = max(1, p.stat().st_size // 1024)
            lines.append(f"{p.name}  ({kb} KB)")
    more = f"\n…and {len(entries) - MAX_RESULTS} more" if len(entries) > MAX_RESULTS else ""
    return f"Contents of {folder}:\n" + "\n".join(lines) + more


@register({
    "name": "find_files",
    "description": (
        "Search a folder (and its subfolders) for files matching a pattern, "
        "e.g. '*.pdf' or 'report*'. Returns matching paths."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "folder": {"type": "string", "description": "Folder to search in"},
            "pattern": {"type": "string", "description": "Filename pattern, e.g. *.pdf"},
        },
        "required": ["folder", "pattern"],
    },
})
def find_files(folder: str, pattern: str) -> str:
    log_action("find_files", f"{pattern} in {folder}")
    root = Path(folder).expanduser()
    if not root.is_dir():
        return f"That folder doesn't exist: {root}"

    matches = []
    try:
        for p in root.rglob(pattern):
            matches.append(str(p))
            if len(matches) >= MAX_RESULTS:
                break
    except PermissionError:
        pass

    if not matches:
        return f"No files matching '{pattern}' found under {root}."
    return f"Found {len(matches)} file(s):\n" + "\n".join(matches)


@register({
    "name": "read_file",
    "description": "Read the text contents of a file (truncated if long). Read-only.",
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Full path of the file to read"},
        },
        "required": ["path"],
    },
})
def read_file(path: str) -> str:
    log_action("read_file", path)
    file = Path(path).expanduser()
    if not file.exists():
        return f"That file doesn't exist: {file}"
    if file.is_dir():
        return f"That's a folder, not a file: {file} (use list_folder instead)"

    try:
        text = file.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        return f"Couldn't read {file}: {e}"

    if len(text) > MAX_READ_CHARS:
        text = text[:MAX_READ_CHARS] + "\n…[truncated]"
    return text or "(the file is empty)"


@register({
    "name": "move_file",
    "description": (
        "Move or rename a file or folder. DESTRUCTIVE: the user will be "
        "asked to confirm before anything happens."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Current full path"},
            "destination": {"type": "string", "description": "New full path (or existing folder to move into)"},
        },
        "required": ["source", "destination"],
    },
})
def move_file(source: str, destination: str) -> str:
    src = Path(source).expanduser()
    dst = Path(destination).expanduser()
    if not src.exists():
        return f"Source doesn't exist: {src}"
    if dst.is_dir():
        dst = dst / src.name
    if dst.exists():
        return f"Refusing to overwrite — something already exists at {dst}"

    def _do_move():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        return f"Moved {src} -> {dst}"

    return guard("move_file", f"Move '{src}' to '{dst}'", _do_move)


@register({
    "name": "delete_file",
    "description": (
        "Delete a file or an entire folder. DESTRUCTIVE: the user will be "
        "asked to confirm before anything happens."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Full path of the file or folder to delete"},
        },
        "required": ["path"],
    },
})
def delete_file(path: str) -> str:
    target = Path(path).expanduser()
    if not target.exists():
        return f"Nothing to delete — {target} doesn't exist."

    if target.is_dir():
        count = sum(1 for _ in target.rglob("*"))
        description = f"Delete the FOLDER '{target}' and everything inside it ({count} items)"
        def _do_delete():
            shutil.rmtree(target)
            return f"Deleted folder {target}"
    else:
        description = f"Delete the file '{target}'"
        def _do_delete():
            target.unlink()
            return f"Deleted file {target}"

    return guard("delete_file", description, _do_delete)
