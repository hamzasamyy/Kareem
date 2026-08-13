import unittest
from unittest import mock
from unittest.mock import MagicMock, patch

from kareem import config
from kareem.agent import Agent
from kareem.brain import HostedBrain, MAX_TOOL_ROUNDS, OllamaBrain


class _FakeOllamaMessage:
    """Minimal stand-in for ollama's response message: supports both
    dict-style `msg["content"]` and attribute-style `msg.tool_calls`,
    matching exactly how OllamaBrain.chat reads a real one."""

    def __init__(self, content="", tool_calls=None):
        self._content = content
        self.tool_calls = tool_calls or []

    def __getitem__(self, key):
        if key == "content":
            return self._content
        raise KeyError(key)


def _make_ollama_brain(responses):
    """An OllamaBrain with __init__ (and its real network probe) bypassed,
    and a fake _client.chat() returning `responses` in order — no live
    Ollama server needed."""
    brain = OllamaBrain.__new__(OllamaBrain)
    brain.model = "fake-model"
    brain.host = "http://fake"
    fake_client = MagicMock()
    fake_client.chat.side_effect = [{"message": m} for m in responses]
    brain._client = fake_client
    return brain


class OllamaBrainToolFailureNotificationTests(unittest.TestCase):
    """Bug report #3: a turn that produces no tool call AND no useful text
    (empty response, or a stuck tool-calling loop) used to vanish — no
    Activity feed entry, nothing in the session log, only a generic reply.
    on_tool_failure closes that gap."""

    def test_empty_response_after_retry_notifies_on_tool_failure(self):
        # Two rounds: first empty (triggers the model's built-in one retry),
        # second also empty -> falls through to the generic fallback string
        # AND must notify, since this is exactly the observed console bug
        # ("put an appointemnet tomorrow at 5 pm..." -> "I couldn't produce
        # an answer for that" with nothing logged anywhere).
        brain = _make_ollama_brain([_FakeOllamaMessage(content=""), _FakeOllamaMessage(content="")])
        failures = []
        reply = brain.chat(
            [{"role": "user", "content": "put an appointment tomorrow at 5pm"}],
            tools=None, execute_tool=None,
            on_tool_failure=lambda name, detail: failures.append((name, detail)),
        )
        self.assertEqual(reply, "I couldn't produce an answer for that — please try rephrasing.")
        self.assertEqual(len(failures), 1)
        name, detail = failures[0]
        self.assertEqual(name, "tool_call")
        self.assertIn("no answer", detail)

    def test_successful_reply_does_not_notify_on_tool_failure(self):
        brain = _make_ollama_brain([_FakeOllamaMessage(content="Sure, done.")])
        failures = []
        reply = brain.chat(
            [{"role": "user", "content": "hello"}],
            tools=None, execute_tool=None,
            on_tool_failure=lambda name, detail: failures.append((name, detail)),
        )
        self.assertEqual(reply, "Sure, done.")
        self.assertEqual(failures, [])

    def test_stuck_tool_loop_notifies_on_tool_failure(self):
        responses = [
            _FakeOllamaMessage(tool_calls=[{"function": {"name": "web_search", "arguments": {}}}])
            for _ in range(MAX_TOOL_ROUNDS)
        ]
        brain = _make_ollama_brain(responses)
        failures = []
        reply = brain.chat(
            [{"role": "user", "content": "loop forever"}],
            tools=[{"name": "web_search", "description": "", "parameters": {}}],
            execute_tool=lambda calls: ["ok"] * len(calls),
            on_tool_failure=lambda name, detail: failures.append((name, detail)),
        )
        self.assertEqual(
            reply,
            "I got stuck in a tool-calling loop and stopped for safety. Please rephrase your request.",
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("maximum tool-calling rounds", failures[0][1])

    def test_successful_tool_round_does_not_notify(self):
        responses = [
            _FakeOllamaMessage(tool_calls=[{"function": {"name": "web_search", "arguments": {}}}]),
            _FakeOllamaMessage(content="Here's what I found."),
        ]
        brain = _make_ollama_brain(responses)
        failures = []
        reply = brain.chat(
            [{"role": "user", "content": "search something"}],
            tools=[{"name": "web_search", "description": "", "parameters": {}}],
            execute_tool=lambda calls: ["ok"] * len(calls),
            on_tool_failure=lambda name, detail: failures.append((name, detail)),
        )
        self.assertEqual(reply, "Here's what I found.")
        self.assertEqual(failures, [])


class FallbackToOllamaNotificationTests(unittest.TestCase):
    """HostedBrain._fallback_to_ollama is where bug #1's causal chain
    (hosted rejects a tool call the router excluded -> falls back to a local
    model offered the SAME excluded tools) actually lands. These pin that
    both the fallback itself, and a fallback that also fails, are visible."""

    def _make_hosted_brain(self):
        return HostedBrain.__new__(HostedBrain)

    @patch("kareem.brain.OllamaBrain")
    def test_fallback_invoked_notifies_before_attempting(self, mock_ollama_cls):
        mock_instance = MagicMock()
        mock_instance.chat.return_value = "answered locally"
        mock_ollama_cls.return_value = mock_instance

        brain = self._make_hosted_brain()
        failures = []
        result = brain._fallback_to_ollama(
            [{"role": "user", "content": "hi"}], tools=None, execute_tool=None,
            reason="hosted rate-limited",
            on_tool_failure=lambda name, detail: failures.append((name, detail)),
        )
        self.assertEqual(result, "answered locally")
        self.assertEqual(len(failures), 1)
        self.assertIn("hosted brain unavailable", failures[0][1])
        self.assertIn("hosted rate-limited", failures[0][1])

    @patch("kareem.brain.OllamaBrain")
    def test_no_local_brain_available_does_not_notify(self, mock_ollama_cls):
        # No local Ollama at all: the ORIGINAL hosted-side error is what the
        # caller reports (via its own on_tool_failure call after re-raising),
        # so this function declining silently (returning None) is correct.
        mock_ollama_cls.side_effect = RuntimeError("no local ollama")
        brain = self._make_hosted_brain()
        failures = []
        result = brain._fallback_to_ollama(
            [{"role": "user", "content": "hi"}], tools=None, execute_tool=None,
            reason="hosted down",
            on_tool_failure=lambda name, detail: failures.append((name, detail)),
        )
        self.assertIsNone(result)
        self.assertEqual(failures, [])

    @patch("kareem.brain.OllamaBrain")
    def test_fallback_chat_failure_notifies_twice(self, mock_ollama_cls):
        mock_instance = MagicMock()
        mock_instance.chat.side_effect = RuntimeError("local also broken")
        mock_ollama_cls.return_value = mock_instance

        brain = self._make_hosted_brain()
        failures = []
        result = brain._fallback_to_ollama(
            [{"role": "user", "content": "hi"}], tools=None, execute_tool=None,
            reason="hosted down",
            on_tool_failure=lambda name, detail: failures.append((name, detail)),
        )
        self.assertIsNone(result)
        self.assertEqual(len(failures), 2)
        self.assertIn("hosted brain unavailable", failures[0][1])
        self.assertIn("local fallback also failed", failures[1][1])


class HostedBrainEndToEndReproTests(unittest.TestCase):
    """Reproduces the exact console failure from the bug report end-to-end,
    deterministically (mocked network): a Groq-style "not in request.tools"
    validation error, falling back to a local model that ALSO can't produce
    anything — with, before this fix, zero trace anywhere but stdout."""

    @patch("kareem.brain.OllamaBrain")
    def test_hosted_validation_error_with_empty_fallback_notifies(self, mock_ollama_cls):
        mock_local = MagicMock()
        mock_local.chat.return_value = (
            "I couldn't produce an answer for that — please try rephrasing."
        )
        mock_ollama_cls.return_value = mock_local

        brain = HostedBrain.__new__(HostedBrain)
        brain.model = "openai/gpt-oss-120b"
        brain.base_url = "https://api.groq.com/openai/v1"

        def fake_create_with_retry(messages, kwargs):
            raise RuntimeError(
                "The hosted model didn't answer (Tool call validation failed: "
                "attempted to call tool 'calendar_add_event' which was not in "
                "request.tools).\nThis usually means no internet, or a wrong "
                "key/model in .env / kareem/config.py."
            )

        brain._create_with_retry = fake_create_with_retry

        with mock.patch.object(config, "HOSTED_FALLBACK_TO_OLLAMA", True):
            failures = []
            reply = brain.chat(
                [{"role": "user", "content":
                  "put an appointment tomorrow at 5pm to travel to sahel"}],
                tools=[{"name": "web_search", "description": "", "parameters": {}}],
                execute_tool=lambda calls: ["ok"] * len(calls),
                on_tool_failure=lambda name, detail: failures.append((name, detail)),
            )

        self.assertEqual(
            reply, "I couldn't produce an answer for that — please try rephrasing."
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("hosted brain unavailable", failures[0][1])
        self.assertIn("calendar_add_event", failures[0][1])


class AgentNotifyToolFailureTests(unittest.TestCase):
    """kareem.agent.Agent._notify_tool_failure and its wiring into send()."""

    def test_notify_tool_failure_pushes_to_activity_feed_and_session_log(self):
        agent = Agent.__new__(Agent)
        received = []
        agent._on_tool = lambda event: received.append(event)

        with patch("kareem.session_log.log_event") as mock_log:
            agent._notify_tool_failure("tool_call", "tool call failed: something broke")

        self.assertEqual(len(received), 1)
        self.assertEqual(received[0], {
            "phase": "end", "name": "tool_call",
            "detail": "tool call failed: something broke", "ok": False,
        })
        mock_log.assert_called_once_with(
            "tool_result", name="tool_call",
            result="tool call failed: something broke", ok=False,
        )

    def test_notify_tool_failure_is_safe_with_no_ui_callback_registered(self):
        # Console mode (no on_tool wired up at all) must not raise.
        agent = Agent.__new__(Agent)
        agent._on_tool = None
        with patch("kareem.session_log.log_event") as mock_log:
            agent._notify_tool_failure("tool_call", "detail")
        mock_log.assert_called_once()

    def test_send_notifies_and_reraises_when_brain_raises(self):
        # The worst case: hosted fails AND no local fallback exists, so the
        # exception propagates all the way out of brain.chat(). Before this
        # fix, kareem/web/server.py's run_turn still caught this generically
        # (an "engine error" toast), but NOTHING reached the Activity feed
        # or session log to say a tool call was even involved.
        agent = Agent.__new__(Agent)
        agent.history = [{"role": "system", "content": "sys"}]

        class _FailingBrain:
            def chat(self, *args, **kwargs):
                raise RuntimeError("hosted down and no local Ollama available")

        agent.brain = _FailingBrain()
        notified = []
        agent._notify_tool_failure = lambda name, detail: notified.append((name, detail))

        with self.assertRaises(RuntimeError):
            agent.send("put an appointment tomorrow at 5pm to travel to sahel")

        self.assertEqual(len(notified), 1)
        self.assertEqual(notified[0][0], "tool_call")
        self.assertIn("hosted down and no local Ollama available", notified[0][1])

    def test_send_does_not_notify_failure_on_a_normal_reply(self):
        agent = Agent.__new__(Agent)
        agent.history = [{"role": "system", "content": "sys"}]

        class _OkBrain:
            def chat(self, *args, **kwargs):
                return "Added the appointment to your calendar."

        agent.brain = _OkBrain()
        notified = []
        agent._notify_tool_failure = lambda name, detail: notified.append((name, detail))

        reply = agent.send("put an appointment tomorrow at 5pm")

        self.assertEqual(reply, "Added the appointment to your calendar.")
        self.assertEqual(notified, [])


if __name__ == "__main__":
    unittest.main()
