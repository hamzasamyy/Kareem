"""
Jarvis configuration — this is the ONE file you edit to change how Jarvis behaves.

Every setting has a comment explaining what it does. Change a value, save the
file, restart Jarvis (`python main.py`), and the new setting takes effect.
"""

# ============================================================================
# THE BRAIN — which AI model powers Jarvis
# ============================================================================

# "hosted" = free cloud model on the provider's GPU (fast; needs a free key in .env)
# "ollama" = free local model running on your PC (slower, but fully private/offline)
# "claude" = Anthropic's Claude API (smartest, costs money, needs a key in .env)
#
# Honest note on tool-calling reliability: the free hosted model
# (openai/gpt-oss-120b) has a real capability ceiling — sharper tool
# descriptions, better error messages, and a tighter system prompt (already
# done in jarvis/agent.py) reduce wrong tool calls and misread requests, but
# can't fully eliminate them; that's the model, not a bug. If reliability is
# still unsatisfying after those, the documented fix is switching this to
# "claude" — Claude follows instructions and picks tool parameters far more
# consistently. It costs money per request; the free brain is deliberately
# the default.
BRAIN = "hosted"

# --- Hosted settings (used when BRAIN = "hosted") ---
# Works with ANY OpenAI-compatible provider — pick one by setting the base URL
# and model id below, and putting that provider's API key in .env as
# HOSTED_API_KEY. Ready-made choices (verified 2026-07-17):
#
# 1) Groq (default — fastest by far: LPU chips, sub-second first token):
#      key from https://console.groq.com (free, no credit card)
#      HOSTED_BASE_URL = "https://api.groq.com/openai/v1"
#      HOSTED_MODEL = "openai/gpt-oss-120b"   (smart, reliable tool calls)
#    Tested alternates on Groq: "qwen/qwen3-32b",
#    "meta-llama/llama-4-scout-17b-16e-instruct".
#    AVOID "llama-3.3-70b-versatile" here — it often emits malformed tool
#    calls on Groq ("tool_use_failed" errors) which breaks Jarvis's tools.
#    Free-tier limits (per model): ~30 requests/min plus daily token caps.
#    Plenty for personal use; if you hit them, Jarvis's auto-retry waits it
#    out and the Ollama fallback below catches longer outages.
#
# 2) OpenRouter (alternate — ":free" models, shared pool so often slower/busy):
#      key from https://openrouter.ai/keys
#      HOSTED_BASE_URL = "https://openrouter.ai/api/v1"
#      HOSTED_MODEL = "nvidia/nemotron-3-super-120b-a12b:free"
#    Other free tool-capable ids there: "openai/gpt-oss-20b:free",
#    "qwen/qwen3-next-80b-a3b-instruct:free", "meta-llama/llama-3.3-70b-instruct:free".
#    NOTE: OpenRouter's free Hermes ("nousresearch/hermes-3-llama-3.1-405b:free")
#    canNOT call tools, so it would break Jarvis's app/file/web abilities.
#
# 3) Nous Research portal (the real Hermes 4 — free signup credits, then paid):
#      key from https://portal.nousresearch.com
#      HOSTED_BASE_URL = "https://inference-api.nousresearch.com/v1"
#      HOSTED_MODEL = "nousresearch/hermes-4-70b"
#
# (NVIDIA NIM also works the same way: https://integrate.api.nvidia.com/v1
#  with a key from build.nvidia.com and a model id from their catalog.)
# ────────────────────────────────────────────────────────────────────────
# ACTIVE HOSTED PROVIDER — flip this ONE line to switch or revert.
#   "nous" = the real Hermes 4 70B via Nous Portal (paid after ~$5 free
#            credits; key in .env as NOUS_API_KEY). NOTE: this endpoint's
#            model metadata reports tools:false — Jarvis's tool-calling on it
#            is under test; if tools don't work, set this back to "groq".
#   "groq" = free, fast Groq openai/gpt-oss-120b with reliable tool calls
#            (the long-standing default; key in .env as HOSTED_API_KEY).
# ────────────────────────────────────────────────────────────────────────
HOSTED_PROVIDER = "groq"

if HOSTED_PROVIDER == "nous":
    HOSTED_BASE_URL = "https://inference-api.nousresearch.com/v1"
    HOSTED_MODEL = "nousresearch/hermes-4-70b"   # exact id from the live /v1/models list
    HOSTED_API_KEY_ENV = "NOUS_API_KEY"
else:  # "groq" — kept fully intact so reverting is the one-line change above
    HOSTED_BASE_URL = "https://api.groq.com/openai/v1"
    # Vision on Groq: meta-llama/llama-4-scout-17b-16e-instruct.
    # The default openai/gpt-oss-120b is text-only.
    HOSTED_MODEL = "openai/gpt-oss-120b"
    HOSTED_API_KEY_ENV = "HOSTED_API_KEY"

# If the hosted endpoint is unreachable or rate-limited, automatically answer
# with the local Ollama model instead (when it's running). Jarvis prints a
# note whenever this happens. Set to False to disable.
HOSTED_FALLBACK_TO_OLLAMA = True

# Stream replies word-by-word and start speaking as soon as the first
# sentence is ready, instead of waiting for the whole answer. Only affects
# the hosted brain. Set to False to go back to wait-then-speak.
STREAMING = True

# --- Ollama settings (used when BRAIN = "ollama") ---
# The one-line escape hatch: set BRAIN = "ollama" above to go back to the
# fully-private offline brain anytime.
# The model must support tool calling. Image input also needs a vision model
# such as llava; the default qwen2.5:3b is text-only.
# qwen2.5:3b = fast on this laptop's CPU (default).
# qwen2.5:7b = smarter but ~2x slower; already downloaded, just change this
#              line to switch back.
OLLAMA_MODEL = "qwen2.5:3b"
OLLAMA_URL = "http://localhost:11434"

# --- Claude settings (used when BRAIN = "claude") ---
# The API key is NOT stored here. Put it in a file named `.env` next to
# main.py (copy `.env.example` to `.env` and fill it in).
# Current Claude models support image input.
CLAUDE_MODEL = "claude-sonnet-5"

# --- Vision fallback (jarvis/tools/vision.py's vision_click — general
# command execution's Layer 3) ---
# Used ONLY as a last resort, when browser_click/app_click can't find a
# target by name/role/label: screenshots the screen and asks a vision
# model where to click. Always calls Groq specifically (reusing
# HOSTED_API_KEY), regardless of what BRAIN is set to above, since a
# fixed vision-capable model is required for this to work at all.
# Verified current as of 2026-07-19: qwen/qwen3.6-27b is Groq's vision
# model, but Groq documents it under "Preview Models" — "intended for
# evaluation purposes only... should not be used in production... may be
# discontinued at short notice." That's the honest ceiling here: expect
# this layer to need more iteration and occasional misses even after this
# build, both from the model's preview status and because pixel-based
# clicking is inherently fuzzier than accessibility-tree matching
# (Layers 0-2). Not a bug if it's imperfect — check Groq's model list if
# this ever starts erroring, the id may have changed.
VISION_MODEL = "qwen/qwen3.6-27b"

# ============================================================================
# CLAUDE CODE DELEGATION (optional) — lets Jarvis hand off coding tasks
# ============================================================================

# The directory Claude Code should treat as its project root when Jarvis
# delegates a task to it. Defaults to Jarvis's own project folder; change
# this if you want voice/text requests like "ask Claude to fix the bug in
# my other project" to target a different folder.
CLAUDE_CODE_PROJECT_DIR = None  # None = Jarvis's own project root

# How Claude Code is allowed to act on this delegated task, since there's no
# interactive terminal for it to ask permission through (Jarvis's own
# safety.py confirmation already happened before this runs).
# "acceptEdits" = auto-accept file edits, still cautious about other actions
#                 (recommended default).
# "bypassPermissions" = no restrictions at all — only use this if you trust
#                 whatever Jarvis might ask it to do completely.
CLAUDE_CODE_PERMISSION_MODE = "acceptEdits"

# Give up and report a timeout if Claude Code hasn't finished in this long.
CLAUDE_CODE_TIMEOUT_SECONDS = 300

# --- Google Calendar ---
# Lets Jarvis create/list/delete events on your real Google Calendar when
# you ask it to. Needs a one-time Google Cloud OAuth setup — see README.
# Automatically disabled (tools simply aren't offered) if credentials.json
# is missing, so this is safe to leave True even before you've set it up.
GOOGLE_CALENDAR_ENABLED = True

# --- GUC (German University in Cairo) integration ---
# How often Jarvis checks CMS/Portal/Mail for new deadlines while running,
# in minutes. A random +/-5 minutes is added each cycle so it isn't a
# perfectly robotic interval. Automatically disabled if GUC_USERNAME/
# GUC_PASSWORD aren't set in .env — safe to leave as-is either way.
GUC_CHECK_INTERVAL_MINUTES = 45

# ============================================================================
# VOICE
# ============================================================================

# Master switch for all voice features (wake word, hotkey, speech, talking).
# Set to False to use Jarvis as a text-only chat in the console.
VOICE_ENABLED = True

# Wake word: say "hey jarvis" out loud to start talking (needs a microphone).
# If the wake-word engine fails to start on your PC, Jarvis automatically
# falls back to hotkey-only and tells you — it will not crash.
WAKE_WORD_ENABLED = True

# Global hotkey: press this key combo anywhere in Windows to start talking.
# Format examples: "ctrl+alt+j", "ctrl+shift+space"
HOTKEY = "ctrl+alt+j"

# Speech-to-text model (faster-whisper). Bigger = more accurate but slower:
#   "tiny.en"  = fastest, least accurate
#   "base.en"  = the default — clearly more accurate than tiny for ~2x the
#                work, still near-instant on CPU for short commands
#   "small.en" = noticeably more accurate again, ~5-6x tiny (use if your PC
#                has the headroom and accuracy matters more than speed)
# The model is pre-warmed at startup, so a larger model doesn't slow down
# your FIRST command. Switch back to "tiny.en" if replies ever feel laggy.
STT_MODEL = "base.en"

# How the speech model runs on the CPU. "int8" = fastest, minimal accuracy
# cost. Alternative: "int8_float32" if you ever see garbled transcriptions.
STT_COMPUTE_TYPE = "int8"

# Optional custom vocabulary to bias speech recognition toward names and
# domain terms it would otherwise mishear — your own name, friends' names,
# app names, GUC course-code prefixes (CSEN, DMET, MET…), and so on. Passed
# to Whisper as an initial-prompt hint. Keep it SHORT (a dozen or so words
# you actually say): a very long list makes the recognizer over-eager to
# hear those words even when you didn't. Empty list = no biasing.
STT_VOCABULARY = ["Kareem"]

# Which engine transcribes your voice commands.
# "local" = faster-whisper running on this PC (default) — your voice never
#           leaves the machine.
# "groq"  = send audio to Groq's hosted whisper-large-v3-turbo instead —
#           faster AND more accurate than any local model here, using the
#           same HOSTED_API_KEY as the hosted brain. PRIVACY TRADE-OFF: your
#           mic audio leaves your PC. Falls back to "local" automatically if
#           this fails (no key, no internet, Groq outage) — never silently
#           drops your command.
STT_ENGINE = "local"

# Where voice input on the WEBSITE is transcribed (the desktop app's own
# wake-word/hotkey path always uses faster-whisper locally, unaffected by
# this setting).
# "backend" = your mic audio is sent to Jarvis's own backend and transcribed
#             with faster-whisper (same model as the desktop app) — generally
#             much more accurate than the browser's built-in recognizer.
#             Needs ffmpeg on PATH (already required — see README).
# "browser" = use the browser's built-in speech recognition only (works with
#             no extra setup, less accurate).
# If "backend" is selected but ffmpeg/faster-whisper aren't usable, Jarvis
# automatically falls back to "browser" for that session and prints why.
WEB_STT_SOURCE = "backend"

# ============================================================================
# VOICE OUTPUT
# ============================================================================

# Which text-to-speech engine speaks Jarvis's replies.
# "piper"   = offline neural voice, and the default — measured RTF ~0.18 on
#             this machine's CPU (near-instant: a 4s reply takes ~0.7s to
#             synthesize), a big responsiveness win over Kokoro below.
# "kokoro"  = more naturally expressive voice, but measured RTF ~1.1-2.2 here
#             (a 4s reply can take 5-8s to *start* speaking) — pick this only
#             if you value natural prosody over snappy replies. Needs:
#             pip install kokoro-onnx soundfile, plus the two model files in
#             jarvis/voice/models/kokoro/ (see README).
# "pyttsx3" = the basic built-in Windows voice — always works, robotic.
# Whatever you pick, Jarvis automatically falls back down kokoro -> piper ->
# pyttsx3 if an engine can't start, and tells you why instead of crashing.
TTS_ENGINE = "piper"

# Kokoro voice id (only used when TTS_ENGINE = "kokoro"). British male
# voices: "bm_george" (calm, butler-ish), "bm_lewis" (deeper). American
# males: "am_adam", "am_michael".
# Full list: https://github.com/thewh1teagle/kokoro-onnx (voices-v1.0)
TTS_VOICE = "bm_george"

# Speaking speed multiplier (1.0 = normal, 1.1 = slightly brisker).
TTS_SPEED = 1.0

# NOTE for later: if this PC turns out to have an NVIDIA GPU, "Chatterbox"
# is an optional higher-quality neural voice we could add as another engine
# here. Not built yet — Kokoro is the quality/latency sweet spot on CPU.

# ============================================================================
# WEB INTERFACE
# ============================================================================

# When you say "hey jarvis" or press the hotkey, Jarvis opens (or focuses)
# the web interface at http://127.0.0.1:8000 in your browser, and the
# conversation continues there. Set to False to get the old behavior back
# (talk out loud immediately, replies spoken from the desktop app).
WAKE_OPENS_WEB = True

# ============================================================================
# SYSTEM TRAY
# ============================================================================

# Shows a small tray icon while Jarvis is running, with a "Quit" option —
# handy when running in the background (see the Desktop shortcut in the
# README). Set to False to disable entirely.
TRAY_ICON_ENABLED = True

# ============================================================================
# SAFETY
# ============================================================================

# "strict"  = confirm before ANY action that changes anything on your PC
# "normal"  = confirm only before risky actions (delete/move files, shell
#             commands, running code, shutdown/restart) — recommended
# Never disable confirmation entirely; there is deliberately no "off" value.
SAFETY_LEVEL = "normal"

# Every action Jarvis takes is written to this log file (created automatically).
LOG_FILE = "jarvis.log"
