import os
import tempfile
import unittest
from pathlib import Path

from harness.context import Budget, render_tool, render_user
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

    def _bloated_session(self):
        # Valid transcript sections (bare text would be preamble -> 0 msgs).
        s = make_session()
        s.context.save("".join(render_user("x" * 200) for _ in range(100)))
        return s

    def test_hard_budget_triggers_prune_then_resumes(self):
        # hard budget above the system prompt's own size but below the
        # bloated transcript: prune turn runs, mock shrinks the file,
        # turn resumes.
        s = self._bloated_session()
        script = [
            # prune turn: shrink the file
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "write",
                 "arguments": {"path": str(s.context.path),
                               "content": render_user("fresh start")}}]},
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
        self.assertIn("fresh start", s.context.load())

    def test_prune_failure_raises_loudly(self):
        s = self._bloated_session()
        # mock never shrinks the file -> prune attempts exhaust
        script = [{"content": "PRUNED"}] * 40
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000))
        with self.assertRaises(BudgetExceeded):
            loop.run_turn(s, "hi")

    def test_model_edit_emits_context_diff(self):
        # A write targeting context.md produces a context-diff event with
        # the token delta; a write elsewhere does not.
        s = make_session()
        s.context.save(render_user("keep me") + render_tool("c9", "x" * 400))
        script = [
            {"content": None, "tool_calls": [
                {"id": "e1", "name": "write",
                 "arguments": {"path": str(s.context.path),
                               "content": render_user("keep me")}}]},
            {"content": "pruned"},
        ]
        events = []
        loop = Loop(MockProvider(script), Budget(100000, 80000),
                    on_event=lambda k, v: events.append((k, v)))
        self.assertEqual(loop.run_turn(s, "tidy up"), "pruned")
        diffs = [v for k, v in events if k == "context-diff"]
        self.assertEqual(len(diffs), 1)
        removed_headers = [r["header"] for r in diffs[0]["removed"]]
        self.assertIn("## tool c9", removed_headers)
        self.assertGreater(diffs[0]["recovered"], 0)

    def test_edit_elsewhere_emits_no_diff(self):
        s = make_session()
        script = [
            {"content": None, "tool_calls": [
                {"id": "e1", "name": "write",
                 "arguments": {"path": "notes.txt",
                               "content": "hello"}}]},
            {"content": "done"},
        ]
        events = []
        loop = Loop(MockProvider(script), Budget(100000, 80000),
                    on_event=lambda k, v: events.append(k))
        self.assertEqual(loop.run_turn(s, "write a file"), "done")
        self.assertNotIn("context-diff", events)

    def test_multi_turn_continuity_through_file(self):
        # Turn 1: model runs a tool and answers. Turn 2 (fresh Loop, same
        # session): the history must come from the file alone.
        s = make_session()
        script1 = [
            {"content": None, "tool_calls": [
                {"id": "c1", "name": "exec",
                 "arguments": {"command": "echo 42"}}]},
            {"content": "the answer is 42"},
        ]
        loop1 = Loop(MockProvider(script1), Budget(100000, 80000))
        self.assertEqual(loop1.run_turn(s, "what is the answer?"),
                         "the answer is 42")

        script2 = [{"content": "still 42"}]
        loop2 = Loop(MockProvider(script2), Budget(100000, 80000))
        self.assertEqual(loop2.run_turn(s, "and now?"), "still 42")
        seen = loop2.provider.calls[0]["messages"]
        roles = [m["role"] for m in seen]
        self.assertEqual(roles,
                         ["user", "assistant", "tool", "assistant", "user"])
        tool_msgs = [m for m in seen if m["role"] == "tool"]
        self.assertIn("42", tool_msgs[0]["content"])

    def test_model_prunes_file_mid_turn(self):
        # The model uses edit on context.md mid-turn; the next iteration
        # reads the pruned file.
        s = make_session()
        script = [
            {"content": "pruning", "tool_calls": [
                {"id": "e1", "name": "write",
                 "arguments": {"path": str(s.context.path),
                               "content": "## user\nkept\n"}}]},
            {"content": "done"},
        ]
        loop = Loop(MockProvider(script), Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "original question"), "done")
        # The model's rewrite won; the harness then appended the reply.
        text = s.context.load()
        self.assertIn("kept", text)
        self.assertNotIn("original question", text)
        self.assertIn("done", text)

    def test_prune_turn_uses_janitor_provider(self):
        # Separate janitor model handles the prune turn; the main model
        # only sees the resumed normal turn.
        s = self._bloated_session()
        main = MockProvider([{"content": "all good"}])
        janitor = MockProvider([
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "write",
                 "arguments": {"path": str(s.context.path),
                               "content": render_user("fresh start")}}]},
            {"content": "PRUNED"},
        ])
        loop = Loop(main, Budget(hard=2000, soft=1000),
                    prune_provider=janitor)
        self.assertEqual(loop.run_turn(s, "hi"), "all good")
        self.assertEqual(len(janitor.calls), 2)  # prune chats
        self.assertEqual(len(main.calls), 1)     # resumed normal chat
        self.assertEqual(janitor.calls[0]["tools"], ["write", "edit"])

    def test_prune_provider_defaults_to_main(self):
        main = MockProvider([])
        loop = Loop(main, Budget(100000, 80000))
        self.assertIs(loop.prune_provider, main)

    def test_prune_turn_restricts_tools(self):
        s = self._bloated_session()
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
