import glob
import os
import tempfile
import unittest
from pathlib import Path

from harness.context import (Budget, parse_transcript, render_history_user,
                               render_tool, render_user)
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
        # Bloat history.md (the model-curated source): 100 stamped
        # sections. Valid sections, not bare text.
        s = make_session()
        big = "".join(render_history_user("x" * 200, i)
                      for i in range(1, 101))
        (s.dir / "history.md").write_text(big, encoding="utf-8")
        return s

    def _curate_history_script(self, s):
        # Janitor script: shrink history.md via a single edit.
        hist = s.dir / "history.md"
        big = hist.read_text(encoding="utf-8")
        return [
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "edit",
                 "arguments": {"path": str(hist),
                               "old_text": big,
                               "new_text": render_history_user(
                                   "fresh start", 1)}}]},
            {"content": "PRUNED"},
        ]

    def test_hard_budget_triggers_prune_then_resumes(self):
        # hard budget above the system prompt's own size but below the
        # bloated history: prune turn runs, mock curates the source,
        # turn resumes.
        s = self._bloated_session()
        script = self._curate_history_script(s) + [
            # resumed normal turn
            {"content": "all good"},
        ]
        events = []
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000),
                    on_event=lambda k, v: events.append(k))
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "all good")
        self.assertIn("prune", events)
        self.assertIn("fresh start",
                      (s.dir / "history.md").read_text(encoding="utf-8"))

    def test_prune_failure_raises_loudly(self):
        s = self._bloated_session()
        # mock never shrinks the sources -> prune attempts exhaust
        script = [{"content": "PRUNED"}] * 70
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000))
        with self.assertRaises(BudgetExceeded):
            loop.run_turn(s, "hi")

    def test_prune_bare_reply_nudges_then_succeeds(self):
        # The model says PRUNED without editing: the loop must nudge and
        # retry within the attempt, not burn the whole attempt on nothing.
        s = self._bloated_session()
        script = [{"content": "PRUNED"}] + self._curate_history_script(s) + [
            {"content": "all good"},
        ]
        events = []
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000),
                    on_event=lambda k, v: events.append(k))
        self.assertEqual(loop.run_turn(s, "hi"), "all good")
        self.assertIn("fresh start",
                      (s.dir / "history.md").read_text(encoding="utf-8"))

    def test_model_edit_emits_context_diff(self):
        # An edit targeting a model-editable source (history.md)
        # produces a context-diff event with the token delta; an edit
        # elsewhere does not.
        s = make_session()
        hist = s.dir / "history.md"
        hist.write_text("## user t0001\nkeep me\n"
                        "## assistant t0001\n" + "verbose\n" * 50,
                        encoding="utf-8")
        script = [
            {"content": None, "tool_calls": [
                {"id": "e1", "name": "edit",
                 "arguments": {"path": str(hist),
                               "old_text": "verbose\n" * 50,
                               "new_text": "summary\n"}}]},
            {"content": "pruned"},
        ]
        events = []
        loop = Loop(MockProvider(script), Budget(100000, 80000),
                    on_event=lambda k, v: events.append((k, v)))
        self.assertEqual(loop.run_turn(s, "tidy up"), "pruned")
        diffs = [v for k, v in events if k == "context-diff"]
        self.assertEqual(len(diffs), 1)
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
        # T7: turn 2 assembles sats (3 user) + history (2 users +
        # 1 episode pointer as user). The tool trace is archived, not
        # in hot context. The trailing harness note is filtered above.
        self.assertEqual(roles, ["user"] * 6)
        self.assertFalse(any(m["role"] == "tool" for m in seen))
        self.assertTrue(any("archive/" in (m.get("content") or "")
                            for m in seen))

    def test_model_prunes_file_mid_turn(self):
        # T5: mid-turn transcript rewrites are gate-denied -- the model
        # curates sources (history.md, sats), not the assembled
        # transcript. Source curation mid-turn lands with T6/T9.
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
        # The denial lives in the archived episode (tool results go to
        # scratch during the turn, archive at close).
        archived = "".join(
            p.read_text(encoding="utf-8")
            for p in (s.dir / "archive").glob("*.md"))
        self.assertIn("DENIED", archived)
        self.assertIn("done", archived)
        text = s.context.load()
        self.assertIn("original question", text)

    def test_prune_turn_uses_janitor_provider(self):
        # Separate janitor model handles the prune turn; the main model
        # only sees the resumed normal turn.
        s = self._bloated_session()
        main = MockProvider([{"content": "all good"}])
        janitor = MockProvider(self._curate_history_script(s))
        loop = Loop(main, Budget(hard=2000, soft=1000),
                    prune_provider=janitor)
        self.assertEqual(loop.run_turn(s, "hi"), "all good")
        self.assertEqual(len(janitor.calls), 2)  # prune chats
        self.assertEqual(len(main.calls), 1)     # resumed normal chat
        # Curation turns are edit-only now.
        self.assertEqual(janitor.calls[0]["tools"], ["edit"])

    def test_model_edit_backs_up_context(self):
        # Model edits must snapshot too: every edit reversible via a
        # pre-edit backup plus a mechanical diff.
        # NOTE (T4): the edit targets a plain file, not the live
        # transcript -- under the exactly-once contract a transcript
        # self-edit is ambiguous by construction (old_text matches its
        # own echo in the tool-calls fence), so curation moves to
        # source files (see T5). _execute_tool is driven directly to
        # keep the transcript echo out of the picture.
        s = make_session()
        target = s.dir / "history.md"
        target.write_text("## user t0001\nkeep me\n", encoding="utf-8")
        diffs = []
        loop = Loop(MockProvider([]), Budget(100000, 80000),
                    on_event=lambda k, v: diffs.append(v)
                    if k == "context-diff" else None)
        result = loop._execute_tool(
            s, {"id": "e1", "name": "edit",
                "arguments": {"path": str(target),
                              "old_text": "keep me\n",
                              "new_text": "keep me (summarized)\n"}})
        self.assertIn("1 occurrence replaced", result)
        # T5: source backups carry the source file's stem.
        baks = glob.glob(str(s.dir / "history.pre-edit-*.bak"))
        self.assertEqual(len(baks), 1)
        bak = open(baks[0], encoding="utf-8").read()
        self.assertIn("keep me", bak)
        # The backup is taken pre-edit; after the edit the file changes.
        self.assertNotEqual(bak, target.read_text(encoding="utf-8"))
        self.assertIn("(summarized)", target.read_text(encoding="utf-8"))
        self.assertEqual(len(diffs), 1)

    def test_transcript_self_edit_fails_loudly(self):
        # T5: the live transcript is harness-owned -- model edits are
        # denied by the source gates before exactly-once even runs.
        # (Under T4's contract alone the edit would also fail: the
        # tool-calls fence echoes old_text, so the match count is never
        # 1.) The denial must be loud and leave the file untouched.
        s = make_session()
        s.context.save(render_user("keep me"))
        before = s.context.load()
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
        # The denial lives in the archived episode.
        archived = "".join(
            p.read_text(encoding="utf-8")
            for p in (s.dir / "archive").glob("*.md"))
        self.assertIn("DENIED", archived)
        self.assertIn("harness-owned", archived)
        # No backup: nothing changed.
        self.assertEqual(len(glob.glob(str(s.dir / "*.pre-edit-*.bak"))), 0)
        # history.md still holds the original user message, stamped.
        history = (s.dir / "history.md").read_text(encoding="utf-8")
        self.assertIn("## user t0001", history)
        self.assertIn("hi", history)

    def test_noop_and_other_files_no_backup(self):
        # Edits elsewhere never mint a .bak, and identical edit is no-op.
        s = make_session()
        script = [
            {"content": None, "tool_calls": [
                {"id": "w2", "name": "write",
                 "arguments": {"path": "elsewhere.txt",
                               "content": "unrelated"}}]},
            {"content": "done"},
        ]
        loop = Loop(MockProvider(script), Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "hi"), "done")
        self.assertEqual(glob.glob(str(s.dir / "*.pre-*.bak")), [])
        self.assertEqual((Path(s.workdir) / "elsewhere.txt")
                         .read_text(encoding="utf-8"), "unrelated")

    def test_prune_provider_defaults_to_main(self):
        main = MockProvider([])
        loop = Loop(main, Budget(100000, 80000))
        self.assertIs(loop.prune_provider, main)

    def test_prune_backs_up_context_first(self):
        # T6: the pre-prune backup covers scratch.md (the working trace
        # the deterministic ladder operates on).
        import glob
        s = self._bloated_session()
        scratch_p = s.dir / "scratch.md"
        scratch_p.write_text("## assistant\nprior episode notes\n",
                             encoding="utf-8")
        script = self._curate_history_script(s) + [
            {"content": "all good"},
        ]
        loop = Loop(MockProvider(script), Budget(hard=2000, soft=1000))
        self.assertEqual(loop.run_turn(s, "hi"), "all good")
        baks = glob.glob(str(s.dir / "scratch.pre-prune-*.bak"))
        self.assertEqual(len(baks), 1)
        self.assertEqual(open(baks[0], encoding="utf-8").read(),
                         "## assistant\nprior episode notes\n")

    def test_prune_gate_uses_full_tool_measure(self):
        # The assembly can be UNDER by the prune-tools measure while OVER
        # by the full-tools measure (4 extra schemas). The gate must use
        # the full measure, or prune_turn returns True instantly, the main
        # check stays over, and the loop burns all MAX_STEPS on OVER lines.
        from harness.assembly import assemble, load_prompt
        s = make_session()
        prov = MockProvider([])
        loop = Loop(prov, Budget(hard=100000, soft=80000))
        system = load_prompt(s.dir, ctx_path="x", hard=100000, soft=80000)
        big = "".join(render_history_user("x" * 200, i)
                      for i in range(1, 13))
        (s.dir / "history.md").write_text(big, encoding="utf-8")
        messages = parse_transcript(assemble(s.dir))
        full = loop._measure(prov, system, messages, loop._tools)["total"]
        small = loop._measure(prov, system, messages,
                              loop._prune_tools)["total"]
        self.assertGreater(full - small, 200)  # the gap is real
        hard = (full + small) // 2

        hist = s.dir / "history.md"
        main = MockProvider([{"content": "done"}])
        janitor = MockProvider([
            {"content": None, "tool_calls": [
                {"id": "p1", "name": "edit",
                 "arguments": {"path": str(hist),
                               "old_text": big,
                               "new_text": render_history_user(
                                   "fresh start", 1)}}]},
            {"content": "PRUNED"},
        ])
        loop2 = Loop(main, Budget(hard=hard, soft=hard - 500),
                     prune_provider=janitor)
        self.assertEqual(loop2.run_turn(s, "hi"), "done")
        # Old code: gate passed instantly, janitor never called.
        self.assertGreaterEqual(len(janitor.calls), 1)
        self.assertIn("fresh start",
                      hist.read_text(encoding="utf-8"))

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
