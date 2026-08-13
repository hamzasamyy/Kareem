"""LLM tool for manually checking the configured GUC systems."""

from kareem.guc import scheduler
from kareem.tools import register


@register({
    "name": "guc_check_now",
    "description": "Manually trigger a check of GUC (CMS/Portal/Mail) for new assignment/quiz/final deadlines, instead of waiting for the next scheduled background check.",
    "parameters": {"type": "object", "properties": {}},
})
def guc_check_now() -> str:
    result = scheduler.run_check_cycle()
    if not result.get("ok"):
        return f"GUC check failed: {result.get('error', 'unknown error')}"
    systems = ", ".join(result.get("systems_checked", [])) or "none"
    return (
        f"Checked GUC ({systems}) in {result.get('duration_seconds', 0):.1f}s — "
        f"found {result.get('items_found', 0)} items, auto-added "
        f"{result.get('items_auto_added', 0)}, flagged "
        f"{result.get('items_flagged', 0)} for review."
    )
