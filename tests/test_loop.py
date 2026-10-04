import os
import tempfile
import unittest
from pathlib import Path

from harness.context import Budget
from harness.loop import BudgetExceeded, Loop, Session
from harness.providers import MockProvider


def make_session(workdir: str | None = None) -> Session:
    d = tempfile.mkdtemp()
    return Session(id="t", dir=Path(d), workdir=workdir or d)


class TestLoop(unittest.TestCase):
    def test_simple_turn_no_tools(self):
        events = []
        loop = Loop(MockProvider([{"content": "done"}]),
                    Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append(k))
        s = make_session()
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "done")
        self.assertIn("assistant", events)

    def test_tool_roundtrip(self):
        script = [
            {"content": None, "tool_calls": [
                {"id": "c1", "name": "exec",
                 "arguments": {"command": "echo hello-tool"}}]},
            {"content": "saw it"},
        ]
        seen = []
        loop = Loop(MockProvider(script), Budget(100000, 80000),
                    on_event=lambda k, v: seen.append((k, v)))
        s = make_session()
        out = loop.run_turn(s, "run echo")
        self.assertEqual(out, "saw it")
        tool_calls = self.loop_tool_results(loop)
        self.assertTrue(any("hello-tool" in r for r in tool_calls))

    def loop_tool_results(self, loop):
        # second recorded call's messages contain the tool result
        msgs = loop.provider.calls[1]["messages"]
        return [m["content"] for m in msgs if m["role"] == "tool"]

    def test_unknown_tool_does_not_crash(self):
        script = [
            {"content": None, "tool_calls": [
                {"id": "c1", "name": "nope", "arguments": {}}]},
            {"content": "recovered"},
        ]
        loop = Loop(MockProvider(script), Budget(100000, 80000))
        s = make_session()
        out = loop.run_turn(s, "x")
        self.assertEqual(out, "recovered")
        results = self.loop_tool_results(loop)
        self.assertTrue(any("unknown tool" in r for r in results))

    def test_hard_budget_triggers_prune_then_resumes(self):
        # hard budget above the system prompt's own size but below the
        # bloated file: prune turn runs, mock shrinks the file, turn resumes.
        s = make_session()
        s.context.save("# big\n" + "x" * 20000)
        script = [
            # prune turn: shrink the file
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "write",
                 "arguments": {"path": str(s.context.path),
                               "content": "# tiny"}}]},
            {"content": "PRUNED"},
            # resumed normal turn
            {"content": "all good"},
        ]
        events = []
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000),
                    on_event=lambda k, v: events.append(k))
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "all good")
        self.assertIn("prune", events)
        self.assertEqual(s.context.load(), "# tiny")

    def test_prune_failure_raises_loudly(self):
        s = make_session()
        s.context.save("x" * 20000)
        # mock never shrinks the file -> prune attempts exhaust
        script = [{"content": "PRUNED"}] * 40
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000))
        with self.assertRaises(BudgetExceeded):
            loop.run_turn(s, "hi")

    def test_prune_turn_restricts_tools(self):
        s = make_session()
        s.context.save("x" * 20000)
        # exec is not allowed on a prune turn: 5 attempts x 2 responses,
        # file never shrinks -> BudgetExceeded, command never runs.
        script = [
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "exec",
                 "arguments": {"command": "echo evil-during-prune"}}]},
            {"content": "PRUNED"},
        ] * 6
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000))
        with self.assertRaises(BudgetExceeded):
            loop.run_turn(s, "hi")


class TestTools(unittest.TestCase):
    def test_exec_read_write_edit(self):
        from harness.tools import run_tool
        with tempfile.TemporaryDirectory() as d:
            r = run_tool("write", {"path": "a.txt", "content": "one"},
                         workdir=d)
            self.assertIn("Wrote", r)
            r = run_tool("read", {"path": "a.txt"}, workdir=d)
            self.assertIn("one", r)
            r = run_tool("edit", {"path": "a.txt", "old_text": "one",
                                 "new_text": "two"}, workdir=d)
            self.assertIn("Edited", r)
            r = run_tool("read", {"path": "a.txt"}, workdir=d)
            self.assertIn("two", r)
            r = run_tool("exec", {"command": "echo ok"}, workdir=d)
            self.assertIn("ok", r)
            self.assertIn("exit=0", r)

    def test_edit_missing_text_errors(self):
        from harness.tools import run_tool
        with tempfile.TemporaryDirectory() as d:
            run_tool("write", {"path": "a.txt", "content": "abc"}, workdir=d)
            r = run_tool("edit", {"path": "a.txt", "old_text": "zzz",
                                 "new_text": "q"}, workdir=d)
            self.assertIn("not found", r)


if __name__ == "__main__":
    unittest.main()
