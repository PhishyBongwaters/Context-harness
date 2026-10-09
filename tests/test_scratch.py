"""Red-first tests for T6: tool traces live in scratch.md.

Spec: docs/assembly-spec.md section 6. During a turn the harness
appends assistant sections and tool results to scratch.md (never the
assembled transcript). User messages go to history.md, stamped.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "tests")
from test_loop import make_session  # noqa: E402

from harness.context import parse_transcript
from harness.loop import Budget, Loop
from harness.providers import MockProvider


def tool_script(path="irrelevant"):
    return [
        {"content": None, "tool_calls": [
            {"id": "e1", "name": "exec",
             "arguments": {"command": "echo 42"}}]},
        {"content": "done"},
    ]


class TestScratchTraces(unittest.TestCase):
    def test_tool_traces_go_to_scratch_not_history(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "do the thing"), "done")
        scratch = (s.dir / "scratch.md").read_text(encoding="utf-8")
        self.assertIn("## assistant", scratch)
        self.assertIn("## tool e1", scratch)
        self.assertIn("42", scratch)
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertNotIn("## tool", history)
        # No real assistant sections (the template docblock mentions the
        # format, so match a header at line start followed by newline).
        self.assertNotRegex(history, r"(?m)^## assistant t\d+\n")

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

    def test_next_turn_sees_scratch_via_assembly(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script() + [{"content": "again"}]),
                    Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        # Second turn's assembled messages include the prior episode's
        # tool trace (until T7 closes episodes).
        seen = []
        orig = loop.provider.chat

        def spy(**kw):
            seen.append(kw["messages"])
            return orig(**kw)

        loop.provider.chat = spy
        loop.run_turn(s, "what did you find?")
        tool_msgs = [m for m in seen[0] if m.get("role") == "tool"]
        self.assertTrue(any("42" in (m.get("content") or "")
                            for m in tool_msgs))

    def test_context_md_is_assembled_artifact(self):
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        artifact = (s.dir / "context.md").read_text(encoding="utf-8")
        # Assembled: sats first, then history, then scratch.
        self.assertIn("## sat facts", artifact)
        self.assertIn("## user t0001", artifact)
        self.assertIn("## tool e1", artifact)

    def test_auto_dedupe_rehomed_to_scratch(self):
        s = make_session()
        loop = Loop(MockProvider([]), Budget(100000, 80000))
        dup = "## tool e9\nsame result\n"
        (s.dir / "scratch.md").write_text(dup + dup, encoding="utf-8")
        loop._auto_dedupe(s)
        after = (s.dir / "scratch.md").read_text(encoding="utf-8")
        self.assertEqual(after.count("## tool e9"), 1)

    def test_parse_handles_scratch_tool_fence(self):
        # The assistant section in scratch carries the tool-calls fence;
        # parse links the tool result (no orphans).
        s = make_session()
        loop = Loop(MockProvider(tool_script()), Budget(100000, 80000))
        loop.run_turn(s, "do the thing")
        from harness.assembly import assemble
        msgs = parse_transcript(assemble(s.dir))
        roles = [m["role"] for m in msgs]
        self.assertIn("tool", roles)


if __name__ == "__main__":
    unittest.main()
