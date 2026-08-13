"""
Vision fallback (Layer 3) — the LAST resort for clicking something Layers
0-2 (kareem/tools/apps.py's navigate_app, kareem/tools/browser.py's DOM-
based browser control, kareem/tools/app_control.py's UI-Automation-based
app control) couldn't find by name/role/label. Screenshots the screen,
asks a vision-capable model where the target is, and clicks those
coordinates with pyautogui.

Honest limitation, not a bug: this runs on Groq's qwen/qwen3.6-27b, which
Groq documents as a PREVIEW model ("intended for evaluation purposes
only... should not be used in production... may be discontinued at short
notice" — see kareem/config.py's VISION_MODEL comment). Pixel-based
clicking is also inherently fuzzier than accessibility-tree matching.
Expect this layer to need more iteration and occasional misses even after
this build — that's the nature of the approach, not something broken.

Every use is logged clearly (both printed and via log_action) so it's
easy to see how often Kareem actually needed this vs. the more reliable
layers.
"""

import base64
import io
import json

from kareem import config
from kareem.safety import log_action
from kareem.tools import register


def _capture_screenshot_b64():
    """Screenshot the primary monitor. Returns (base64 PNG, (width, height))."""
    import mss
    from PIL import Image

    with mss.mss() as sct:
        monitor = sct.monitors[1]  # [0] is "all monitors combined"; [1] is the primary
        shot = sct.grab(monitor)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii"), img.size


def _ask_vision_model(image_b64: str, description: str) -> dict:
    """Sends the screenshot + target description to Groq's vision model.
    Returns {"found": bool, "x": int, "y": int, "reasoning": str}. Raises
    on any failure (network, bad response, unparseable JSON) — the tool
    function below turns that into a plain-English result, never crashes
    Kareem. Deliberately a ONE-shot call with no internal retry loop: a
    preview model that fails once is unlikely to self-correct immediately,
    and the calling agent's own retry-with-different-tool loop (see
    agent.py's tightened error messages) is the right place for that
    decision, not a silent retry buried in here."""
    import os

    from dotenv import load_dotenv
    from openai import OpenAI

    load_dotenv()
    api_key = os.getenv("HOSTED_API_KEY")
    if not api_key:
        raise RuntimeError("No HOSTED_API_KEY configured — vision fallback needs a Groq key.")

    client = OpenAI(base_url="https://api.groq.com/openai/v1", api_key=api_key, timeout=30)
    prompt = (
        f"This is a screenshot of the user's screen. Find: {description}\n"
        "Respond with ONLY a JSON object, no other text, in exactly this form:\n"
        '{"found": true, "x": <int>, "y": <int>, "reasoning": "<short reason>"}\n'
        "x and y are the PIXEL coordinates of the CENTER of the target, "
        "measured from the top-left corner of THIS image. If you cannot "
        'find it, respond {"found": false, "x": 0, "y": 0, "reasoning": "<why not>"}.'
    )
    resp = client.chat.completions.create(
        model=getattr(config, "VISION_MODEL", "qwen/qwen3.6-27b"),
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ],
        }],
    )
    from kareem.brain import strip_think

    text = strip_think(resp.choices[0].message.content or "")
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    text = text.strip()
    # The model sometimes adds a stray sentence before/after the JSON.
    # Decode from each opening brace so braces in that prose cannot widen
    # the candidate into invalid JSON.
    decoder = json.JSONDecoder()
    for start, char in enumerate(text):
        if char != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and "found" in candidate:
            return candidate
    raise ValueError("Vision model did not return a valid result JSON object.")


@register({
    "name": "vision_click",
    "description": (
        "LAST RESORT ONLY, after browser_click/app_click have ALREADY been "
        "tried and failed to find the target by name/role/label. Locates a "
        "click target via screenshot + vision model and clicks an estimated "
        "pixel position (not a confirmed UI element) — less reliable, expect "
        "occasional misses. Every use is logged. Asks the user to confirm "
        "first for send/submit/delete-looking clicks; refuses outright for "
        "financial transactions, permanent deletion, or a CAPTCHA/bot-check."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "description": {
                "type": "string",
                "description": "Plain-language description of what to click on screen",
            },
        },
        "required": ["description"],
    },
})
def vision_click(description: str) -> str:
    from kareem.safety import refuse_if_hard_blocked_click

    blocked = refuse_if_hard_blocked_click("vision_click", description)
    if blocked:
        return blocked

    model = getattr(config, "VISION_MODEL", "qwen/qwen3.6-27b")
    print(f"(VISION FALLBACK: locating '{description}' via screenshot + {model})")
    log_action("vision_click", f"FALLBACK USED — {description}")

    try:
        image_b64, (width, height) = _capture_screenshot_b64()
    except Exception as e:
        log_action("vision_click", f"FALLBACK FAILED — {description}: screenshot: {e}")
        return f"Vision fallback couldn't capture a screenshot: {e}"

    try:
        result = _ask_vision_model(image_b64, description)
    except Exception as e:
        log_action("vision_click", f"FALLBACK FAILED — {description}: {e}")
        return (
            f"Vision fallback failed to locate '{description}' ({e}). This "
            "is the least reliable layer (preview-tier model) — try "
            "rephrasing the description, or ask the user to do this step "
            "themselves."
        )

    if not result.get("found"):
        reason = result.get("reasoning", "no reason given")
        log_action("vision_click", f"FALLBACK: not found — {description} ({reason})")
        return f"Vision fallback couldn't find '{description}' on screen: {reason}"

    try:
        x, y = int(result.get("x", 0)), int(result.get("y", 0))
    except (TypeError, ValueError):
        log_action("vision_click", f"FALLBACK FAILED — {description}: non-numeric coordinates")
        return f"Vision fallback returned non-numeric coordinates: {result!r}"

    if not (0 <= x < width and 0 <= y < height):
        log_action("vision_click", f"FALLBACK FAILED — {description}: out-of-range ({x}, {y})")
        return (
            f"Vision fallback returned out-of-range coordinates ({x}, {y}) "
            f"for a {width}x{height} screenshot — refusing to click blindly."
        )

    def _do_click():
        import pyautogui
        pyautogui.click(x, y)
        log_action("vision_click", f"FALLBACK: clicked ({x}, {y}) for '{description}'")
        return (
            f"Vision fallback clicked '{description}' at approximately ({x}, {y}) "
            f"on screen — reasoning: {result.get('reasoning', '')}. This was a "
            "screenshot-estimated click (preview-tier vision model), not a "
            "confirmed UI element — verify the result looks right."
        )

    from kareem.safety import classify_click_intent, guard

    try:
        if classify_click_intent(description) == "risky":
            return guard(
                "vision_action_click",
                f"Click '{description}' at approximately ({x}, {y}) on screen "
                "(looks like it sends, submits, confirms, or deletes something)",
                _do_click,
            )
        return _do_click()
    except Exception as e:
        return f"Vision fallback located '{description}' at ({x}, {y}) but the click itself failed: {e}"
