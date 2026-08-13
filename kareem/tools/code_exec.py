"""
Python code execution tool: runs a snippet in a separate Python process
with a timeout and returns whatever it prints. Routed through
kareem.safety.guard() since arbitrary code is inherently risky — the user
sees the exact code and must confirm before it runs.
"""

import subprocess
import sys

from kareem.safety import guard
from kareem.tools import register

CODE_TIMEOUT = 30  # seconds


@register({
    "name": "run_python",
    "description": (
        "Execute a Python code snippet and return its printed output. "
        "The code must print() anything it wants to show. The user will be "
        "asked to confirm the exact code before it runs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "The Python code to execute"},
        },
        "required": ["code"],
    },
})
def run_python(code: str) -> str:
    def _do_run():
        try:
            result = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True,
                text=True,
                timeout=CODE_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return f"The code didn't finish within {CODE_TIMEOUT} seconds and was stopped."

        output = (result.stdout or "").strip()
        errors = (result.stderr or "").strip()
        if result.returncode == 0:
            return output or "(the code ran fine but printed nothing)"
        return f"The code failed:\n{errors[:2000]}"

    preview = code if len(code) <= 300 else code[:300] + " …"
    return guard("run_code", f"Run this Python code:\n---\n{preview}\n---", _do_run)
