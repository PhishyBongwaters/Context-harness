"""Red-first tests for T7: episode close.

Spec: docs/assembly-spec.md section 6. When the assistant replies with
no more tool calls, the harness archives scratch.md, clears it, and
records a `## episode t<NNNN>` pointer in history.md (archive path,
turn range, tool-call count).
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, "tests")
from test_loop import make_session  # noqa: E402

from harness.assembly import assemble
from harness.context import parse_transcript
from harness.loop import Budget, Loop
from harness.providers import MockProvider


def tool_script():
    return [
        {"content": None, "tool_calls": [
            {"id": "e1", "name": "exec",
             "arguments": {"command": "echo 42"}}]},
        {"content": "done"},
    ]


def archived_files(s):
    return list((s.dir / "archive").glob("*.md"))


class TestEpisodeClose(unittest.TestCase):
    def test_close_archives_scratch_and_clears(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "do the thing"), "done")
        # scratch is cleared...
        self.assertEqual(
            (s.dir / "scratch.md").read_text(encoding="utf-8"), "")
        # ...and the full episode trace lives in one archive file.
        files = archived_files(s)
        self.assertEqual(len(files), 1)
        archived = files[0].read_text(encoding="utf-8")
        self.assertIn("## assistant", archived)
        self.assertIn("## tool e1", archived)
        self.assertIn("42", archived)
        self.assertIn("done", archived)

    def test_archive_filename_format(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        files = archived_files(s)
        self.assertEqual(len(files), 1)
        self.assertRegex(files[0].name, r"^\d{8}-t0001\.md$")

    def test_history_gets_episode_pointer(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertIn("## episode t0001", history)
        files = archived_files(s)
        self.assertIn(files[0].name, history)
        self.assertIn("tool_calls: 1", history)

    def test_pointer_parses_as_user_message(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        msgs = parse_transcript(assemble(s.dir))
        users = [m for m in msgs if m["role"] == "user"]
        # The pointer is a plain user-role message naming the archive.
        self.assertTrue(any("archive/" in (m.get("content") or "")
                            for m in users))

    def test_next_turn_sees_pointer_not_trace(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script() + [{"content": "again"}]),
                    Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        seen = []
        orig = loop.provider.chat

        def spy(**kw):
            seen.append(kw["messages"])
            return orig(**kw)

        loop.provider.chat = spy
        loop.run_turn(s, "what did you find?")
        msgs = seen[0]
        # Pointer present...
        self.assertTrue(any("archive/" in (m.get("content") or "")
                            for m in msgs if m["role"] == "user"))
        # ...but the archived tool trace is NOT in hot context.
        self.assertFalse(any(m.get("role") == "tool" for m in msgs))

    def test_tool_call_count_recorded(self):
        s = make_session()
        script = [
            {"content": None, "tool_calls": [
                {"id": "e1", "name": "exec",
                 "arguments": {"command": "echo a"}},
                {"id": "e2", "name": "exec",
                 "arguments": {"command": "echo b"}}]},
            {"content": "done"},
        ]
        loop = Loop(MockProvider(script), Budget(100000, 80000))
        loop.run_turn(s, "two tools")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertIn("tool_calls: 2", history)

    def test_second_turn_closes_second_episode(self):
        s = make_session()
        loop = Loop(MockProvider([{"content": "a"}, {"content": "b"}]),
                    Budget(100000, 80000))
        loop.run_turn(s, "one")
        loop.run_turn(s, "two")
        files = archived_files(s)
        self.assertEqual(len(files), 2)
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertIn("## episode t0001", history)
        self.assertIn("## episode t0002", history)

    def test_closing_reply_recorded_in_history(self):
        # H1: the harness records the closing reply deterministically;
        # the model is never asked to "move durable things" itself.
        s = make_session()
        loop = Loop(MockProvider([{"content": "hello there"}]),
                    Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "hi"), "hello there")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertIn("## assistant t0001", history)
        self.assertIn("hello there", history)

    def test_history_order_user_episode_assistant(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        iu = history.index("## user t0001")
        ie = history.index("## episode t0001")
        ia = history.index("## assistant t0001")
        self.assertLess(iu, ie)
        self.assertLess(ie, ia)

    def test_empty_reply_not_recorded(self):
        s = make_session()
        loop = Loop(MockProvider([{"content": None}]),
                    Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "hi"), "")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertNotRegex(history, r"(?m)^## assistant t\d+\n")

    def test_episode_close_emits_event(self):
        s = make_session()
        events = []
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000),
                    on_event=lambda k, v: events.append((k, v)))
        loop.run_turn(s, "do the thing")
        closes = [v for k, v in events if k == "episode-close"]
        self.assertEqual(len(closes), 1)
        self.assertEqual(closes[0]["tool_calls"], 1)
        self.assertIn("archive", closes[0])


if __name__ == "__main__":
    unittest.main()
