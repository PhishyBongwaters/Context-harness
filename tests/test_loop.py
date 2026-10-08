import glob
import os
import tempfile
import unittest
from pathlib import Path

from harness.context import (Budget, parse_transcript, render_tool,
                               render_user)
from harness.loop import BudgetExceeded, Loop, Session, _estimate
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
        script = [{"content": "PRUNED"}] * 70
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000))
        with self.assertRaises(BudgetExceeded):
            loop.run_turn(s, "hi")

    def test_prune_bare_reply_nudges_then_succeeds(self):
        # The model says PRUNED without editing: the loop must nudge and
        # retry within the attempt, not burn the whole attempt on nothing.
        s = self._bloated_session()
        script = [
            {"content": "PRUNED"},
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "write",
                 "arguments": {"path": str(s.context.path),
                               "content": render_user("fresh start")}}]},
            {"content": "PRUNED"},
            {"content": "all good"},
        ]
        events = []
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000),
                    on_event=lambda k, v: events.append(k))
        self.assertEqual(loop.run_turn(s, "hi"), "all good")
        self.assertIn("fresh start", s.context.load())

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
        seen = [m for m in loop2.provider.calls[0]["messages"]
                if "[harness note:" not in (m.get("content") or "")]
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

    def test_model_edit_backs_up_context(self):
        # Mid-turn curation (model write/edit on context.md during a
        # NORMAL turn) must snapshot too: every model edit reversible.
        s = make_session()
        s.context.save(render_user("keep me"))
        script = [
            {"content": None, "tool_calls": [
                {"id": "e1", "name": "edit",
                 "arguments": {"path": str(s.context.path),
                               "old_text": "keep me",
                               "new_text": "keep me (summarized)"}}]},
            {"content": "curated"},
        ]
        loop = Loop(MockProvider(script), Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "hi"), "curated")
        baks = glob.glob(str(s.dir / "context.pre-edit-*.bak"))
        self.assertEqual(len(baks), 1)
        bak = open(baks[0], encoding="utf-8").read()
        self.assertIn("keep me", bak)
        # The backup is taken pre-edit; after the edit the file changes.
        self.assertNotEqual(bak, s.context.load())
        self.assertIn("(summarized)", s.context.load())

    def test_noop_and_other_files_no_backup(self):
        # Edits elsewhere never mint a .bak, and identical edit is no-op.
        s = make_session()
        s.context.save(render_user("stable"))
        script = [
            {"content": None, "tool_calls": [
                {"id": "w2", "name": "write",
                 "arguments": {"path": "elsewhere.txt",
                               "content": "unrelated"}}]},
            {"content": "done"},
        ]
        loop = Loop(MockProvider(script), Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "hi"), "done")
        self.assertEqual(glob.glob(str(s.dir / "context.pre-*.bak")), [])
        self.assertIn("stable", s.context.load())

    def test_prune_provider_defaults_to_main(self):
        main = MockProvider([])
        loop = Loop(main, Budget(100000, 80000))
        self.assertIs(loop.prune_provider, main)

    def test_prune_backs_up_context_first(self):
        import glob
        s = self._bloated_session()
        script = [
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "write",
                 "arguments": {"path": str(s.context.path),
                               "content": render_user("fresh start")}}]},
            {"content": "PRUNED"},
            {"content": "all good"},
        ]
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000))
        before = s.context.load()
        self.assertEqual(loop.run_turn(s, "hi"), "all good")
        baks = glob.glob(str(s.dir / "context.pre-prune-*.bak"))
        self.assertEqual(len(baks), 1)
        # backup is taken at prune time, i.e. after the user msg appends
        self.assertEqual(open(baks[0], encoding="utf-8").read(),
                         before + "\n" + render_user("hi"))

    def test_prune_gate_uses_full_tool_measure(self):
        # The transcript can be UNDER by the prune-tools measure while OVER
        # by the full-tools measure (3 extra schemas). The gate must use
        # the full measure, or prune_turn returns True instantly, the main
        # check stays over, and the loop burns all MAX_STEPS on OVER lines.
        from harness.loop import SYSTEM_PROMPT
        s = make_session()
        prov = MockProvider([])
        loop = Loop(prov, Budget(hard=100000, soft=80000))
        system = SYSTEM_PROMPT.format(ctx_path="x", hard=100000, soft=80000)
        s.context.save("".join(render_user("x" * 200) for _ in range(12)))
        messages = parse_transcript(s.context.load())
        full = loop._measure(prov, system, messages, loop._tools)["total"]
        small = loop._measure(prov, system, messages,
                              loop._prune_tools)["total"]
        self.assertGreater(full - small, 200)  # the gap is real
        hard = (full + small) // 2

        main = MockProvider([{"content": "done"}])
        janitor = MockProvider([
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "write",
                 "arguments": {"path": str(s.context.path),
                               "content": render_user("fresh start")}}]},
            {"content": "PRUNED"},
        ])
        loop2 = Loop(main, Budget(hard=hard, soft=hard - 500),
                     prune_provider=janitor)
        self.assertEqual(loop2.run_turn(s, "hi"), "done")
        # Old code: gate passed instantly, janitor never called.
        self.assertGreaterEqual(len(janitor.calls), 1)
        self.assertIn("fresh start", s.context.load())

    def test_estimate_counts_tool_schemas(self):
        tools = [{"name": "exec", "description": "d" * 400,
                  "parameters": {"type": "object"}}]
        msgs = [{"role": "user", "content": "hi"}]
        self.assertGreater(_estimate("sys", msgs, tools),
                           _estimate("sys", msgs))

    def test_prune_turn_restricts_tools(self):
        s = self._bloated_session()
        # exec is not allowed on a prune turn: 5 attempts x 12 responses,
        # file never shrinks -> BudgetExceeded, command never runs.
        script = [
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "exec",
                 "arguments": {"command": "echo evil-during-prune"}}]},
            {"content": "PRUNED"},
        ] * 40
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

    def test_tokens_counts_file_and_text(self):
        from harness.tools import run_tool
        with tempfile.TemporaryDirectory() as d:
            run_tool("write", {"path": "a.txt", "content": "hello world"},
                     workdir=d)
            r = run_tool("tokens", {"path": "a.txt"}, workdir=d)
            self.assertIn("tokens [tiktoken/cl100k_base]", r)
            r = run_tool("tokens", {"text": "hello world"}, workdir=d)
            self.assertIn("2 tokens", r)
            r = run_tool("tokens", {}, workdir=d)
            self.assertIn("ERROR", r)


class BlockingProvider:
    """Provider stub that blocks inside chat() until released."""

    name = "blocking"

    def __init__(self):
        import threading
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def chat(self, *, system, messages, tools):
        self.calls += 1
        self.entered.set()
        self.release.wait(10)
        return {"content": "hi", "tool_calls": [],
                "usage": {"input": 0, "output": 0}}


class TestInterrupt(unittest.TestCase):
    def test_stop_during_blocking_call_aborts_without_storing(self):
        import threading
        events = []
        prov = BlockingProvider()
        loop = Loop(prov, Budget(100000, 80000),
                    on_event=lambda k, v: events.append(k))
        s = make_session()
        out = []
        t = threading.Thread(
            target=lambda: out.append(loop.run_turn(s, "hi")))
        t.start()
        self.assertTrue(prov.entered.wait(5))
        loop.request_stop()
        prov.release.set()
        t.join(5)
        self.assertFalse(t.is_alive())
        # Aborted, not stored: no assistant reply, interrupted emitted.
        self.assertEqual(out, [""])
        self.assertIn("interrupted", events)
        self.assertNotIn("assistant", events)

    def test_completed_turn_consumes_stop_flag(self):
        import threading
        prov = BlockingProvider()
        loop = Loop(prov, Budget(100000, 80000),
                    on_event=lambda k, v: None)
        s = make_session()
        out = []
        t = threading.Thread(
            target=lambda: out.append(loop.run_turn(s, "hi")))
        t.start()
        self.assertTrue(prov.entered.wait(5))
        loop.request_stop()
        prov.release.set()
        t.join(5)
        # Interrupted turn cleared the flag: the next turn runs clean.
        self.assertFalse(loop._stop_event.is_set())
        prov2 = BlockingProvider()
        prov2.release.set()
        loop.provider = prov2
        self.assertEqual(loop.run_turn(s, "again"), "hi")


if __name__ == "__main__":
    unittest.main()
