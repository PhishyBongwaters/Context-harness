import tempfile
import unittest
from pathlib import Path

from harness.context import (Budget, count_tokens, parse_transcript,
                             render_assistant, render_tool, render_user)
from harness.deterministic import (cap_sections, dedupe_exact,
                                   evict_oldest_tools, prune_deterministic)
from harness.loop import Loop, Session
from harness.providers import MockProvider


def tool_sec(cid, body):
    return render_tool(cid, body)


def test_session():
    d = tempfile.mkdtemp()
    return Session(id="d", dir=Path(d), workdir=d)


class TestDedupe(unittest.TestCase):
    def test_collapses_dupes_keeps_newest(self):
        text = (render_user("q") + render_assistant("a1", None)
                + tool_sec("c1", "same-output")
                + render_assistant("a2", None) + tool_sec("c2", "same-output")
                + render_assistant("a3", None))
        new, n = dedupe_exact(text)
        self.assertEqual(n, 1)
        # newest duplicate (c2) survives, oldest (c1) goes
        self.assertIn("## tool c2", new)
        self.assertNotIn("## tool c1", new)

    def test_user_sections_never_touched(self):
        text = render_user("same") + render_user("same")
        new, n = dedupe_exact(text)
        self.assertEqual((new, n), (text, 0))

    def test_no_dupes_noop(self):
        text = render_user("q") + render_assistant("a", None)
        self.assertEqual(dedupe_exact(text), (text, 0))


class TestEvict(unittest.TestCase):
    def test_keeps_newest_n(self):
        text = (render_user("q")
                + tool_sec("c1", "old") + tool_sec("c2", "mid")
                + tool_sec("c3", "new"))
        new, dropped = evict_oldest_tools(text, keep=2)
        self.assertEqual(dropped, ["c1"])
        self.assertIn("## tool c3", new)
        self.assertNotIn("## tool c1", new)

    def test_fence_scrubbed(self):
        tcs = [{"id": "c1", "name": "exec", "arguments": {}},
               {"id": "c3", "name": "exec", "arguments": {}}]
        text = (render_user("q") + render_assistant("run", tcs)
                + tool_sec("c1", "old") + tool_sec("c3", "new"))
        new, dropped = evict_oldest_tools(text, keep=1)
        self.assertEqual(dropped, ["c1"])
        msgs = parse_transcript(new)
        asm = [m for m in msgs if m["role"] == "assistant"][0]
        self.assertEqual([t["id"] for t in asm["tool_calls"]], ["c3"])

    def test_empty_fence_removed(self):
        tcs = [{"id": "c1", "name": "exec", "arguments": {}}]
        text = (render_user("q") + render_assistant("run", tcs)
                + tool_sec("c1", "old") + tool_sec("c2", "x")
                + tool_sec("c3", "y"))
        new, _ = evict_oldest_tools(text, keep=2)
        self.assertNotIn("tool-calls", new)


class TestCap(unittest.TestCase):
    def test_big_tool_capped(self):
        big = "\n".join(f"line {i}" for i in range(2000))
        text = render_user("q") + tool_sec("c1", big)
        new, n = cap_sections(text, cap=200)
        self.assertEqual(n, 1)
        self.assertIn("elided by deterministic cap", new)
        self.assertLess(count_tokens(new), count_tokens(text))

    def test_small_untouched(self):
        text = render_user("q") + tool_sec("c1", "tiny")
        self.assertEqual(cap_sections(text, cap=200)[1], 0)


class TestLadder(unittest.TestCase):
    def test_stops_at_target(self):
        text = (render_user("q") + tool_sec("c1", "x" * 5000)
                + tool_sec("c1", "x" * 5000))
        new, rep = prune_deterministic(text, target=10 ** 9)
        self.assertEqual(rep["deduped"], 0)
        new, rep = prune_deterministic(text, target=1)
        self.assertLessEqual(rep["tokens_after"], rep["tokens_before"])

    def test_prune_turn_prefers_deterministic(self):
        # T6: the deterministic ladder runs on scratch.md; bloat there
        # is handled without the janitor.
        s = test_session()
        dup = "z" * 1500
        (s.dir / "scratch.md").write_text(
            tool_sec("c1", dup) + tool_sec("c2", dup) + tool_sec("c3", dup),
            encoding="utf-8")
        main = MockProvider([{"content": "done"}])
        janitor = MockProvider([])
        loop = Loop(main, Budget(hard=10 ** 9, soft=10 ** 9),
                    prune_provider=janitor)
        # force deterministic with a low target, agent never needed
        loop.prune_target = 1
        loop.prune_keep_tools = 1
        self.assertTrue(loop.prune_turn(s))
        self.assertEqual(janitor.calls, [])
        scratch = (s.dir / "scratch.md").read_text(encoding="utf-8")
        self.assertIn("## tool c3", scratch)
        self.assertNotIn("## tool c1", scratch)

    def test_archive_oldest_half_when_way_over(self):
        import tempfile, os
        # Build a text with 10 sections, way over a tiny target.
        text = "".join(
            render_user(f"q{i}") + f"## assistant\nanswer {i}\n"
            for i in range(10))
        with tempfile.TemporaryDirectory() as d:
            new, rep = prune_deterministic(
                text, target=10, archive_dir=d)
            # Archive file was created.
            self.assertIsNotNone(rep["archived"])
            self.assertTrue(os.path.exists(rep["archived"]))
            # Archived content has the oldest sections.
            archived = open(rep["archived"]).read()
            self.assertIn("q0", archived)
            # Trimmed text keeps the newest.
            self.assertIn("q9", new)
            self.assertNotIn("q0", new)


if __name__ == "__main__":
    unittest.main()
