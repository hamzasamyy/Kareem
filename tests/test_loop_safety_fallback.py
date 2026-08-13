import unittest
from unittest.mock import MagicMock

from kareem.brain import (
    MAX_TOOL_ROUNDS,
    ClaudeBrain,
    HostedBrain,
    OllamaBrain,
    _loop_safety_message,
    _most_recent_assistant_text,
)


class MostRecentAssistantTextTests(unittest.TestCase):
    """Pure-function tests for the helper that finds what to fold into the
    "stuck in a tool-calling loop" safety-stop message (cross-turn search
    bug report, fix #3)."""

    def test_finds_the_only_assistant_reply(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello there."},
        ]
        self.assertEqual(_most_recent_assistant_text(messages), "Hello there.")

    def test_finds_the_most_recent_of_several(self):
        messages = [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "first answer"},
            {"role": "user", "content": "q2"},
            {"role": "assistant", "content": "second answer"},
        ]
        self.assertEqual(_most_recent_assistant_text(messages), "second answer")

    def test_skips_tool_call_rounds_with_empty_or_none_content(self):
        # Exactly what a stuck-loop turn's OWN messages look like: several
        # assistant entries with no text (just tool_calls), which must be
        # skipped in favor of the real prior-turn reply further back.
        messages = [
            {"role": "user", "content": "what's the weather in la vista"},
            {"role": "assistant", "content": "Tomorrow in La Vista will be mild."},
            {"role": "user", "content": "you know i mean lavista bay in egypt right?"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
            {"role": "tool", "content": "some search result"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "2"}]},
            {"role": "tool", "content": "another search result"},
        ]
        self.assertEqual(
            _most_recent_assistant_text(messages), "Tomorrow in La Vista will be mild."
        )

    def test_skips_list_content_assistant_messages(self):
        # Anthropic's raw content-block list (ClaudeBrain's convo shape for
        # a tool-call round) — must not be mistaken for real text.
        messages = [
            {"role": "assistant", "content": "real earlier reply"},
            {"role": "assistant", "content": [{"type": "text", "text": "not a string"}]},
        ]
        self.assertEqual(_most_recent_assistant_text(messages), "real earlier reply")

    def test_returns_none_when_no_assistant_text_exists(self):
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "1"}]},
        ]
        self.assertIsNone(_most_recent_assistant_text(messages))

    def test_returns_none_for_empty_messages(self):
        self.assertIsNone(_most_recent_assistant_text([]))

    def test_tolerant_of_non_dict_entries(self):
        # OllamaBrain.chat appends the raw provider message object mid-loop
        # (ollama_messages.append(msg)), which may not support .get().
        class _NotADict:
            pass

        messages = [
            {"role": "assistant", "content": "real earlier reply"},
            _NotADict(),
        ]
        self.assertEqual(_most_recent_assistant_text(messages), "real earlier reply")

    def test_whitespace_only_content_treated_as_no_reply(self):
        messages = [{"role": "assistant", "content": "   "}]
        self.assertIsNone(_most_recent_assistant_text(messages))


class LoopSafetyMessageTests(unittest.TestCase):
    def test_no_prior_reply_returns_bare_generic_message(self):
        messages = [{"role": "user", "content": "loop forever"}]
        self.assertEqual(
            _loop_safety_message(messages),
            "I got stuck in a tool-calling loop and stopped for safety. Please rephrase your request.",
        )

    def test_prior_reply_gets_folded_in(self):
        messages = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "Tomorrow in La Vista will be mild."},
        ]
        result = _loop_safety_message(messages)
        self.assertIn("I'm having trouble finding more specific information", result)
        self.assertIn("based on what I found a moment ago", result)
        self.assertIn("Tomorrow in La Vista will be mild.", result)


# --------------------------------------------------------------- OllamaBrain

class _FakeOllamaMessage:
    def __init__(self, content="", tool_calls=None):
        self._content = content
        self.tool_calls = tool_calls or []

    def __getitem__(self, key):
        if key == "content":
            return self._content
        raise KeyError(key)


def _make_ollama_brain_always_tool_call():
    brain = OllamaBrain.__new__(OllamaBrain)
    brain.model = "fake-model"
    brain.host = "http://fake"
    fake_client = MagicMock()
    fake_client.chat.side_effect = lambda **kwargs: {
        "message": _FakeOllamaMessage(
            tool_calls=[{"function": {"name": "web_search", "arguments": {}}}]
        )
    }
    brain._client = fake_client
    return brain


# --------------------------------------------------------------- HostedBrain

class _FakeToolCallFunction:
    def __init__(self, name=None, arguments=None):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id_, name, arguments):
        self.id = id_
        self.function = _FakeToolCallFunction(name, arguments)


class _FakeChoiceMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls or []

    def model_dump(self, exclude_none=True):
        data = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            data["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in self.tool_calls
            ]
        if exclude_none:
            data = {k: v for k, v in data.items() if v is not None}
        return data


class _FakeBlockingResponse:
    def __init__(self, message):
        self.choices = [MagicMock(message=message)]


def _make_hosted_brain_always_tool_call_blocking():
    brain = HostedBrain.__new__(HostedBrain)
    brain.model = "fake-model"
    brain.base_url = "https://fake"

    def fake_create_with_retry(messages, kwargs):
        return _FakeBlockingResponse(_FakeChoiceMessage(
            tool_calls=[_FakeToolCall("call_x", "web_search", "{}")]
        ))

    brain._create_with_retry = fake_create_with_retry
    return brain


class _FakeDeltaToolCall:
    def __init__(self, index, id_=None, name=None, arguments=None):
        self.index = index
        self.id = id_
        self.function = _FakeToolCallFunction(name, arguments) if (name or arguments) else None


class _FakeDelta:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeChunk:
    def __init__(self, delta):
        self.choices = [MagicMock(delta=delta)]


def _make_hosted_brain_always_tool_call_streaming():
    brain = HostedBrain.__new__(HostedBrain)
    brain.model = "fake-model"
    brain.base_url = "https://fake"

    def fake_create_with_retry(messages, kwargs):
        return [_FakeChunk(_FakeDelta(tool_calls=[
            _FakeDeltaToolCall(0, id_="call_x", name="web_search", arguments="{}")
        ]))]

    brain._create_with_retry = fake_create_with_retry
    return brain


# --------------------------------------------------------------- ClaudeBrain

class _FakeClaudeBlock:
    def __init__(self, type_, **kwargs):
        self.type = type_
        for key, value in kwargs.items():
            setattr(self, key, value)


class _FakeClaudeResponse:
    def __init__(self, stop_reason, content):
        self.stop_reason = stop_reason
        self.content = content


def _make_claude_brain_always_tool_call():
    brain = ClaudeBrain.__new__(ClaudeBrain)
    brain.model = "fake-model"
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _FakeClaudeResponse(
        "tool_use", [_FakeClaudeBlock("tool_use", id="call_x", name="web_search", input={})]
    )
    brain._client = fake_client
    return brain


PRIOR_ANSWER = "Tomorrow in La Vista will be mild, around 23C with some rain."


def _repro_messages():
    """The reported bug's message shape: a real prior answer, then a
    clarifying follow-up that (in this test) triggers an endless tool loop —
    used to confirm all 4 brains fold the prior answer in rather than
    returning a bare failure."""
    return [
        {"role": "user", "content": "what's the weather in la vista"},
        {"role": "assistant", "content": PRIOR_ANSWER},
        {"role": "user", "content": "you know i mean lavista bay in egypt right?"},
    ]


class StuckLoopFoldsInPriorReplyTests(unittest.TestCase):
    """Deliberately trigger the loop-safety-stop (all 4 brain.py call
    sites) and confirm each one surfaces the prior real answer instead of a
    bare failure, per the bug report's fix #3. This is a fully
    deterministic simulation (a fake client that always returns a tool
    call), not a live model call — see the task report for why that's the
    right verification standard for this specific piece (pure, testable
    Python; no live-model judgment involved), unlike fixes #1/#2."""

    def test_ollama_stuck_loop_folds_in_prior_reply(self):
        brain = _make_ollama_brain_always_tool_call()
        reply = brain.chat(
            _repro_messages(),
            tools=[{"name": "web_search", "description": "", "parameters": {}}],
            execute_tool=lambda calls: ["ok"] * len(calls),
        )
        self.assertIn("based on what I found a moment ago", reply)
        self.assertIn(PRIOR_ANSWER, reply)

    def test_ollama_stuck_loop_with_no_prior_reply_stays_bare(self):
        brain = _make_ollama_brain_always_tool_call()
        reply = brain.chat(
            [{"role": "user", "content": "loop forever"}],
            tools=[{"name": "web_search", "description": "", "parameters": {}}],
            execute_tool=lambda calls: ["ok"] * len(calls),
        )
        self.assertEqual(
            reply,
            "I got stuck in a tool-calling loop and stopped for safety. Please rephrase your request.",
        )

    def test_hosted_blocking_stuck_loop_folds_in_prior_reply(self):
        brain = _make_hosted_brain_always_tool_call_blocking()
        reply = brain._chat_blocking(
            _repro_messages(),
            tools=[{"name": "web_search", "description": "", "parameters": {}}],
            execute_tool=lambda calls: ["ok"] * len(calls),
        )
        self.assertIn("based on what I found a moment ago", reply)
        self.assertIn(PRIOR_ANSWER, reply)

    def test_hosted_streaming_stuck_loop_folds_in_prior_reply(self):
        brain = _make_hosted_brain_always_tool_call_streaming()
        reply = brain._chat_streaming(
            _repro_messages(),
            [{"name": "web_search", "description": "", "parameters": {}}],
            lambda calls: ["ok"] * len(calls),
            on_sentence=None,
        )
        self.assertIn("based on what I found a moment ago", reply)
        self.assertIn(PRIOR_ANSWER, reply)

    def test_claude_stuck_loop_folds_in_prior_reply(self):
        brain = _make_claude_brain_always_tool_call()
        reply = brain.chat(
            _repro_messages(),
            tools=[{"name": "web_search", "description": "", "parameters": {}}],
            execute_tool=lambda calls: ["ok"] * len(calls),
        )
        self.assertIn("based on what I found a moment ago", reply)
        self.assertIn(PRIOR_ANSWER, reply)

    def test_all_four_sites_still_report_tool_failure(self):
        # Fix #3 (this file) must not have quietly broken the earlier
        # calendar-bug fix (on_tool_failure/Activity-feed visibility,
        # tested in test_tool_call_failure_visibility.py) — spot-check here
        # that the notification still fires alongside the folded message.
        brain = _make_ollama_brain_always_tool_call()
        failures = []
        brain.chat(
            _repro_messages(),
            tools=[{"name": "web_search", "description": "", "parameters": {}}],
            execute_tool=lambda calls: ["ok"] * len(calls),
            on_tool_failure=lambda name, detail: failures.append((name, detail)),
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("exceeded the maximum tool-calling rounds", failures[0][1])


if __name__ == "__main__":
    unittest.main()
