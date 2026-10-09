"""Tests for T6/T7: scratch.md as the episode working file.

During a turn the harness appends assistant sections and tool results
to scratch.md (T6). When the assistant replies with no more tool
calls, the episode closes: scratch is archived and cleared (T7).
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "tests")
from test_loop import make_session  # noqa: E402

from harness.context import parse_transcript
from harness.assembly import assemble
from harness.loop import Budget, Loop
from harness.providers import MockProvider


def tool_script():
    return [
        {"content": None, "tool_calls": [
            {"id": "e1", "name": "exec",
             "arguments": {"command": "echo 42"}}]},
        {"content": "done"},
    ]


class TestScratchTraces(unittest.TestCase):
    def test_tool_traces_archived_not_in_history(self):
        # T7: after close the trace lives in the archive, never in
        # history.md (which keeps user messages + episode pointers).
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "do the thing"), "done")
        archived = "".join(
            p.read_text(encoding="utf-8")
            for p in (s.dir / "archive").glob("*.md"))
        self.assertIn("## assistant", archived)
        self.assertIn("## tool e1", archived)
        self.assertIn("42", archived)
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertNotIn("## tool", history)
        self.assertNotRegex(history, r"(?m)^## assistant t\d+\n")

    def test_scratch_cleared_after_close(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        self.assertEqual(
            (s.dir / "scratch.md").read_text(encoding="utf-8"), "")

    def test_user_message_stamped_in_history(self):
        s = make_session()
        loop = Loop(MockProvider([{"content": "hi"}]), Budget(100000, 80000))
        loop.run_turn(s, "first question")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertIn("## user t0001", history)
        self.assertIn("first question", history)

    def test_turn_counter_monotonic_across_turns(self):
        s = make_session()
        loop = Loop(MockProvider([{"content": "a"}, {"content": "b"}]),
                    Budget(100000, 80000))
        loop.run_turn(s, "one")
        loop.run_turn(s, "two")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertIn("## user t0001", history)
        self.assertIn("## user t0002", history)

    def test_context_md_is_assembled_artifact(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        artifact = (s.dir / "context.md").read_text(encoding="utf-8")
        # Assembled: sats, history (user + episode pointer). The tool
        # trace is archived, not in hot context.
        self.assertIn("## sat facts", artifact)
        self.assertIn("## user t0001", artifact)
        self.assertIn("## episode t0001", artifact)
        self.assertNotIn("## tool e1", artifact)

    def test_auto_dedupe_rehomed_to_scratch(self):
        s = make_session()
        loop = Loop(MockProvider([]), Budget(100000, 80000))
        dup = "## tool e9\nsame result\n"
        (s.dir / "scratch.md").write_text(dup + dup, encoding="utf-8")
        loop._auto_dedupe(s)
        after = (s.dir / "scratch.md").read_text(encoding="utf-8")
        self.assertEqual(after.count("## tool e9"), 1)


if __name__ == "__main__":
    unittest.main()
