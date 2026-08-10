"""
Delegates a coding task to the Claude Code CLI (`claude`). DESTRUCTIVE-
CAPABLE — Claude Code can edit files and run commands in the target project,
so this always goes through jarvis.safety.guard() for confirmation first,
exactly like run_command/shutdown_or_restart.
"""

import json
import shutil
import subprocess
from pathlib import Path

from jarvis import config
from jarvis.safety import guard
from jarvis.tools import register

MAX_RESULT_CHARS = 4000


@register({
    "name": "run_claude_code",
    "description": (
        "Delegate a coding task to the Claude Code CLI — use this when the "
        "user asks to have Claude/Claude Code do, fix, build, or change "
        "something in code (e.g. 'ask Claude to fix the login bug', 'have "
        "Claude Code add a test for this'). DESTRUCTIVE-CAPABLE: it can edit "
        "files and run commands in the project; the user will be asked to "
        "confirm the exact task before it runs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "description": "A clear, specific description of the coding "
                               "task to hand to Claude Code, in the user's "
                               "own words/intent.",
            },
        },
        "required": ["task"],
    },
})
def run_claude_code(task: str) -> str:
    def _do_run():
        claude_path = shutil.which("claude")
        if not claude_path:
            return ("Claude Code isn't installed or isn't on PATH. "
                    "Install it from https://claude.com/claude-code and try again.")

        project_dir = config.CLAUDE_CODE_PROJECT_DIR
        if project_dir is None:
            project_dir = Path(__file__).resolve().parents[2]

        try:
            result = subprocess.run(
                [claude_path, "-p", task,
                 "--output-format", "json",
                 "--permission-mode", config.CLAUDE_CODE_PERMISSION_MODE],
                capture_output=True,
                text=True,
                timeout=config.CLAUDE_CODE_TIMEOUT_SECONDS,
                cwd=project_dir,
            )
        except subprocess.TimeoutExpired:
            return (f"Claude Code didn't finish within "
                    f"{config.CLAUDE_CODE_TIMEOUT_SECONDS} seconds and was stopped.")
        except Exception as e:
            return f"Couldn't run Claude Code: {e}"

        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        summary = ""
        if stdout:
            try:
                payload = json.loads(stdout)
                if isinstance(payload, dict) and isinstance(payload.get("result"), str):
                    summary = payload["result"].strip()
                else:
                    summary = stdout
            except (json.JSONDecodeError, TypeError):
                summary = stdout

        if not summary:
            summary = stderr or "Claude Code finished without returning a response."
        elif result.returncode != 0 and stderr:
            summary = f"{summary}\n\n[errors]\n{stderr}"

        if result.returncode != 0:
            summary = f"Claude Code exited with code {result.returncode}.\n{summary}"

        if len(summary) > MAX_RESULT_CHARS:
            summary = (summary[:MAX_RESULT_CHARS].rstrip()
                       + "\n\n[Claude Code response truncated for readability]")
        return summary

    description = f"Ask Claude Code to: {task}"
    return guard("run_claude_code", description, _do_run)
