"""Cooperative turn interrupt: request_stop() halts run_turn at the
next safe point (between steps, before model calls and tool runs)."""
import tempfile
import unittest
from pathlib import Path
from harness.tui import has_tui

from harness.context import Budget
from harness.loop import Loop, Session
from harness.providers import MockProvider


def make_session() -> Session:
    d = tempfile.mkdtemp()
    return Session(id="t", dir=Path(d), workdir=d)


class StopOnFirstChat(MockProvider):
    """Requests a stop inside the first chat() call."""

    def __init__(self, script, loop_box):
        super().__init__(script)
        self._loop_box = loop_box

    def chat(self, **kw):
        if not self.calls:
            self._loop_box["loop"].request_stop()
        return super().chat(**kw)


class TestInterrupt(unittest.TestCase):
    def _loop(self, script, events=None):
        box = {}
        provider = MockProvider(script)
        sink = events if events is not None else []
        loop = Loop(provider, Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: sink.append(k))
        box["loop"] = loop
        return loop, box

    def test_stop_before_start_runs_nothing(self):
        events = []
        loop, _ = self._loop([{"content": "never"}], events)
        loop.request_stop()
        out = loop.run_turn(make_session(), "hi")
        self.assertEqual(out, "")
        self.assertEqual(loop.provider.calls, [])
        self.assertIn("interrupted", events)

    def test_stop_mid_turn_before_second_chat(self):
        events = []
        box = {}
        provider = StopOnFirstChat(
            [{"content": None,
              "tool_calls": [{"id": "c1", "name": "exec",
                              "arguments": {"command": "echo hi"}}]},
             {"content": "second"}],
            box)
        loop = Loop(provider, Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append(k))
        box["loop"] = loop
        out = loop.run_turn(make_session(), "go")
        self.assertEqual(out, "")
        # exactly one model call happened; the tool never ran
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("interrupted", events)

    def test_flag_cleared_for_next_turn(self):
        events = []
        loop, _ = self._loop([{"content": "first"}] * 2, events)
        loop.request_stop()
        self.assertEqual(loop.run_turn(make_session(), "hi"), "")
        # next turn runs normally: the stale flag was cleared
        self.assertEqual(loop.run_turn(make_session(), "hi"), "first")
        self.assertEqual(events.count("interrupted"), 1)

    def test_request_stop_without_turn_aborts_next(self):
        loop, _ = self._loop([{"content": "ok"}])
        loop.request_stop()  # no turn running: must not raise...
        # ...but the next turn honors the stop instead of running
        self.assertEqual(loop.run_turn(make_session(), "hi"), "")


if __name__ == "__main__":
    unittest.main()
@unittest.skipUnless(has_tui(), "textual extra missing")
class TestInterruptDuringToolPhase(unittest.IsolatedAsyncioTestCase):
    """Escape must register a stop while the worker runs a tool.

    Regression: the interrupt guard used the request-span timer
    (_turn_start), which _poll clears on every "response"/"tool" event.
    During a long tool execution the timer is None while the turn is
    still running, so escape silently did nothing.
    """

    async def test_escape_registers_stop_when_request_span_ended(self):
        from harness.tui.app import HarnessApp
        from harness.tui.bridge import TuiBridge

        loop = Loop(MockProvider([{"content": "ok"}]),
                    Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: None)
        app = HarnessApp(loop, make_session(), TuiBridge())
        async with app.run_test() as pilot:
            # Simulate the UI state mid-tool-execution: the turn is
            # running, but the last event processed was "response", so
            # the request-span timer was cleared.
            app._turn_running = True
            app._turn_start = None
            await pilot.press("escape")
            self.assertTrue(loop._stop_event.is_set(),
                            "escape must register stop during tool phase")
            # With no turn running, escape stays a silent no-op.
            loop._stop_event.clear()
            app._turn_running = False
            await pilot.press("escape")
            self.assertFalse(loop._stop_event.is_set())
