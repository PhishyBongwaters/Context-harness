"""Red-first: deterministic history windowing (H2).

When history.md exceeds the token cap, the harness archives the oldest
turns (whole turns: user + assistant + episode sections) to
archive/history-<date>-t<NNNN>-t<NNNN>.md and leaves a
## history-archive pointer. The newest turn is never archived. No model
summarization -- just windowing with lookback.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, "tests")
from test_loop import make_session  # noqa: E402

from harness.context import count_tokens, parse_transcript
from harness.deterministic import window_history
from harness.loop import Budget, Loop
from harness.providers import MockProvider


def write_history(s, turns):
    """turns: list of (user_text, assistant_text)."""
    parts = []
    for i, (u, a) in enumerate(turns, 1):
        tag = f"t{i:04d}"
        parts.append(f"## user {tag}\n{u}\n")
        parts.append(f"## episode {tag}\narchive: archive/x.md\n"
                     f"turns: {tag}-{tag}\ntool_calls: 0\n")
        parts.append(f"## assistant {tag}\n{a}\n")
    (s.dir / "history.md").write_text("".join(parts), encoding="utf-8")


class TestHistoryWindowing(unittest.TestCase):
    def test_no_window_when_under_cap(self):
        s = make_session()
        write_history(s, [("q1", "a1")])
        rep = window_history(s.dir, cap_tokens=10 ** 9)
        self.assertFalse(rep["windowed"])
        self.assertIn("## user t0001",
                      (s.dir / "history.md").read_text(encoding="utf-8"))

    def test_windows_oldest_turns(self):
        s = make_session()
        write_history(s, [("q1", "a1"), ("q2", "a2"), ("q3", "a3")])
        # Cap fits ~1.5 turns: oldest must go.
        one_turn = count_tokens("## user t0001\nq1\n## episode t0001\n"
                                "archive: archive/x.md\nturns: t0001-t0001\n"
                                "tool_calls: 0\n## assistant t0001\na1\n")
        rep = window_history(s.dir, cap_tokens=int(one_turn * 1.5))
        self.assertTrue(rep["windowed"])
        self.assertEqual(rep["turns"], "t0001-t0002")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertNotIn("q1", history)
        self.assertNotIn("q2", history)
        self.assertIn("## user t0003", history)
        self.assertIn("q3", history)
        self.assertIn("## history-archive", history)
        self.assertIn(rep["archive"], history)
        # Archived file holds the moved turns verbatim.
        archived = (s.dir / rep["archive"]).read_text(encoding="utf-8")
        self.assertIn("## user t0001", archived)
        self.assertIn("q2", archived)
        self.assertNotIn("q3", archived)

    def test_never_archives_newest_turn(self):
        s = make_session()
        write_history(s, [("q1", "a1"), ("q2", "a2")])
        rep = window_history(s.dir, cap_tokens=1)
        self.assertTrue(rep["windowed"])
        # Newest turn always survives, even under a tiny cap.
        self.assertEqual(rep["turns"], "t0001-t0001")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertNotIn("q1", history)
        self.assertIn("q2", history)

    def test_single_turn_nothing_to_archive(self):
        s = make_session()
        write_history(s, [("q1", "a1")])
        rep = window_history(s.dir, cap_tokens=1)
        self.assertFalse(rep["windowed"])
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertIn("q1", history)

    def test_pointer_parses_as_user_message(self):
        s = make_session()
        write_history(s, [("q1", "a1"), ("q2", "a2")])
        one_turn = count_tokens("## user t0001\nq1\n")
        window_history(s.dir, cap_tokens=one_turn)
        from harness.assembly import assemble
        msgs = parse_transcript(assemble(s.dir))
        ptrs = [m for m in msgs if m["role"] == "user"
                and "history-" in (m.get("content") or "")]
        self.assertEqual(len(ptrs), 1)
        self.assertIn("t0001", ptrs[0]["content"])

    def test_close_triggers_windowing(self):
        s = make_session()
        loop = Loop(MockProvider([{"content": "a"}, {"content": "b"},
                                  {"content": "c"}]),
                    Budget(100000, 80000), history_cap=80)
        loop.run_turn(s, "one two three four five six seven eight")
        loop.run_turn(s, "nine ten eleven twelve thirteen fourteen")
        loop.run_turn(s, "fifteen sixteen seventeen eighteen nineteen")
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        # Oldest turns were windowed out deterministically.
        self.assertIn("## history-archive", history)
        self.assertIn("## user t0003", history)


if __name__ == "__main__":
    unittest.main()
