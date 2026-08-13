"""
System-level tools: running shell commands, shutdown/restart.
Every function here is destructive-capable and is routed through
kareem.safety.guard() — nothing runs without the user saying yes.
"""

import subprocess
import sys

from kareem.safety import guard
from kareem.tools import register

COMMAND_TIMEOUT = 60  # seconds


@register({
    "name": "run_command",
    "description": (
        "Run a Windows shell (PowerShell) command and return its output. "
        "DESTRUCTIVE-CAPABLE: the user will be asked to confirm the exact "
        "command before it runs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run"},
        },
        "required": ["command"],
    },
})
def run_command(command: str) -> str:
    def _do_run():
        if sys.platform != "win32":
            return "run_command only supports Windows (PowerShell) right now."
        try:
            result = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True,
                text=True,
                timeout=COMMAND_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"The command didn't finish within {COMMAND_TIMEOUT} seconds and was stopped."

        output = (result.stdout or "").strip()
        errors = (result.stderr or "").strip()
        parts = []
        if output:
            parts.append(output[:4000])
        if errors:
            parts.append(f"[errors]\n{errors[:2000]}")
        parts.append(f"(exit code {result.returncode})")
        return "\n".join(parts)

    return guard("run_shell_command", f"Run this shell command: {command}", _do_run)


@register({
    "name": "shutdown_or_restart",
    "description": (
        "Shut down or restart the user's computer. DESTRUCTIVE: the user "
        "will be asked to confirm first."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["shutdown", "restart"],
                "description": "Whether to shut down or restart",
            },
        },
        "required": ["action"],
    },
})
def shutdown_or_restart(action: str) -> str:
    flag = "/s" if action == "shutdown" else "/r"

    def _do_it():
        subprocess.Popen(["shutdown", flag, "/t", "10"])
        return f"OK — the PC will {action} in 10 seconds. Run 'shutdown /a' to abort."

    return guard("shutdown_or_restart", f"{action.capitalize()} this computer", _do_it)
