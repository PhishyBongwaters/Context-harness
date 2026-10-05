import json
import tempfile
import unittest
from pathlib import Path

from harness.context import Budget
from harness.debug import DebugLog
from harness.loop import Loop, Session
from harness.providers import MockProvider


def make_session(workdir=None):
    d = tempfile.mkdtemp()
    return Session(id="dbg", dir=Path(d), workdir=workdir or d)


class TestDebugLog(unittest.TestCase):
    def test_write_produces_valid_jsonl(self):
        with tempfile.TemporaryDirectory() as d:
            log = DebugLog(Path(d) / "debug.jsonl", session_id="s1")
            log.write("assistant", "hello")
            log.write("tool", {"name": "exec", "args": {}})
            lines = (Path(d) / "debug.jsonl").read_text(
                encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 2)
            first = json.loads(lines[0])
            self.assertEqual(first["session"], "s1")
            self.assertEqual(first["kind"], "assistant")
            self.assertIn("ts", first)

    def test_handler_tees_to_file_and_wrapped(self):
        seen = []
        with tempfile.TemporaryDirectory() as d:
            log = DebugLog(Path(d) / "debug.jsonl", session_id="s1")
            h = log.handler(lambda k, v: seen.append(k))
            h("assistant", "hi")
            self.assertEqual(seen, ["assistant"])
            self.assertEqual(
                len((Path(d) / "debug.jsonl").read_text(
                    encoding="utf-8").splitlines()), 1)

    def test_loop_emits_request_response(self):
        s = make_session()
        kinds = []
        loop = Loop(MockProvider([{"content": "done"}]),
                    Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: kinds.append(k))
        self.assertEqual(loop.run_turn(s, "hi"), "done")
        self.assertIn("request", kinds)
        self.assertIn("response", kinds)

    def test_loop_events_land_in_debug_file(self):
        s = make_session()
        with tempfile.TemporaryDirectory() as d:
            log = DebugLog(Path(d) / "debug.jsonl", session_id=s.id)
            loop = Loop(MockProvider([{"content": "done"}]),
                        Budget(hard=100000, soft=80000),
                        on_event=log.handler())
            loop.run_turn(s, "hi")
            kinds = [json.loads(line)["kind"]
                     for line in (Path(d) / "debug.jsonl").read_text(
                         encoding="utf-8").splitlines()]
            self.assertIn("request", kinds)
            self.assertIn("response", kinds)
            self.assertIn("assistant", kinds)


if __name__ == "__main__":
    unittest.main()
