"""Wake word goes completely silent while any browser tab has the Kareem
website open (VoiceController._on_wake_trigger) -- but the hotkey and the
in-browser voice input are both untouched. The check is against LIVE
connection state (kareem.web.server.page_is_open, backed by the same
WebSocket client count simple-mode's close-tab-to-quit logic already
uses) on every single detection, not a cached snapshot from startup."""

import unittest
from unittest.mock import MagicMock, patch

from kareem.voice.controller import VoiceController


class WakeWordWebSuppressionTests(unittest.TestCase):
    def _controller(self):
        agent = MagicMock()
        controller = VoiceController(agent, MagicMock())
        controller._on_trigger = MagicMock()
        return controller

    def test_wake_word_silently_ignored_when_a_tab_is_open(self):
        controller = self._controller()
        controller.page_is_open = MagicMock(return_value=True)
        controller._on_wake_trigger()
        controller._on_trigger.assert_not_called()

    def test_wake_word_fires_normally_when_no_tab_is_open(self):
        controller = self._controller()
        controller.page_is_open = MagicMock(return_value=False)
        controller._on_wake_trigger()
        controller._on_trigger.assert_called_once()

    def test_check_is_live_not_a_startup_snapshot(self):
        # Same controller, same page_is_open callable, but its RETURN VALUE
        # changes between calls -- exactly what happens when a tab opens
        # then closes while Kareem keeps running. The gate must re-evaluate
        # every time, not remember the first answer.
        controller = self._controller()
        states = iter([True, False, True])
        controller.page_is_open = MagicMock(side_effect=lambda: next(states))

        controller._on_wake_trigger()  # tab open -> suppressed
        controller._on_trigger.assert_not_called()

        controller._on_wake_trigger()  # tab closed -> fires
        controller._on_trigger.assert_called_once()

        controller._on_trigger.reset_mock()
        controller._on_wake_trigger()  # tab open again -> suppressed again
        controller._on_trigger.assert_not_called()

    def test_no_side_effects_at_all_when_suppressed(self):
        # "Silently ignored" means nothing happens -- not even acquiring the
        # busy lock or spawning the interaction thread that would otherwise
        # print/log. Verify the real (non-mocked) _on_trigger's own guard
        # state is untouched, using the REAL _on_trigger rather than a mock
        # so a regression that moves logging earlier would be caught too.
        controller = self._controller()
        controller._on_trigger = VoiceController._on_trigger.__get__(controller)
        controller.page_is_open = MagicMock(return_value=True)
        with patch("threading.Thread") as mock_thread:
            controller._on_wake_trigger()
        mock_thread.assert_not_called()
        self.assertFalse(controller._busy.locked())

    def test_falls_through_when_page_is_open_was_never_wired(self):
        # web interface unavailable (fastapi/uvicorn missing, or disabled) ->
        # main.py never sets controller.page_is_open, leaving it None. The
        # wake word must behave exactly as it always did, not silently break.
        controller = self._controller()
        self.assertIsNone(controller.page_is_open)
        controller._on_wake_trigger()
        controller._on_trigger.assert_called_once()

    def test_hotkey_registration_bypasses_the_gate_entirely(self):
        # The hotkey must call _on_trigger directly -- never _on_wake_trigger
        # -- so it keeps working regardless of whether a tab is open.
        controller = self._controller()
        controller.page_is_open = MagicMock(return_value=True)  # tab open

        captured = {}

        class FakeGlobalHotKeys:
            def __init__(self, mapping):
                captured["callback"] = next(iter(mapping.values()))
            def start(self): pass
            daemon = False

        with patch("pynput.keyboard.GlobalHotKeys", FakeGlobalHotKeys), \
             patch("kareem.config.HOTKEY", "ctrl+alt+j"), \
             patch("kareem.config.WAKE_WORD_ENABLED", False), \
             patch("kareem.voice.tts.Speaker"):
            controller.start()

        # Bound methods compare equal (same __self__/__func__) even though
        # two separate accesses of controller._on_trigger produce distinct
        # bound-method OBJECTS -- assertIs would spuriously fail here.
        self.assertEqual(captured["callback"], controller._on_trigger,
                          "the hotkey must be wired directly to _on_trigger, not the wake-word gate")

    def test_wakeword_listener_is_wired_to_the_gated_callback(self):
        controller = self._controller()
        captured = {}

        class FakeWakeWordListener:
            def __init__(self, on_wake):
                captured["on_wake"] = on_wake
            def start(self): return True

        with patch("kareem.voice.wakeword.WakeWordListener", FakeWakeWordListener), \
             patch("kareem.config.WAKE_WORD_ENABLED", True), \
             patch("kareem.config.HOTKEY", None), \
             patch("kareem.voice.tts.Speaker"):
            controller.start()

        self.assertEqual(captured["on_wake"], controller._on_wake_trigger,
                          "WakeWordListener must be given the gated callback, not _on_trigger directly")


if __name__ == "__main__":
    unittest.main()
