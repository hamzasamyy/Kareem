"""
The agent loop: takes user input, hands it to the active brain along with
the tool registry, executes any tools the model asks for, and returns the
final reply.
"""

from kareem import tool_routing, tools
from kareem.brain import get_brain
from kareem.safety import log_action

KEEP_RECENT_TURNS = 12
TRIM_TRIGGER_TURNS = KEEP_RECENT_TURNS + 6

# Hard cap on how much text ANY single tool result can add to the
# conversation, applied in _execute_tool below regardless of which tool
# produced it. Root cause of the "who won the last F1 race?" failure: a
# single clean web_search -> fetch_page round on a Wikipedia page (already
# soft-truncated to 4000 chars there, ~1000-1300 tokens) landed on top of
# the full tool-schema + conversation-history overhead already in the
# request and pushed a single Groq call to 8750 tokens against its 8000
# tokens/minute limit — an outright rejection, not a slow burn from
# multiple searches (that's the separate redundancy/cascade fix in
# web_search's tool description). A per-tool truncation like fetch_page's
# is easy to miss adding to some future tool, or to still be too generous
# once schema/history overhead grows — this backstop applies AFTER any
# tool-specific truncation, to every tool's output, so no single result can
# blow the budget on its own no matter where it came from.
# ~1800 chars is roughly 450 tokens at a ~4-chars/token estimate, leaving
# headroom under an 8000 token/minute cap even next to a full tool schema.
MAX_TOOL_RESULT_CHARS = 1800

# Tool names that can, depending on their arguments, call
# safety.guard()/confirm() (exhaustively verified via `grep "guard(" -r
# kareem/tools/` at the time this was written). A batch of same-round tool
# calls is run concurrently in _execute_tool_calls() below ONLY when none of
# them are in this set; a batch containing any of them runs sequentially in
# the original order instead — exactly how every tool call worked before
# this optimization existed — because two concurrent guard() calls would
# race on the single confirmation slot (safety.py's one _voice_ask_fn, the
# web server's one "pending" confirmation per connection).
# IMPORTANT: if you add a guard()/confirm() call to a new or existing tool,
# add its name here too, or a future batch could run it concurrently with
# something else and corrupt the confirmation flow.
_CONFIRMATION_CAPABLE_TOOLS = {
    "delete_file", "move_file",
    "run_command", "run_python", "run_claude_code", "shutdown_or_restart",
    "calendar_delete_event",
    "browser_click", "app_click", "vision_click",
}

SYSTEM_PROMPT = (
    "You are Kareem, a desktop assistant on the user's Windows PC. "
    "Use your tools to act, then answer concisely in plain language. "
    "Your answers are spoken aloud — keep them to a few short sentences "
    "unless the user asks for detail. Write plain sentences with no markdown "
    "formatting (no asterisks, bullet lists, or code fences) unless the user "
    "explicitly asks for code. "
    "Risky tools (delete/move files, shell commands, code, shutdown) already "
    "make Kareem's built-in safety gate ask the user to confirm — so don't "
    "ask for permission yourself, just call the tool. "
    "Before calling a tool, check its required parameters against what the "
    "user actually asked for — don't guess a value (a filename, an id, a "
    "date) that wasn't stated or clearly implied. If the request is genuinely "
    "ambiguous or missing something a tool needs, ask one short clarifying "
    "question instead of picking an assumption and acting on it — a wrong "
    "guess that changes something is worse than asking. If a tool call "
    "fails, read the error message: it tells you exactly what was wrong and "
    "how to fix the call, so correct it and try again rather than giving up "
    "or repeating the identical call. "
    "Your training data has a cutoff and goes stale — default to calling "
    "web_search rather than answering from memory whenever a question could "
    "be time-sensitive: the current status, result, winner, score, or "
    "outcome of an ongoing or recent event; anything phrased with 'now', "
    "'current', 'latest', 'still', or 'has X happened'; or a specific "
    "real-world event with a scheduled date (a sports final, an election, "
    "a product launch, ...) — a scheduled date is informative but NOT "
    "authoritative on its own, so verify via search whether it has already "
    "happened, especially if that date is on or before today. When genuinely "
    "unsure whether something is settled or time-sensitive, search anyway — "
    "a wasted search costs little; a wrong, confidently-stated answer is far "
    "worse. "
    "EXCEPTION to the above, specifically for a follow-up that merely "
    "clarifies or confirms something you already answered earlier in THIS "
    "conversation (e.g. 'you know I mean X, right?', 'I meant Y', 'yes, "
    "that one') — that is not a new information request, so don't repeat a "
    "fresh web_search for it: answer directly from what you already found. "
    "Only search again if the clarification reveals your earlier answer was "
    "actually about the wrong thing (a different place/person/event that "
    "happens to share a name) — and if so, search ONCE more with the "
    "corrected, more specific detail rather than repeatedly mutating the "
    "query; if that one search still doesn't turn up something specific, "
    "say so plainly instead of continuing to guess at rephrasings. This "
    "exception is narrow: a follow-up asking about something genuinely new "
    "or different (a different day, a different detail not yet covered) "
    "still needs a fresh search like any other time-sensitive question."
)

# Small local models (qwen2.5:3b) fail SILENTLY — empty replies — when the
# system prompt gets long, so the local brain gets this trimmed version.
# Keep additions here SHORT for that reason (see the comment above) — this
# is a one-clause version of SYSTEM_PROMPT's cross-turn-clarification
# exception above, not the full explanation.
SYSTEM_PROMPT_SMALL = (
    "You are Kareem, a desktop assistant on the user's Windows PC. "
    "Use your tools to act, then answer concisely in plain language. "
    "Your training data is stale — use web_search instead of guessing for "
    "anything current, recent, or time-sensitive (scores, news, results, "
    "'has X happened yet'). Exception: don't re-search just to confirm "
    "something you already answered this conversation, unless it turns out "
    "to be about something different."
)


class Agent:
    @staticmethod
    def _system_message():
        """Build the baseline used both at launch and after a reset."""
        from kareem import config, memory

        prompt = SYSTEM_PROMPT_SMALL if config.BRAIN == "ollama" else SYSTEM_PROMPT
        memory_block = memory.format_memory_block()
        if memory_block:
            prompt += "\n\n" + memory_block
        return {"role": "system", "content": prompt}

    def __init__(self):
        from kareem import session_log

        tools.load_tools()
        self._log_tool_schema_size()
        self.brain = get_brain()
        session_log.start_session()
        self.history = [self._system_message()]

    @staticmethod
    def _log_tool_schema_size():
        """Print the full tool registry's size so schema bloat is visible
        at startup instead of silently costing latency on every request."""
        import json

        total_chars = sum(len(json.dumps(spec)) for spec in tools.TOOL_SPECS)
        print(f"Tools loaded: {len(tools.TOOL_SPECS)} "
              f"(full schema: {total_chars} chars, ~{total_chars // 4} tokens "
              "if all were sent — intent routing sends only the relevant "
              "subset per message, see kareem/tool_routing.py)")

    def reset(self):
        """Forget the conversation and rotate its best-effort disk log."""
        from kareem import session_log

        self.history = [self._system_message()]
        session_log.rotate_session()

    def resume(self, session_id: str) -> bool:
        """Reload a past session's visible conversation and keep logging into it.

        Tool calls are NOT re-run (their effects already happened) — but
        each tool's name/result IS folded into the following assistant
        reply as a bracketed note, because they used to be dropped
        entirely on resume. That was a real bug, not just an omission: if
        a fact only ever existed in a tool result (e.g. an item's ID) and
        the assistant's spoken reply didn't restate it verbatim ("Got it"
        rather than repeating the ID back), that fact was completely
        invisible to the model after any resume — reopening Kareem, or
        resuming an old session from the History panel — even though nothing
        else about the conversation seemed to have changed. Folding tool
        activity into the assistant message (rather than inserting separate
        messages) keeps strict user/assistant alternation, which several
        providers require.
        """
        from kareem import session_log

        events = session_log.read_session_events(session_id)
        if not events:
            return False
        history = [self._system_message()]
        pending_tool_notes = []
        for event in events:
            event_type = event.get("type")
            if event_type == "user_message":
                pending_tool_notes = []
                history.append({"role": "user", "content": event.get("text", "")})
            elif event_type == "tool_call":
                name = event.get("name", "?")
                args = event.get("args") or {}
                args_summary = ", ".join(f"{k}={v!r}" for k, v in args.items())
                pending_tool_notes.append(f"called {name}({args_summary})")
            elif event_type == "tool_result":
                if pending_tool_notes:
                    result = str(event.get("result", ""))[:300]
                    pending_tool_notes[-1] += f" -> {result}"
            elif event_type == "assistant_reply":
                text = event.get("text", "")
                if pending_tool_notes:
                    note = "[Tool activity this turn: " + "; ".join(pending_tool_notes) + "] "
                    text = note + text
                    pending_tool_notes = []
                history.append({"role": "assistant", "content": text})
        if not session_log.resume_session(session_id):
            return False
        self.history = history
        return True

    def _notify_tool(self, phase: str, name: str, detail: str = "", ok: bool = True):
        """Report tool activity to the current turn's on_tool callback (the web
        UI's activity feed). Guarded so a UI problem can never break a tool."""
        callback = getattr(self, "_on_tool", None)
        if callback is None:
            return
        try:
            callback({"phase": phase, "name": name, "detail": detail[:160], "ok": ok})
        except Exception:
            pass

    def _notify_tool_failure(self, name: str, detail: str):
        """Report a tool-calling failure that never reaches _execute_tool at
        all — excluded by routing, a provider validation error (e.g. Groq's
        "not in request.tools"), a hallucinated tool name the provider itself
        rejected, an empty response, or a stuck tool-calling loop. Without
        this, such failures were completely invisible outside raw console
        output: _notify_tool only ever fires from inside _execute_tool, which
        never runs when no tool call reaches our own code. Covers BOTH the
        live Activity feed (via _notify_tool) and the persisted session log,
        so a failure is debuggable even after the browser tab is gone.
        Passed to brain.chat() as on_tool_failure — see kareem/brain.py."""
        from kareem import session_log

        self._notify_tool("end", name, detail, ok=False)
        session_log.log_event("tool_result", name=name, result=detail, ok=False)

    @staticmethod
    def _cap_tool_result(result: str) -> str:
        """Hard-cap any tool result to MAX_TOOL_RESULT_CHARS before it's
        allowed into the conversation — see that constant's comment for why.
        Applies uniformly to every tool's output, on top of whatever
        tool-specific truncation (if any) already ran."""
        if len(result) <= MAX_TOOL_RESULT_CHARS:
            return result
        return result[:MAX_TOOL_RESULT_CHARS] + " …[truncated]"

    def _execute_tool(self, name: str, args: dict) -> str:
        """Runs one tool call from the model. Never raises — the model gets
        a readable error string instead, so one bad call can't crash Kareem."""
        from kareem import session_log

        session_log.log_event("tool_call", name=name, args=args)
        func = tools.TOOL_FUNCTIONS.get(name)
        if func is None:
            # A weak model occasionally hallucinates a close-but-wrong tool
            # name (e.g. "tracker_delete" instead of "tracker_remove").
            # Suggesting real near-matches turns that into a same-turn
            # self-correction instead of a dead end the model just gives up
            # on — this IS the "retry once with corrected parameters"
            # mechanism: the model's own multi-round tool loop (up to
            # MAX_TOOL_ROUNDS) already re-tries on the next round as long as
            # the error message gives it enough to fix; the fix here is
            # making that error message actually actionable.
            import difflib
            close = difflib.get_close_matches(name, tools.TOOL_FUNCTIONS.keys(), n=3, cutoff=0.5)
            suggestion = f" Did you mean: {', '.join(close)}?" if close else ""
            self._notify_tool("end", name, "unknown tool", ok=False)
            result = f"Unknown tool: {name}.{suggestion} Use one of the tools listed in your instructions."
            session_log.log_event("tool_result", name=name, result=result, ok=False)
            return result

        args_summary = ", ".join(f"{k}={v!r}" for k, v in args.items())
        print(f"  [tool] {name}({args_summary})")
        self._notify_tool("start", name, args_summary)
        # TEMP timing instrumentation (search-latency diagnosis, see task
        # report): actual wall-clock time for this ONE tool call's real work
        # (the network call itself for web_search/fetch_page), independent
        # of whether it ran concurrently with others in the same round — see
        # brain.py's "[tools]" line for the round's overall wall-clock.
        import time
        start = time.perf_counter()
        try:
            result = self._cap_tool_result(str(func(**args)))
            elapsed = time.perf_counter() - start
            print(f"  [tool] {name} finished in {elapsed:.2f}s")
            self._notify_tool("end", name, result)
            session_log.log_event("tool_result", name=name, result=result, ok=True)
            return result
        except TypeError as e:
            # Echo the tool's actual parameter schema back to the model so
            # a bad call can be corrected on the next round instead of
            # guessing again — without this, "Bad arguments: X" alone gives
            # a weak model nothing concrete to fix.
            spec = next((s for s in tools.TOOL_SPECS if s.get("name") == name), None)
            schema = (spec or {}).get("parameters", {}).get("properties", {})
            required = (spec or {}).get("parameters", {}).get("required", [])
            schema_hint = f" Expected parameters: {schema}. Required: {required}." if spec else ""
            self._notify_tool("end", name, f"bad arguments: {e}", ok=False)
            result = f"Bad arguments for {name}: {e}.{schema_hint} Correct the arguments and call the tool again."
            session_log.log_event("tool_result", name=name, result=result, ok=False)
            return result
        except Exception as e:
            log_action("tool_error", f"{name}: {e}")
            self._notify_tool("end", name, str(e), ok=False)
            result = f"The tool {name} failed: {e}"
            session_log.log_event("tool_result", name=name, result=result, ok=False)
            return result

    def _execute_tool_calls(self, calls: list[tuple[str, dict]]) -> list[str]:
        """Run one round's tool calls and return their results in the same
        order as `calls` — callers zip() this back against the provider's own
        tool_call_id/tool_use_id list, so the order must be preserved exactly.

        Runs concurrently (via a thread pool) when none of the calls can ever
        trigger the safety confirmation gate (see _CONFIRMATION_CAPABLE_TOOLS
        above): most rounds are independent read/search/navigate calls that
        gain real wall-clock time from not waiting on each other. The moment
        any call in the batch COULD show a confirmation dialog, the whole
        batch falls back to running sequentially, one at a time, in the
        original order — identical to how every tool call ran before this
        optimization existed.
        """
        if len(calls) <= 1 or any(name in _CONFIRMATION_CAPABLE_TOOLS for name, _ in calls):
            return [self._execute_tool(name, args) for name, args in calls]

        from concurrent.futures import ThreadPoolExecutor

        results = [None] * len(calls)
        with ThreadPoolExecutor(max_workers=len(calls)) as pool:
            futures = {
                pool.submit(self._execute_tool, name, args): index
                for index, (name, args) in enumerate(calls)
            }
            for future, index in futures.items():
                results[index] = future.result()
        return results

    def _trim_history_if_needed(self) -> None:
        """Summarize old complete turns without splitting tool exchanges."""
        user_indexes = [
            index for index, message in enumerate(self.history[1:], start=1)
            if message.get("role") == "user"
        ]
        if len(user_indexes) < TRIM_TRIGGER_TURNS:
            return

        first_kept_index = user_indexes[-KEEP_RECENT_TURNS]
        removed = self.history[1:first_kept_index]
        turns_summarized = len(user_indexes) - KEEP_RECENT_TURNS

        excerpt_lines = []
        for message in removed:
            role = message.get("role")
            content = message.get("content")
            if isinstance(content, list):
                rendered_parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text" and part.get("text"):
                        rendered_parts.append(part["text"])
                    elif part.get("type") == "image_url":
                        rendered_parts.append("[image attached]")
                content = " ".join(rendered_parts)
            if role in {"user", "assistant"} and content:
                excerpt_lines.append(f"{role.capitalize()}: {content}")
        excerpt = "\n".join(excerpt_lines)
        prompt = (
            "Summarize this earlier part of an ongoing conversation in 2-3 "
            "sentences, focusing on facts, decisions, or context that matter "
            f"for continuing it naturally:\n\n{excerpt}"
        )
        try:
            summary_text = self.brain.chat(
                [{"role": "user", "content": prompt}],
                tools=None,
                execute_tool=None,
            )
            if not summary_text:
                raise ValueError("empty conversation summary")
        except Exception:
            summary_text = (
                f"Earlier in this conversation: {turns_summarized} earlier "
                "exchanges not shown here."
            )

        summary_message = {
            "role": "assistant",
            "content": f"[Summary of earlier conversation: {summary_text}]",
        }
        self.history[1:first_kept_index] = [summary_message]

        from kareem import session_log
        session_log.log_event(
            "history_trimmed", turns_summarized=turns_summarized
        )

    def send(self, user_text: str, images: list[str] | None = None,
             on_sentence=None, on_token=None, on_tool=None) -> str:
        """on_sentence: optional callback that receives each finished sentence
        of the reply as it streams, so speech can start early.
        on_token: optional callback that receives each visible text chunk as it
        streams (used by the web UI to render the reply live).
        on_tool: optional callback({"phase","name","detail","ok"}) fired when a
        tool starts/finishes (used by the web UI's activity feed). Turns are
        serialized by the caller's agent_lock, so storing it on self is safe."""
        self._on_tool = on_tool
        if images:
            content = [{"type": "text", "text": user_text}]
            content.extend(
                {"type": "image_url", "image_url": {"url": image}}
                for image in images
            )
        else:
            content = user_text
        self.history.append({"role": "user", "content": content})
        from kareem import config, session_log
        session_log.log_event("user_message", text=user_text)
        # Which tool schemas to send this turn. The capable hosted/Claude brains
        # get the FULL set — trimming them was the source of the web-search and
        # calendar under-inclusion failures (a phrasing that missed the keyword
        # filter got its tool silently denied). Only the small local Ollama
        # model, which degrades with many tools, gets the filtered subset.
        # See kareem/tool_routing.py.
        relevant_tools = tool_routing.select_tools_for_message(
            tools.TOOL_SPECS, user_text, config.BRAIN
        )
        # TEMP timing instrumentation (search-latency diagnosis, see task
        # report): total wall-clock for this whole turn, for comparison
        # against the [groq]/[tool] breakdown lines brain.py/this file print
        # during the call below.
        import time
        turn_start = time.perf_counter()
        try:
            reply = self.brain.chat(
                self.history,
                tools=relevant_tools,
                execute_tool=self._execute_tool_calls,
                on_sentence=on_sentence,
                on_token=on_token,
                on_tool_failure=self._notify_tool_failure,
            )
        except Exception as e:
            # A brain that raises all the way out here (e.g. the hosted
            # provider rejected the request AND no local Ollama fallback was
            # available) already gets caught and shown as a generic error by
            # the caller (kareem/web/server.py's run_turn) — but that alone
            # never touched the Activity feed/session log (see
            # _notify_tool_failure's docstring). Notify once more here as a
            # catch-all, then re-raise unchanged so existing error handling
            # upstream is untouched.
            self._notify_tool_failure("tool_call", f"turn failed: {e}")
            raise
        finally:
            self._on_tool = None
        turn_elapsed = time.perf_counter() - turn_start
        print(f"[turn] total wall-clock: {turn_elapsed:.2f}s")
        self.history.append({"role": "assistant", "content": reply})
        session_log.log_event("assistant_reply", text=reply)
        self._trim_history_if_needed()
        return reply

    @property
    def last_streamed(self) -> bool:
        """True if the last reply was already live-printed by the brain."""
        return getattr(self.brain, "last_streamed", False)
