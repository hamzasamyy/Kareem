import unittest

from jarvis.brain import HostedBrain


class OllamaCompatibleMessagesTests(unittest.TestCase):
    def test_string_arguments_converted_to_dict(self):
        # This is the exact shape self.history ends up holding after a
        # hosted-brain tool round (OpenAI format: arguments is a JSON
        # string) — the reported crash happened because this was handed
        # straight to Ollama's client, which requires a dict.
        messages = [
            {"role": "user", "content": "add a reminder"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "calendar_add_event",
                            "arguments": '{"title": "submit report", "start": "2026-07-21T00:00:00"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "Created event."},
        ]
        converted = HostedBrain._to_ollama_compatible_messages(messages)
        args = converted[1]["tool_calls"][0]["function"]["arguments"]
        self.assertIsInstance(args, dict)
        self.assertEqual(args["title"], "submit report")

    def test_actually_passes_ollama_client_validation(self):
        # Reproduces the exact crash from the bug report: without the fix,
        # this raises pydantic.ValidationError("1 validation error for
        # Message tool_calls.0.function.arguments ... Input should be a
        # valid dictionary") the instant the client tries to send it —
        # no live Ollama server is even needed to trigger it.
        import ollama

        messages = [
            {"role": "user", "content": "add a reminder"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "calendar_add_event",
                            "arguments": '{"title": "submit report", "start": "2026-07-21T00:00:00"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "Created event."},
        ]
        converted = HostedBrain._to_ollama_compatible_messages(messages)
        client = ollama.Client(host="http://127.0.0.1:11434")
        try:
            client.chat(model="doesnt-matter", messages=converted, tools=[])
        except ollama.ResponseError:
            pass  # got past client-side validation; failed for an unrelated reason (no such model/server)
        except Exception as e:  # pragma: no cover - would fail the test with a clear message
            self.fail(f"Expected client-side validation to pass, got: {type(e).__name__}: {e}")

    def test_messages_without_tool_calls_are_unchanged(self):
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        converted = HostedBrain._to_ollama_compatible_messages(messages)
        self.assertEqual(converted, messages)

    def test_malformed_arguments_json_falls_back_to_empty_dict(self):
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "foo", "arguments": "{not valid json"}}
                ],
            },
        ]
        converted = HostedBrain._to_ollama_compatible_messages(messages)
        self.assertEqual(converted[0]["tool_calls"][0]["function"]["arguments"], {})

    def test_original_messages_list_not_mutated(self):
        original_args = '{"a": 1}'
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function",
                     "function": {"name": "foo", "arguments": original_args}}
                ],
            },
        ]
        HostedBrain._to_ollama_compatible_messages(messages)
        self.assertEqual(messages[0]["tool_calls"][0]["function"]["arguments"], original_args)


if __name__ == "__main__":
    unittest.main()
