import threading
import time
import unittest

from jarvis.agent import Agent, _CONFIRMATION_CAPABLE_TOOLS


class ExecuteToolCallsTests(unittest.TestCase):
    """Agent._execute_tool_calls: same-round tool calls run concurrently when
    none of them can ever require confirmation, and strictly sequentially
    (in original order) the moment any of them can — see the rationale on
    _CONFIRMATION_CAPABLE_TOOLS in jarvis/agent.py."""

    def _make_agent(self):
        return Agent.__new__(Agent)

    def test_results_returned_in_call_order(self):
        agent = self._make_agent()
        agent._execute_tool = lambda name, args: f"result:{name}:{args.get('n')}"
        calls = [("web_search", {"n": 1}), ("read_file", {"n": 2}), ("tracker_list", {"n": 3})]
        self.assertEqual(
            agent._execute_tool_calls(calls),
            ["result:web_search:1", "result:read_file:2", "result:tracker_list:3"],
        )

    def test_safe_batch_actually_runs_concurrently(self):
        # A 3-party barrier only releases once all 3 calls are in flight AT
        # THE SAME TIME — real proof of concurrency, not a timing guess. If
        # _execute_tool_calls ran these one at a time, the first call would
        # block here alone until the barrier's timeout raises
        # BrokenBarrierError, which would fail this test loudly.
        agent = self._make_agent()
        barrier = threading.Barrier(3, timeout=2)

        def fake_execute_tool(name, args):
            barrier.wait()
            return name

        agent._execute_tool = fake_execute_tool
        calls = [("web_search", {}), ("read_file", {}), ("tracker_list", {})]
        results = agent._execute_tool_calls(calls)
        self.assertEqual(results, ["web_search", "read_file", "tracker_list"])

    def test_batch_with_a_confirmation_capable_tool_runs_sequentially(self):
        agent = self._make_agent()
        intervals = []
        lock = threading.Lock()

        def fake_execute_tool(name, args):
            start = time.monotonic()
            time.sleep(0.05)
            end = time.monotonic()
            with lock:
                intervals.append((name, start, end))
            return name

        agent._execute_tool = fake_execute_tool
        calls = [("web_search", {}), ("delete_file", {"path": "x"}), ("read_file", {})]
        results = agent._execute_tool_calls(calls)

        self.assertEqual(results, ["web_search", "delete_file", "read_file"])
        self.assertEqual(len(intervals), 3)
        by_name = {name: (start, end) for name, start, end in intervals}
        ordered = [name for name, _ in calls]
        for earlier, later in zip(ordered, ordered[1:]):
            self.assertLessEqual(by_name[earlier][1], by_name[later][0])

    def test_confirmation_capable_tool_anywhere_in_batch_forces_sequential(self):
        # Same as above, but the risky tool is FIRST — the whole batch must
        # still be sequential, not just "risky tool runs alone".
        agent = self._make_agent()
        intervals = []
        lock = threading.Lock()

        def fake_execute_tool(name, args):
            start = time.monotonic()
            time.sleep(0.05)
            end = time.monotonic()
            with lock:
                intervals.append((name, start, end))
            return name

        agent._execute_tool = fake_execute_tool
        calls = [("run_command", {"command": "echo hi"}), ("web_search", {}), ("read_file", {})]
        agent._execute_tool_calls(calls)
        by_name = {name: (start, end) for name, start, end in intervals}
        ordered = [name for name, _ in calls]
        for earlier, later in zip(ordered, ordered[1:]):
            self.assertLessEqual(by_name[earlier][1], by_name[later][0])

    def test_single_call_batch_bypasses_thread_pool(self):
        agent = self._make_agent()
        agent._execute_tool = lambda name, args: f"solo:{name}"
        self.assertEqual(agent._execute_tool_calls([("web_search", {})]), ["solo:web_search"])

    def test_empty_batch_returns_empty(self):
        agent = self._make_agent()
        agent._execute_tool = lambda name, args: "unused"
        self.assertEqual(agent._execute_tool_calls([]), [])

    def test_confirmation_capable_tools_pins_known_guard_calling_tools(self):
        # Documents the exhaustive set derived from `grep "guard(" -r
        # jarvis/tools/` at the time this was written. If this test starts
        # failing because a new tool now calls guard(), add its name to
        # _CONFIRMATION_CAPABLE_TOOLS in jarvis/agent.py, not just this test.
        expected = {
            "delete_file", "move_file",
            "run_command", "run_python", "run_claude_code", "shutdown_or_restart",
            "calendar_delete_event",
            "browser_click", "app_click", "vision_click",
        }
        self.assertEqual(_CONFIRMATION_CAPABLE_TOOLS, expected)


if __name__ == "__main__":
    unittest.main()
