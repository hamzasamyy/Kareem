"""
Tool registry — this is how the AI brain (Ollama or Claude) is given the
ability to *do* things instead of just talking.

Each tool is a plain Python function plus a "spec" describing its name,
description, and parameters as a JSON schema. The spec format here is
provider-neutral; brain.py translates it into whatever Ollama or Claude
expects, so tools only need to be written once.

To add a new tool: write a function in one of the modules in this folder,
decorate it with @register({...spec...}), and make sure the module is
imported in load_tools() below.
"""

# Maps tool name -> callable Python function.
TOOL_FUNCTIONS = {}

# List of neutral tool specs describing each tool to the LLM.
TOOL_SPECS = []


def register(spec: dict):
    """Decorator: registers a function as a callable tool for the brain.

    Usage:
        @register({
            "name": "open_app",
            "description": "Open a desktop application by name.",
            "parameters": {"type": "object", "properties": {...}, "required": [...]},
        })
        def open_app(name: str) -> str:
            ...
    """
    def decorator(func):
        TOOL_FUNCTIONS[spec["name"]] = func
        TOOL_SPECS.append(spec)
        return func
    return decorator


def load_tools():
    """Import every tool module so their @register decorators run.

    Called once by the agent at startup. Modules that fail to import (for
    example a missing optional dependency) are skipped with a warning
    instead of crashing Kareem.
    """
    import importlib
    from pathlib import Path

    from kareem import config

    modules = ["apps", "app_control", "web", "browser", "vision", "files", "system", "code_exec", "claude_code", "tracker", "memory"]
    credentials_path = Path(__file__).resolve().parent.parent.parent / "credentials.json"
    if getattr(config, "GOOGLE_CALENDAR_ENABLED", True) and credentials_path.is_file():
        modules.append("calendar")
    elif not getattr(config, "GOOGLE_CALENDAR_ENABLED", True):
        print("Google Calendar unavailable (disabled in kareem/config.py)")
    else:
        print("Google Calendar unavailable (credentials.json is missing — see README)")
    from kareem.guc import auth as guc_auth
    if guc_auth.credentials_configured():
        modules.append("guc")
    else:
        print("GUC tools unavailable (GUC_USERNAME/GUC_PASSWORD are missing — see README)")
    for mod in modules:
        try:
            importlib.import_module(f"kareem.tools.{mod}")
        except Exception as e:
            print(f"Note: tools from '{mod}' are unavailable ({e})")
