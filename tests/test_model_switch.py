"""Web-UI model switcher: kareem.brain.build_brain/MODEL_OPTIONS,
kareem.agent.Agent.switch_brain, and the /api/model endpoints
(kareem/web/server.py). Constructing the new brain IS the validation
(build_brain actually instantiates the class -- real key/reachability
checks) so a bad choice must never leave the agent brainless; these tests
verify the previous brain survives a failed switch, not just that the
call doesn't raise."""

import threading
import unittest
from unittest.mock import MagicMock, patch

from kareem import brain as brain_module
from kareem.agent import Agent


class FakeBrain:
    def __init__(self, model=None):
        self.model = model
        self.calls = []

    def chat(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return "ok"


class BuildBrainTests(unittest.TestCase):
    def test_build_brain_dispatches_to_the_right_class(self):
        with patch.object(brain_module, "ClaudeBrain", FakeBrain) as fake:
            result = brain_module.build_brain("claude", model="claude-opus-5")
        self.assertIsInstance(result, FakeBrain)
        self.assertEqual(result.model, "claude-opus-5")

    def test_build_brain_rejects_unknown_brain(self):
        with self.assertRaises(ValueError):
            brain_module.build_brain("gpt5", model=None)

    def test_model_options_are_internally_consistent(self):
        # Every option names a real brain kind build_brain understands, and
        # ids are unique (the web UI looks options up by id).
        seen_ids = set()
        for option in brain_module.MODEL_OPTIONS:
            self.assertIn(option["brain"], {"claude", "hosted", "ollama"})
            self.assertNotIn(option["id"], seen_ids, f"duplicate option id {option['id']!r}")
            seen_ids.add(option["id"])


class AgentSwitchBrainTests(unittest.TestCase):
    def _agent(self):
        from kareem import config

        agent = Agent.__new__(Agent)
        agent.brain = FakeBrain(model="original")
        agent.history = [{"role": "system", "content": "original system prompt"}]
        # switch_brain() mutates config.BRAIN as a real side effect (that's
        # the point — everything else reads it live) — restore it after
        # every test in this class so a leaked value can't affect tests
        # that run afterward in the same discover session.
        self.addCleanup(setattr, config, "BRAIN", config.BRAIN)
        return agent

    def test_successful_switch_replaces_brain_and_refreshes_system_message(self):
        agent = self._agent()
        with patch.object(brain_module, "build_brain", return_value=FakeBrain(model="claude-opus-5")) as mock_build:
            ok, detail = agent.switch_brain("claude", "claude-opus-5")
        self.assertTrue(ok)
        mock_build.assert_called_once_with("claude", model="claude-opus-5")
        self.assertEqual(agent.brain.model, "claude-opus-5")

    def test_failed_switch_leaves_previous_brain_running(self):
        agent = self._agent()
        original_brain = agent.brain
        with patch.object(brain_module, "build_brain", side_effect=RuntimeError("no CLAUDE_API_KEY")):
            ok, detail = agent.switch_brain("claude", "claude-opus-5")
        self.assertFalse(ok)
        self.assertIn("no CLAUDE_API_KEY", detail)
        self.assertIs(agent.brain, original_brain, "the previous brain must still be running after a failed switch")

    def test_switching_updates_config_brain_live(self):
        from kareem import config
        agent = self._agent()
        original = config.BRAIN
        try:
            with patch.object(brain_module, "build_brain", return_value=FakeBrain()):
                agent.switch_brain("ollama", None)
            self.assertEqual(config.BRAIN, "ollama")
        finally:
            config.BRAIN = original

    def test_switching_to_ollama_swaps_in_the_small_system_prompt(self):
        # _system_message() also appends a real memory block (from
        # kareem.memory) when the user has remembered facts stored, so
        # assert the small prompt is the PREFIX -- not exact equality,
        # which would break on this machine's real memory.json content.
        from kareem import agent as agent_module
        agent = self._agent()
        with patch.object(brain_module, "build_brain", return_value=FakeBrain()):
            agent.switch_brain("ollama", None)
        self.assertTrue(agent.history[0]["content"].startswith(agent_module.SYSTEM_PROMPT_SMALL))


class ModelEndpointTests(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from kareem import config
        from kareem.web import server

        self.agent = Agent.__new__(Agent)
        self.agent.brain = FakeBrain(model="original")
        self.agent.history = [{"role": "system", "content": "original"}]
        self.app = server.create_app(self.agent, threading.Lock())
        self.client = TestClient(self.app)
        self.addCleanup(setattr, config, "BRAIN", config.BRAIN)

    def test_get_model_status_returns_options(self):
        response = self.client.get("/api/model")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("options", data)
        self.assertTrue(len(data["options"]) >= 3)
        self.assertIn("brain", data)

    def test_post_unknown_option_id_is_rejected(self):
        response = self.client.post("/api/model", json={"id": "not-a-real-option"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["switched"])
        # A rejected switch must not touch the running brain.
        self.assertEqual(self.agent.brain.model, "original")

    def test_post_valid_option_switches_and_reports_success(self):
        with patch.object(brain_module, "build_brain", return_value=FakeBrain(model="claude-haiku-4-5-20251001")):
            response = self.client.post("/api/model", json={"id": "claude-haiku"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["switched"])
        self.assertEqual(self.agent.brain.model, "claude-haiku-4-5-20251001")

    def test_post_construction_failure_returns_error_and_keeps_old_brain(self):
        original_brain = self.agent.brain
        with patch.object(brain_module, "build_brain", side_effect=RuntimeError("Ollama unreachable")):
            response = self.client.post("/api/model", json={"id": "ollama"})
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["switched"])
        self.assertIn("Ollama unreachable", data["reason"])
        self.assertIs(self.agent.brain, original_brain)


if __name__ == "__main__":
    unittest.main()
