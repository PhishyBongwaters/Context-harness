import json
import tempfile
import unittest
from pathlib import Path

from harness.context import Budget
from harness.loop import Loop, Session
from harness.providers import MockProvider
from harness.usage import UsageTracker


def make_session(workdir=None):
    d = tempfile.mkdtemp()
    return Session(id="u", dir=Path(d), workdir=workdir or d)


class TestMeasure(unittest.TestCase):
    def test_breakdown_sums_to_total(self):
        s = make_session()
        loop = Loop(MockProvider([{"content": "done"}]),
                    Budget(hard=100000, soft=80000))
        bd = loop._measure(loop.provider, "sys",
                           [{"role": "user", "content": "hi"}],
                           loop._tools)
        self.assertEqual(bd["total"],
                         bd["system"] + bd["transcript"] + bd["tools"])
        self.assertGreater(bd["tools"], 0)
        self.assertGreater(bd["system"], 0)


class TestTracker(unittest.TestCase):
    def test_record_accumulates_and_persists(self):
        with tempfile.TemporaryDirectory() as d:
            t = UsageTracker(d)
            t.record(turn=1, phase="main", step=0,
                     breakdown={"system": 1, "transcript": 2, "tools": 3,
                                "total": 6},
                     server={"input": 100, "output": 20})
            t.record(turn=1, phase="main", step=1,
                     breakdown={"system": 1, "transcript": 4, "tools": 3,
                                "total": 8},
                     server={"input": 120, "output": 10})
            self.assertEqual(t.totals,
                             {"input": 220, "output": 30, "estimated": 14})
            t2 = UsageTracker(d)  # reloads from usage.json
            self.assertEqual(t2.totals, t.totals)
            self.assertEqual(len(t2.turns), 2)

    def test_loop_records_server_usage(self):
        s = make_session()
        events = []
        script = [{"content": "done",
                   "usage": {"input": 50, "output": 7}}]
        with tempfile.TemporaryDirectory() as d:
            tracker = UsageTracker(d)
            loop = Loop(MockProvider(script), Budget(100000, 80000),
                        on_event=lambda k, v: events.append((k, v)),
                        usage_tracker=tracker)
            loop.run_turn(s, "hi")
            self.assertEqual(tracker.totals["input"], 50)
            self.assertEqual(tracker.totals["output"], 7)
        reqs = [v for k, v in events
                if k == "request" and v.get("phase") == "main"]
        self.assertIn("breakdown", reqs[0])
        self.assertIn("usage_total", reqs[0])
        resps = [v for k, v in events if k == "response"]
        self.assertIn("usage_total", resps[0])

    def test_usage_note_sent_but_never_stored(self):
        s = make_session()
        loop = Loop(MockProvider([{"content": "done"}]),
                    Budget(100000, 80000))
        loop.run_turn(s, "hi")
        sent = loop.provider.calls[0]["messages"]
        self.assertEqual(sent[-1]["role"], "user")
        self.assertIn("[harness note:", sent[-1]["content"])
        self.assertNotIn("[harness note:", s.context.load())

    def test_usage_note_opt_out(self):
        s = make_session()
        loop = Loop(MockProvider([{"content": "done"}]),
                    Budget(100000, 80000), usage_note=False)
        loop.run_turn(s, "hi")
        sent = loop.provider.calls[0]["messages"]
        self.assertFalse(any("[harness note:" in (m.get("content") or "")
                             for m in sent))


if __name__ == "__main__":
    unittest.main()
