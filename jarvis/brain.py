"""
Brain abstraction — swaps between a local Ollama model and the Claude API
without the rest of Jarvis needing to know which one is active.

Which brain is used is controlled entirely by jarvis/config.py (BRAIN =
"ollama" or "claude"). Both classes expose the same chat() method so
agent.py never has to branch on which provider is running.

Tool calling: chat() accepts a list of tool specs (provider-neutral format,
see jarvis/tools/__init__.py) and an execute_tool(calls) callback, where
calls is a list of (name, args) pairs for everything the model asked for in
one round; it returns a list of result strings in the SAME order (see
jarvis.agent.Agent._execute_tool_calls — that's also where same-round calls
may run concurrently). Each brain translates the specs into its own
provider's format, runs the model→tool→model loop internally, and returns
the final text reply.
"""

from jarvis import config

# Safety valve: the model can chain at most this many rounds of tool calls
# for a single user message, so a confused model can't loop forever.
MAX_TOOL_ROUNDS = 8


class ThinkFilter:
    """Streaming version of strip_think(): feed it text chunks as they arrive
    and it returns only the text that is safely outside <think>…</think>
    blocks — even when a tag is split across two chunks."""

    def __init__(self):
        self._buf = ""
        self._inside = False

    def push(self, text: str) -> str:
        self._buf += text
        out = []
        while True:
            low = self._buf.lower()
            if self._inside:
                end = low.find("</think>")
                if end < 0:
                    # keep just enough to recognize a tag split across chunks
                    self._buf = self._buf[-7:]
                    break
                self._buf = self._buf[end + len("</think>"):]
                self._inside = False
            else:
                start = low.find("<think>")
                if start < 0:
                    # emit all but a small tail that might be a partial "<think"
                    safe = len(self._buf) - 6
                    if safe > 0:
                        out.append(self._buf[:safe])
                        self._buf = self._buf[safe:]
                    break
                out.append(self._buf[:start])
                self._buf = self._buf[start + len("<think>"):]
                self._inside = True
        return "".join(out)

    def flush(self) -> str:
        """Call at end of stream: returns whatever held-back text is real."""
        if self._inside:
            self._buf = ""
            return ""
        out, self._buf = self._buf, ""
        return out


class SentenceBuffer:
    """Collects streamed text and hands complete sentences to a callback, so
    speech can start while the rest of the answer is still generating."""

    # Common abbreviations ending in a period that do NOT end a sentence —
    # without this, "Dr. Smith called" would split into "Dr." (spoken as an
    # isolated, oddly-paused fragment) and "Smith called", which is exactly
    # what made streamed speech sound choppy/cut-off on ordinary sentences,
    # not just course-code text.
    _ABBREVIATIONS = {
        "dr", "mr", "mrs", "ms", "prof", "st", "vs", "etc", "jr", "sr",
        "approx", "no", "vol", "fig", "dept", "univ", "e.g", "i.e", "inc",
        "ltd", "co", "u.s", "u.k",
    }

    def __init__(self, on_sentence):
        self.on_sentence = on_sentence
        self._buf = ""

    def push(self, text: str):
        if not self.on_sentence:
            return
        import re

        self._buf += text
        search_from = 0
        while True:
            m = re.search(r"[.!?](?=\s)|\n", self._buf[search_from:])
            if not m:
                break
            abs_end = search_from + m.end()
            if self._buf[abs_end - 1] == "." and self._ends_with_abbreviation(self._buf[:abs_end]):
                # Not a real sentence boundary — keep scanning past it
                # rather than splitting here.
                search_from = abs_end
                continue
            sentence = self._buf[:abs_end].strip()
            self._buf = self._buf[abs_end:]
            search_from = 0
            if len(sentence) > 1:
                self.on_sentence(sentence)

    @classmethod
    def _ends_with_abbreviation(cls, text: str) -> bool:
        import re

        # Include internal periods so dotted abbreviations present in the
        # allow-list ("e.g.", "i.e.", "U.S.") can actually match.
        m = re.search(r"([\w.]+)\.$", text)
        return bool(m) and m.group(1).lower() in cls._ABBREVIATIONS

    def flush(self):
        if self.on_sentence and self._buf.strip():
            self.on_sentence(self._buf.strip())
        self._buf = ""


def strip_think(text: str) -> str:
    """Remove <think>…</think> reasoning traces some hosted models emit, so
    Jarvis never prints (or reads aloud!) its internal thoughts. Also handles
    an unclosed <think> at the start of a reply."""
    import re

    if not text:
        return text
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # unclosed trace: everything from a dangling <think> onward is not an answer
    text = re.sub(r"<think>.*", "", text, flags=re.DOTALL | re.IGNORECASE)
    return text.strip()


def _most_recent_assistant_text(messages: list[dict]) -> str | None:
    """Scan backward for the most recent assistant message with real text
    content, or None if there isn't one.

    Used by _loop_safety_message below to fold a prior real answer into the
    tool-calling-loop safety-stop message instead of a bare failure (cross-
    turn search bug report, fix #3): a turn that exhausts MAX_TOOL_ROUNDS
    appends several of ITS OWN assistant messages first — tool-call rounds,
    whose content is empty/None — so scanning backward naturally skips past
    all of those and lands on the last turn's real final reply instead, with
    no need to know which messages belong to "this turn" vs. "an earlier
    one". Deliberately tolerant of non-dict entries: OllamaBrain.chat
    appends the raw provider message object mid-loop (see its ollama_messages
    handling), which may not support .get() — this is a best-effort nicety,
    never worth crashing the safety-stop path it's meant to improve."""
    for message in reversed(messages):
        try:
            if message.get("role") != "assistant":
                continue
            content = message.get("content")
        except AttributeError:
            continue
        if isinstance(content, str) and content.strip():
            return content.strip()
    return None


def _loop_safety_message(messages: list[dict]) -> str:
    """The final reply when MAX_TOOL_ROUNDS is exhausted without a real
    answer. Folds in the most recent prior assistant text reply, if any,
    instead of a bare failure — the reported bug's exact scenario was a
    clarifying follow-up that triggered an unnecessary re-search cascade
    and then hit this exact cap, silently discarding the real answer
    already given a moment earlier. Falls back to the original bare
    message when there's nothing to fold in (e.g. the very first turn of a
    conversation loops), so existing behavior for that case is unchanged."""
    prior_reply = _most_recent_assistant_text(messages)
    if prior_reply:
        return (
            "I'm having trouble finding more specific information, but "
            f"based on what I found a moment ago: {prior_reply}"
        )
    return "I got stuck in a tool-calling loop and stopped for safety. Please rephrase your request."


class OllamaBrain:
    """Talks to a local Ollama server (free, offline, default)."""

    def __init__(self, model: str = None, host: str = None):
        try:
            import ollama
        except ImportError:
            raise RuntimeError(
                "The 'ollama' Python package isn't installed.\n"
                "Fix: pip install -r requirements.txt"
            )

        self.model = model or config.OLLAMA_MODEL
        self.host = host or config.OLLAMA_URL
        self._client = ollama.Client(host=self.host)

        # Fail fast with a clear message if Ollama isn't reachable.
        try:
            self._client.list()
        except Exception as e:
            raise RuntimeError(
                f"Couldn't reach Ollama at {self.host}.\n"
                "Fix: make sure the Ollama app/service is running "
                "(https://ollama.com), then try again.\n"
                f"Details: {e}"
            )

    @staticmethod
    def _to_ollama_tools(tool_specs: list[dict]) -> list[dict]:
        """Convert our neutral specs to Ollama's function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
            for spec in tool_specs
        ]

    def chat(self, messages: list[dict], tools: list[dict] = None, execute_tool=None,
             on_sentence=None, on_token=None, on_tool_failure=None) -> str:
        """
        messages: [{"role": "user"|"assistant"|"system", "content": "..."}]
        tools: optional list of neutral tool specs the model may call
        execute_tool: callback(calls: list[(name, args)]) -> list[str], results
                     in the same order as calls; required if tools given
        on_sentence / on_token: accepted for interface compatibility (streaming
                     is a hosted-brain feature; the local brain answers whole)
        on_tool_failure: optional callback(name, detail) fired for a
                     tool-calling failure that never reaches execute_tool (no
                     answer/tool call produced, stuck loop, ...) — otherwise
                     these failures were invisible outside raw console output.
                     See jarvis.agent.Agent._notify_tool_failure.
        """
        ollama_tools = self._to_ollama_tools(tools) if tools else None
        ollama_messages = []
        has_multimodal_message = False
        for message in messages:
            content = message.get("content")
            if message.get("role") == "user" and isinstance(content, list):
                has_multimodal_message = True
                text_parts, images = [], []
                for part in content:
                    if part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                    elif part.get("type") == "image_url":
                        url = part.get("image_url", {}).get("url", "")
                        if ";base64," in url:
                            images.append(url.split(";base64,", 1)[1])
                ollama_messages.append({
                    **message,
                    "content": "\n".join(text_parts),
                    "images": images,
                })
            else:
                ollama_messages.append(dict(message))
        if not has_multimodal_message:
            # Preserve the original text-only tool-loop behavior exactly.
            ollama_messages = messages

        empty_retries = 0
        for _ in range(MAX_TOOL_ROUNDS):
            response = self._client.chat(
                model=self.model,
                messages=ollama_messages,
                tools=ollama_tools,
                # keep the model loaded in RAM between questions — otherwise
                # Ollama unloads it after ~5 idle minutes and the next reply
                # pays a long reload delay
                keep_alive="1h",
            )
            msg = response["message"]
            tool_calls = getattr(msg, "tool_calls", None) or []

            if not tool_calls or execute_tool is None:
                content = (msg["content"] or "").strip()
                # Small local models occasionally go silent (no text, no tool
                # call). One re-ask usually snaps them out of it.
                if not content and not tool_calls and empty_retries < 1:
                    empty_retries += 1
                    continue
                if not content and on_tool_failure:
                    on_tool_failure("tool_call", "the model produced no answer and no tool call")
                return content or (
                    "I couldn't produce an answer for that — please try rephrasing."
                )

            # The model wants to use tools: record its request, run them
            # (concurrently when the whole batch is confirmation-free — see
            # agent.py's _execute_tool_calls), feed the results back, and let
            # it continue.
            ollama_messages.append(msg)
            calls = [(call["function"]["name"], call["function"]["arguments"] or {})
                     for call in tool_calls]
            for call, result in zip(tool_calls, execute_tool(calls)):
                ollama_messages.append({
                    "role": "tool",
                    "tool_name": call["function"]["name"],
                    "content": str(result),
                })

        if on_tool_failure:
            on_tool_failure("tool_call", "exceeded the maximum tool-calling rounds")
        return _loop_safety_message(ollama_messages)


class HostedBrain:
    """Talks to a free hosted cloud model over any OpenAI-compatible API
    (OpenRouter, Nous portal, NVIDIA NIM, Groq, Hugging Face router, …).

    Which provider/model is used comes from config.HOSTED_BASE_URL and
    config.HOSTED_MODEL; the API key comes from HOSTED_API_KEY in .env.
    Runs on the provider's GPU, so it's much faster than the local brain."""

    def __init__(self, model: str = None, base_url: str = None):
        try:
            from openai import OpenAI
        except ImportError:
            raise RuntimeError(
                "The 'openai' Python package isn't installed.\n"
                "Fix: pip install -r requirements.txt"
            )

        import os
        from dotenv import load_dotenv

        load_dotenv()
        api_key = os.getenv("HOSTED_API_KEY")
        if not api_key:
            raise RuntimeError(
                "No HOSTED_API_KEY found.\n"
                "Fix: copy .env.example to .env (in the Jarvis folder) and paste in a\n"
                "free API key — e.g. from https://console.groq.com — like this:\n"
                "    HOSTED_API_KEY=your-key-here"
            )

        self.last_streamed = False  # True after a reply that was live-printed/spoken
        self.model = model or config.HOSTED_MODEL
        self.base_url = base_url or config.HOSTED_BASE_URL
        if not self.model or not self.base_url:
            raise RuntimeError(
                "HOSTED_BASE_URL / HOSTED_MODEL aren't set in jarvis/config.py.\n"
                "Fix: see the comments above those settings for ready-made values."
            )
        self._client = OpenAI(base_url=self.base_url, api_key=api_key)

    @staticmethod
    def _to_openai_tools(tool_specs: list[dict]) -> list[dict]:
        """Convert our neutral specs to the OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
            for spec in tool_specs
        ]

    def _create_with_retry(self, messages, kwargs):
        """One API call, retrying politely if the free tier is rate-limited.
        Raises RuntimeError with a plain-English message on real failures."""
        import time

        from openai import RateLimitError

        last_error = None
        for attempt in range(3):
            try:
                return self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    **kwargs,
                )
            except RateLimitError as e:
                last_error = e
                if attempt < 2:
                    # Short, bounded backoff. This runs INSIDE the caller's
                    # agent_lock, so every interface (console, web tabs, voice)
                    # is frozen while it sleeps — a long wait here made Jarvis
                    # look hung. Keep it brief: a transient free-tier 429 clears
                    # in a few seconds, and a sustained outage is caught by the
                    # Ollama fallback (round 0) or surfaced as a clear error
                    # rather than a ~45s freeze.
                    wait = 4 * (attempt + 1)  # 4s, then 8s (was 15s, 30s)
                    print(f"(free tier is busy — waiting {wait}s and retrying…)")
                    time.sleep(wait)
            except Exception as e:
                has_images = any(
                    isinstance(message.get("content"), list)
                    and any(
                        isinstance(part, dict) and part.get("type") == "image_url"
                        for part in message["content"]
                    )
                    for message in messages
                )
                if has_images:
                    raise RuntimeError(
                        f"The current model ({self.model}) doesn't support image input. "
                        "Switch HOSTED_MODEL in jarvis/config.py to a vision-capable "
                        "model (see the comment there) and try again."
                    ) from e
                raise RuntimeError(
                    f"The hosted model didn't answer ({e}).\n"
                    "This usually means no internet, or a wrong key/model in "
                    ".env / jarvis/config.py."
                )

        raise RuntimeError(
            f"The free hosted model is rate-limited right now ({last_error}).\n"
            "Wait a minute and try again, or switch HOSTED_MODEL in jarvis/config.py "
            "to another free model (see the comments there)."
        )

    @staticmethod
    def _to_ollama_compatible_messages(messages: list[dict]) -> list[dict]:
        """Convert shared conversation history to the shape Ollama's client
        will accept.

        Our history stores tool calls OpenAI-style: function.arguments is a
        JSON *string*. Ollama's own client validates incoming messages
        against a pydantic schema where function.arguments must already be a
        *dict* — so passing our history straight through crashes with a
        pydantic ValidationError the moment ANY earlier turn in the
        conversation included a tool call, before any network request is
        even made. This returns a new list (the original/self.history is
        left untouched) with arguments parsed back into dicts."""
        import json

        converted = []
        for message in messages:
            tool_calls = message.get("tool_calls") if message.get("role") == "assistant" else None
            if not tool_calls:
                converted.append(message)
                continue
            new_calls = []
            for call in tool_calls:
                function = dict(call.get("function", {}))
                arguments = function.get("arguments")
                if isinstance(arguments, str):
                    try:
                        function["arguments"] = json.loads(arguments or "{}")
                    except json.JSONDecodeError:
                        function["arguments"] = {}
                new_calls.append({**call, "function": function})
            converted.append({**message, "tool_calls": new_calls})
        return converted

    def _fallback_to_ollama(self, messages, tools, execute_tool, reason, on_tool_failure=None):
        """If the hosted endpoint is down/rate-limited and a local Ollama
        server is available, answer with it instead (and say so)."""
        try:
            local = OllamaBrain()
        except Exception:
            return None  # no local brain either — caller reports the real error
        print(f"(hosted brain unavailable — answering with the local Ollama model instead: {reason})")
        if on_tool_failure:
            on_tool_failure("tool_call", f"hosted brain unavailable ({reason}) — used local fallback")
        try:
            safe_messages = self._to_ollama_compatible_messages(messages)
            return local.chat(safe_messages, tools=tools, execute_tool=execute_tool,
                               on_tool_failure=on_tool_failure)
        except Exception as e:
            # The local brain is a best-effort fallback, not a requirement —
            # if IT also can't handle this conversation for some other
            # reason, decline (return None) so the caller reports the
            # original hosted-side error instead of crashing on this one.
            print(f"(local Ollama fallback also failed, giving up on it: {e})")
            if on_tool_failure:
                on_tool_failure("tool_call", f"local fallback also failed: {e}")
            return None

    def chat(self, messages: list[dict], tools: list[dict] = None, execute_tool=None,
             on_sentence=None, on_token=None, on_tool_failure=None) -> str:
        """on_sentence: optional callback(sentence) — with streaming enabled it
        receives each finished sentence of the final answer as it's generated,
        so the voice can start speaking before the reply is complete.
        on_token: optional callback(text) — receives each visible chunk of the
        final answer as it streams (used by the web UI to render live).
        on_tool_failure: optional callback(name, detail) — see OllamaBrain.chat's
        docstring; threaded through streaming/blocking/fallback below."""
        self.last_streamed = False

        if getattr(config, "STREAMING", True):
            try:
                return self._chat_streaming(messages, tools, execute_tool, on_sentence,
                                            on_token, on_tool_failure)
            except RuntimeError:
                raise  # already a friendly message (incl. the Ollama fallback path)
            except Exception as e:
                # Anything odd about streaming: quietly do it the old way.
                print(f"(streaming hiccup, answering the normal way: {e})")
                self.last_streamed = False
        try:
            return self._chat_blocking(messages, tools, execute_tool, on_tool_failure)
        except RuntimeError:
            raise  # already a friendly message (incl. the Ollama fallback path)
        except Exception as e:
            # A single bad turn (a malformed tool call, an unexpected
            # provider response shape, ...) must never crash the whole
            # conversation/connection — see jarvis/web/server.py's
            # run_turn, which relies on chat() always returning a string
            # rather than raising for this class of failure.
            print(f"(tool-call handling hit an unexpected error, not crashing the turn: {e})")
            if on_tool_failure:
                on_tool_failure("tool_call", f"unexpected error handling tool calls: {e}")
            return "I had trouble completing part of that — could you confirm what you'd like me to do?"

    # ------------------------------------------------------------- streaming

    def _chat_streaming(self, messages, tools, execute_tool, on_sentence,
                        on_token=None, on_tool_failure=None) -> str:
        import json
        import uuid

        # emit() mirrors exactly what gets live-printed to the console, so the
        # web UI sees the same clean text (no <think> traces, no tool-round text)
        emit = on_token or (lambda _t: None)

        kwargs = {"stream": True}
        if tools:
            kwargs["tools"] = self._to_openai_tools(tools)

        for round_no in range(MAX_TOOL_ROUNDS):
            try:
                stream = self._create_with_retry(messages, kwargs)
            except RuntimeError as e:
                if "doesn't support image input" in str(e):
                    raise
                if round_no == 0 and getattr(config, "HOSTED_FALLBACK_TO_OLLAMA", True):
                    fallback = self._fallback_to_ollama(
                        messages, tools, execute_tool, str(e).splitlines()[0],
                        on_tool_failure=on_tool_failure,
                    )
                    if fallback is not None:
                        return fallback
                if on_tool_failure:
                    on_tool_failure("tool_call", str(e).splitlines()[0])
                raise

            content_parts = []
            tool_acc = {}          # index -> accumulated tool call
            think = ThinkFilter()
            sentences = SentenceBuffer(on_sentence)
            held = ""              # text held back until we know this isn't a tool round
            emitting = False       # True once we start live printing/speaking

            for chunk in stream:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta is None:
                    continue

                # --- accumulate tool-call fragments (they stream in pieces) ---
                if delta.tool_calls:
                    for tc in delta.tool_calls:
                        acc = tool_acc.setdefault(
                            tc.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if tc.id:
                            acc["id"] = tc.id
                        if tc.function and tc.function.name:
                            acc["name"] = tc.function.name
                        if tc.function and tc.function.arguments:
                            acc["arguments"] += tc.function.arguments

                # --- text ---
                if delta.content:
                    content_parts.append(delta.content)
                    visible = think.push(delta.content)
                    if not visible:
                        continue
                    if tool_acc:
                        continue  # tool round — never speak/print its text
                    if not emitting:
                        held += visible
                        # a few real characters with no tool call yet ⇒ this is
                        # the actual answer: start live output
                        if len(held.strip()) >= 8:
                            print("Jarvis: ", end="", flush=True)
                            print(held, end="", flush=True)
                            sentences.push(held)
                            emit(held)
                            held = ""
                            emitting = True
                    else:
                        print(visible, end="", flush=True)
                        sentences.push(visible)
                        emit(visible)

            tail = think.flush()
            full_text = strip_think("".join(content_parts))

            # ---- tool round: run the tools (safety gate included) and loop ----
            if tool_acc and execute_tool is not None:
                calls = [tool_acc[i] for i in sorted(tool_acc)]
                # A missing id would send a tool_calls/tool_result pair the
                # provider can't match up, and some providers reject that
                # outright — fall back to a locally-generated one rather
                # than sending an empty string.
                for c in calls:
                    if not c["id"]:
                        c["id"] = f"call_{uuid.uuid4().hex[:24]}"
                messages.append({
                    "role": "assistant",
                    "content": full_text or None,
                    "tool_calls": [
                        {
                            "id": c["id"],
                            "type": "function",
                            "function": {"name": c["name"], "arguments": c["arguments"] or "{}"},
                        }
                        for c in calls
                    ],
                })
                parsed_calls = []
                for c in calls:
                    try:
                        args = json.loads(c["arguments"] or "{}")
                    except json.JSONDecodeError:
                        print(f"(tool call '{c['name']}' sent unparseable arguments, "
                              f"treating as empty — raw payload: {c['arguments']!r})")
                        args = {}
                    parsed_calls.append((c["name"], args))
                for c, result in zip(calls, execute_tool(parsed_calls)):
                    messages.append({
                        "role": "tool",
                        "tool_call_id": c["id"],
                        "content": str(result),
                    })
                continue

            # ---- final text round: finish emitting and return ----
            leftover = held + tail
            if emitting:
                if leftover:
                    print(leftover, end="", flush=True)
                    sentences.push(leftover)
                    emit(leftover)
                print("\n", flush=True)
                sentences.flush()
                self.last_streamed = True
            elif leftover.strip():
                # answer was shorter than the hold-back threshold
                print(f"Jarvis: {leftover}\n", flush=True)
                sentences.push(leftover)
                emit(leftover)
                sentences.flush()
                self.last_streamed = True
            return full_text

        if on_tool_failure:
            on_tool_failure("tool_call", "exceeded the maximum tool-calling rounds")
        return _loop_safety_message(messages)

    # ------------------------------------------------------------- blocking

    def _chat_blocking(self, messages, tools, execute_tool, on_tool_failure=None) -> str:
        import json

        kwargs = {}
        if tools:
            kwargs["tools"] = self._to_openai_tools(tools)

        for round_no in range(MAX_TOOL_ROUNDS):
            try:
                response = self._create_with_retry(messages, kwargs)
            except RuntimeError as e:
                if "doesn't support image input" in str(e):
                    raise
                # Only fall back before any tools have run this turn — halfway
                # through a tool conversation the formats aren't compatible.
                if round_no == 0 and getattr(config, "HOSTED_FALLBACK_TO_OLLAMA", True):
                    fallback = self._fallback_to_ollama(
                        messages, tools, execute_tool, str(e).splitlines()[0],
                        on_tool_failure=on_tool_failure,
                    )
                    if fallback is not None:
                        return fallback
                if on_tool_failure:
                    on_tool_failure("tool_call", str(e).splitlines()[0])
                raise

            msg = response.choices[0].message
            tool_calls = msg.tool_calls or []

            if not tool_calls or execute_tool is None:
                return strip_think(msg.content or "")

            # The model wants to use tools: record its request, run them
            # (concurrently when safe — see agent.py's _execute_tool_calls),
            # feed the results back, and let it continue.
            messages.append(msg.model_dump(exclude_none=True))
            parsed_calls = []
            for call in tool_calls:
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    print(f"(tool call '{call.function.name}' sent unparseable arguments, "
                          f"treating as empty — raw payload: {call.function.arguments!r})")
                    args = {}
                parsed_calls.append((call.function.name, args))
            for call, result in zip(tool_calls, execute_tool(parsed_calls)):
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": str(result),
                })

        if on_tool_failure:
            on_tool_failure("tool_call", "exceeded the maximum tool-calling rounds")
        return _loop_safety_message(messages)


class ClaudeBrain:
    """Talks to the Anthropic Claude API (optional, paid, needs CLAUDE_API_KEY in .env)."""

    def __init__(self, model: str = None):
        try:
            import anthropic
        except ImportError:
            raise RuntimeError(
                "The 'anthropic' Python package isn't installed.\n"
                "Fix: pip install -r requirements.txt"
            )

        import os
        from dotenv import load_dotenv

        load_dotenv()
        api_key = os.getenv("CLAUDE_API_KEY")
        if not api_key:
            raise RuntimeError(
                "No CLAUDE_API_KEY found.\n"
                "Fix: copy .env.example to .env and paste in your Anthropic API key "
                "from https://console.anthropic.com/settings/keys"
            )

        self.model = model or config.CLAUDE_MODEL
        self._client = anthropic.Anthropic(api_key=api_key)

    @staticmethod
    def _to_claude_tools(tool_specs: list[dict]) -> list[dict]:
        """Convert our neutral specs to the Anthropic tool-use format."""
        return [
            {
                "name": spec["name"],
                "description": spec["description"],
                "input_schema": spec["parameters"],
            }
            for spec in tool_specs
        ]

    def chat(self, messages: list[dict], tools: list[dict] = None, execute_tool=None,
             on_sentence=None, on_token=None, on_tool_failure=None) -> str:
        # (on_sentence/on_token accepted for interface compatibility; not used here.)
        # Claude takes the system prompt as a separate parameter, not a message.
        system = None
        convo = []
        for m in messages:
            if m["role"] == "system":
                system = m["content"]
            else:
                content = m.get("content")
                if m.get("role") == "user" and isinstance(content, list):
                    blocks = []
                    for part in content:
                        if part.get("type") == "text":
                            blocks.append({"type": "text", "text": part.get("text", "")})
                        elif part.get("type") == "image_url":
                            url = part.get("image_url", {}).get("url", "")
                            if ";base64," not in url:
                                continue
                            header, payload = url.split(";base64,", 1)
                            blocks.append({
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": header.removeprefix("data:"),
                                    "data": payload,
                                },
                            })
                    convo.append({**m, "content": blocks})
                else:
                    convo.append(m)

        claude_tools = self._to_claude_tools(tools) if tools else None

        for _ in range(MAX_TOOL_ROUNDS):
            response = self._client.messages.create(
                model=self.model,
                max_tokens=2048,
                system=system,
                messages=convo,
                tools=claude_tools or [],
            )

            if response.stop_reason != "tool_use" or execute_tool is None:
                text_parts = [b.text for b in response.content if b.type == "text"]
                return "\n".join(text_parts)

            # Run every tool Claude asked for (concurrently when safe — see
            # agent.py's _execute_tool_calls) and send back the results.
            convo.append({"role": "assistant", "content": response.content})
            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            calls = [(b.name, b.input or {}) for b in tool_use_blocks]
            results = [
                {"type": "tool_result", "tool_use_id": block.id, "content": str(result)}
                for block, result in zip(tool_use_blocks, execute_tool(calls))
            ]
            convo.append({"role": "user", "content": results})

        if on_tool_failure:
            on_tool_failure("tool_call", "exceeded the maximum tool-calling rounds")
        # Scans the ORIGINAL messages param, not convo: convo's assistant
        # entries for tool-call rounds hold Anthropic's raw content-block
        # list (response.content), not a plain string, for THIS turn's own
        # rounds — but messages (== Agent.history, never mutated by this
        # method) still has any earlier turn's real reply as a plain string,
        # which is exactly what we want to fold in here.
        return _loop_safety_message(messages)


def get_brain():
    """Returns the active brain instance based on config.BRAIN."""
    if config.BRAIN == "ollama":
        return OllamaBrain()
    elif config.BRAIN == "hosted":
        return HostedBrain()
    elif config.BRAIN == "claude":
        return ClaudeBrain()
    else:
        raise ValueError(
            f"Unknown BRAIN '{config.BRAIN}' in jarvis/config.py — "
            "must be 'hosted', 'ollama', or 'claude'."
        )
