import copy
import json
import unittest

from kareem.agent import Agent, KEEP_RECENT_TURNS, TRIM_TRIGGER_TURNS


class SummaryBrain:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def chat(self, messages, tools=None, execute_tool=None, **kwargs):
        self.calls.append((messages, tools, execute_tool))
        if self.error:
            raise self.error
        return "The user and Kareem established useful earlier context."


def make_turn(number, with_tool=False):
    messages = [{"role": "user", "content": f"user {number}"}]
    if with_tool:
        call_id = f"call-{number}"
        messages.extend([
            {
                "role": "assistant",
                "tool_calls": [{"id": call_id, "function": {"name": "demo"}}],
            },
            {"role": "tool", "tool_call_id": call_id, "content": "done"},
        ])
    messages.append({"role": "assistant", "content": f"assistant {number}"})
    return messages


def assert_tool_calls_paired(test_case, history):
    for index, message in enumerate(history):
        calls = message.get("tool_calls", [])
        if not calls:
            continue
        expected = [call["id"] for call in calls]
        actual = []
        for following in history[index + 1:]:
            if following.get("role") != "tool":
                break
            actual.append(following.get("tool_call_id"))
        test_case.assertEqual(expected, actual)


class HistoryTrimTests(unittest.TestCase):
    def make_agent(self, brain):
        agent = Agent.__new__(Agent)
        agent.brain = brain
        agent.history = [{"role": "system", "content": "system"}]
        for number in range(TRIM_TRIGGER_TURNS + 1):
            agent.history.extend(make_turn(number, with_tool=number in {2, 12}))
        return agent

    def test_trims_only_complete_turns_and_keeps_recent_turns_verbatim(self):
        brain = SummaryBrain()
        agent = self.make_agent(brain)
        before = copy.deepcopy(agent.history)
        recent_start = [
            i for i, message in enumerate(before)
            if message.get("role") == "user"
        ][-KEEP_RECENT_TURNS]
        recent_bytes = json.dumps(before[recent_start:], sort_keys=True)

        agent._trim_history_if_needed()

        self.assertLess(len(agent.history), len(before))
        self.assertEqual(agent.history[1]["role"], "assistant")
        self.assertTrue(agent.history[1]["content"].startswith(
            "[Summary of earlier conversation:"
        ))
        self.assertEqual(
            sum(message.get("content", "").startswith(
                "[Summary of earlier conversation:"
            ) for message in agent.history),
            1,
        )
        self.assertEqual(json.dumps(agent.history[2:], sort_keys=True), recent_bytes)
        self.assertEqual(brain.calls[0][1:], (None, None))
        assert_tool_calls_paired(self, agent.history)

    def test_summary_failure_uses_deterministic_fallback(self):
        agent = self.make_agent(SummaryBrain(RuntimeError("offline")))

        agent._trim_history_if_needed()

        summarized = TRIM_TRIGGER_TURNS + 1 - KEEP_RECENT_TURNS
        self.assertIn(
            f"Earlier in this conversation: {summarized} earlier exchanges not shown here.",
            agent.history[1]["content"],
        )
        assert_tool_calls_paired(self, agent.history)


if __name__ == "__main__":
    unittest.main()
