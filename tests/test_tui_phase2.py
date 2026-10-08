"""Phase 2 TUI tests: budget bar, status, dedupe, modal approver.

Stdlib-only except the Textual screen test, which skips cleanly when
the extra is missing.
"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from harness.approvals import Policy
from harness.context import Budget
from harness.loop import Session
from harness.tui import has_tui
from harness.tui.approvals import (TUIApprover, approval_brief,
                                   call_with_timeout, normalize_answer)
from harness.tui.bridge import (TranscriptDedupe, budget_bar_status,
                                budget_bar_text, dedupe_entries,
                                format_event, format_status)


def make_request(tokens=1000, hard=8000, soft=6000, status=None,
                 phase="main"):
    data = {"phase": "main" if phase == "main" else phase, "step": 0,
            "tokens_est": tokens, "hard": hard, "soft": soft,
            "breakdown": {"system": 100, "transcript": 700,
                          "tools": 200, "total": tokens},
            "usage_total": {"input": 10, "output": 5, "estimated": 0}}
    if status is not None:
        data["status"] = status
    return data


class TestBudgetBar(unittest.TestCase):
    def test_numbers_match_cli_line(self):
        data = make_request()
        cli = format_event("request", data)
        bar = budget_bar_text(data)
        for v in (1000, 8000, 100, 700, 200, 10, 5):
            self.assertIn(f"{v:,}", cli)
            self.assertIn(f"{v:,}", bar)

    def test_no_estimate_is_none(self):
        self.assertIsNone(budget_bar_text({"phase": "x"}))
        self.assertIsNone(budget_bar_text(None))

    def test_readout_prefers_window_status_stays_enforcement(self):
        data = make_request(tokens=1180)
        data["window"] = 135168
        cli = format_event("request", data)
        bar = budget_bar_text(data)
        for text in (cli, bar):
            self.assertIn("1,180", text)
            self.assertIn("135,168", text)
            self.assertNotIn("8,000", text)
        # Status still keys off hard/soft, not the window.
        self.assertEqual(budget_bar_status(data), "ok")
        data["tokens_est"] = 70000
        self.assertEqual(budget_bar_status(data), "over")

    def test_readout_falls_back_to_hard_without_window(self):
        data = make_request(tokens=1000)
        bar = budget_bar_text(data)
        self.assertIn("8,000", bar)
        self.assertNotIn("None", bar)

    def test_status_matches_budget(self):
        hard, soft = 8000, 6000
        b = Budget(hard=hard, soft=soft)
        for tokens in (100, 5999, 6000, 7999, 8000):
            data = make_request(tokens=tokens)
            data.pop("status", None)
            self.assertEqual(budget_bar_status(data), b.status(tokens))

    def test_status_honours_loop_status(self):
        self.assertEqual(budget_bar_status(make_request(status="warn")),
                         "warn")
        self.assertEqual(budget_bar_status(make_request(status="over")),
                         "over")


class TestStatusLine(unittest.TestCase):
    def test_thinking(self):
        result = format_status("main", 3)
        self.assertIn("thinking 3s", result)
        # Animation blocks present
        self.assertTrue(any(c in result for c in "▁▂▃▄▅▆▇█"))

    def test_prune_label_distinct(self):
        result = format_status("prune", 7)
        self.assertIn("pruning 7s", result)

    def test_unknown_phase_thinks(self):
        result = format_status(None, 0)
        self.assertIn("thinking 0s", result)


class TestDedupe(unittest.TestCase):
    def resp(self, content, phase="main", tools=None):
        return ("response", {"phase": phase, "content": content,
                             "tool_calls": tools},
                content)

    def test_main_dupe_collapses(self):
        entries = [self.resp("hello"),
                   ("usage", {"input": 1}, None),
                   ("assistant", "hello", "hello")]
        got = dedupe_entries(entries)
        self.assertEqual([l for _, _, l in got if l is not None],
                         ["hello"])

    def test_prune_response_kept(self):
        entries = [self.resp("PRUNED", phase="prune")]
        self.assertEqual(dedupe_entries(entries), entries)

    def test_different_text_kept(self):
        entries = [self.resp("raw ## header"),
                   ("assistant", "cleaned", "cleaned")]
        self.assertEqual(len(dedupe_entries(entries)), 2)

    def test_tool_calls_note_survives(self):
        tc = [{"id": "1", "name": "exec", "arguments": {}}]
        entries = [self.resp("run it", tools=tc),
                   ("assistant", "run it", "run it")]
        got = [l for _, _, l in dedupe_entries(entries)]
        self.assertEqual(got, ["[tool calls: exec]", "run it"])

    def test_other_lines_single(self):
        entries = [
            ("tool", {"name": "exec", "args": {"command": "ls"}},
             "$ exec ls"),
            ("budget", {"status": "warn", "tokens": 1}, "[WARN budget]"),
            ("prune", {"attempt": 1, "tokens": 2}, "[prune]"),
            ("context-diff", {"recovered": 1}, "[context diff]"),
        ]
        self.assertEqual(dedupe_entries(entries), entries)

    def test_stateful_across_polls(self):
        d = TranscriptDedupe()
        self.assertEqual(d.feed([self.resp("hello")]), [])
        self.assertEqual(d.feed([("usage", {}, None)]), [])
        self.assertEqual(d.feed([("assistant", "hello", "hello")]),
                         [("assistant", "hello")])
        self.assertEqual(d.flush(), [])

    def test_realistic_formatted_lines_collapse(self):
        # Regression: dedupe compared FORMATTED lines, but the assistant
        # line always carries a label prefix, so production dupes never
        # collapsed and every reply showed twice. Build entries the way
        # the bridge does (format_event output) and require one line.
        def entry(kind, data):
            return (kind, data, format_event(kind, data))

        text = "hello there"
        entries = [
            entry("response", {"phase": "main", "content": text,
                               "tool_calls": []}),
            entry("usage", {"input": 1, "output": 1}),
            entry("assistant", text),
        ]
        got = [l for l in dedupe_entries(entries) if l[2] is not None]
        self.assertEqual(len(got), 1)
        self.assertIn("hello there", got[0][2])

        # stateful path too
        d = TranscriptDedupe()
        self.assertEqual(d.feed(entries[:1]), [])
        self.assertEqual(d.feed(entries[1:2]), [])
        pairs = d.feed(entries[2:])
        self.assertEqual(len(pairs), 1)
        self.assertEqual(pairs[0][0], "assistant")
        self.assertIn("hello there", pairs[0][1])

    def test_stateful_no_dupe_flushes(self):
        d = TranscriptDedupe()
        d.feed([self.resp("hello")])
        got = d.feed([("tool", {"name": "exec", "args": {}},
                       "$ exec x")])
        self.assertEqual(got, [("response", "hello"), ("tool", "$ exec x")])


class TestNormalizeAnswer(unittest.TestCase):
    def test_table(self):
        self.assertEqual(normalize_answer("turn"), "turn")
        self.assertEqual(normalize_answer("session"), "session")
        self.assertEqual(normalize_answer("deny"), "deny")
        self.assertEqual(normalize_answer("a"), "turn")
        self.assertEqual(normalize_answer("s"), "session")
        self.assertEqual(normalize_answer("d"), "deny")
        self.assertEqual(normalize_answer("esc"), "deny")
        self.assertEqual(normalize_answer(""), "deny")
        self.assertEqual(normalize_answer(None), "deny")
        self.assertEqual(normalize_answer("bogus"), "deny")

    def test_brief_truncates(self):
        self.assertEqual(approval_brief({"command": "ls"}), "ls")
        self.assertTrue(approval_brief({"command": "x" * 300})
                        .endswith("..."))

    def test_call_with_timeout(self):
        self.assertEqual(call_with_timeout(lambda: "turn", 5), "turn")
        never = threading.Event()
        self.assertIsNone(call_with_timeout(never.wait, 0.1))
        def boom():
            raise RuntimeError("x")
        self.assertIsNone(call_with_timeout(boom, 5))


class TestTUIApproverPhase2(unittest.TestCase):
    def policy_for(self, s):
        return Policy(s.workdir, s.dir, s.context.path)

    def ask(self, decide, **kw):
        d = tempfile.mkdtemp()
        kw.setdefault("approval_timeout", 120)
        a = TUIApprover(d, decide=decide, **kw)
        s = Session(id="x", dir=Path(d), workdir=d)
        return a.resolve(self.policy_for(s), "exec",
                         {"command": "git push origin main"})

    def test_decide_turn_and_session(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(id="x", dir=Path(d), workdir=d)
            pol = self.policy_for(s)
            a = TUIApprover(d, decide=lambda info: "turn")
            ok, _ = a.resolve(pol, "exec",
                              {"command": "git push origin main"})
            self.assertTrue(ok)
            self.assertEqual(len(a.turn_keys), 1)
            a.new_turn()
            self.assertEqual(a.turn_keys, set())
            b = TUIApprover(d, decide=lambda info: "s")
            ok, _ = b.resolve(pol, "exec",
                              {"command": "git push origin main"})
            self.assertTrue(ok)
            self.assertTrue((Path(d) / "approvals.json").is_file())

    def test_decide_none_and_error_deny(self):
        ok, msg = self.ask(lambda info: None)
        self.assertFalse(ok)
        self.assertIn("DENIED", msg)
        ok, _ = self.ask(lambda info: 1 / 0)
        self.assertFalse(ok)

    def test_countdown_default_deny(self):
        never = threading.Event()
        with tempfile.TemporaryDirectory() as d:
            s = Session(id="x", dir=Path(d), workdir=d)
            a = TUIApprover(d, decide=never.wait, approval_timeout=1)
            t0 = time.monotonic()
            ok, msg = a.resolve(self.policy_for(s), "exec",
                                {"command": "git push origin main"})
            self.assertFalse(ok)
            self.assertIn("DENIED", msg)
            self.assertLess(time.monotonic() - t0, 10)

    def test_pending_visible_during_ask(self):
        seen = {}
        holder = {}
        gate = threading.Event()

        def decide(info):
            seen.update(holder["a"].pending or {})
            gate.wait(5)
            return "deny"

        with tempfile.TemporaryDirectory() as d:
            s = Session(id="x", dir=Path(d), workdir=d)
            holder["a"] = TUIApprover(d, decide=decide)
            holder["a"].resolve(self.policy_for(s), "exec",
                                {"command": "git push origin main"})
            self.assertEqual(seen.get("tool"), "exec")

    def test_yes_parity_auto_approve(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(id="x", dir=Path(d), workdir=d)
            pol = self.policy_for(s)
            a = TUIApprover(d, auto_approve=True)  # no modal wired
            ok, _ = a.resolve(pol, "exec",
                              {"command": "git push origin main"})
            self.assertTrue(ok)

    def test_yes_still_denies_denylist(self):
        with tempfile.TemporaryDirectory() as d:
            s = Session(id="x", dir=Path(d), workdir=d)
            a = TUIApprover(d, auto_approve=True)
            ok, msg = a.resolve(self.policy_for(s), "exec",
                                {"command": "rm -rf /"})
            self.assertFalse(ok)
            self.assertIn("DENIED", msg)

    def test_yes_never_opens_modal(self):
        def decide(info):
            raise AssertionError("modal must not open with --yes")

        with tempfile.TemporaryDirectory() as d:
            s = Session(id="x", dir=Path(d), workdir=d)
            a = TUIApprover(d, auto_approve=True, decide=decide)
            ok, _ = a.resolve(self.policy_for(s), "exec",
                              {"command": "git push origin main"})
            self.assertTrue(ok)

    def test_modal_path_ignores_stdin(self):
        from harness.approvals import StdinPump
        pump = StdinPump(start_thread=False)
        pump._q.put("s")  # must stay unread
        with tempfile.TemporaryDirectory() as d:
            s = Session(id="x", dir=Path(d), workdir=d)
            a = TUIApprover(d, decide=lambda info: "deny")
            a.pump = pump
            ok, _ = a.resolve(self.policy_for(s), "exec",
                              {"command": "git push origin main"})
            self.assertFalse(ok)
            self.assertEqual(pump._q.qsize(), 1)


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestApprovalScreen(unittest.TestCase):
    def test_bindings_and_countdown(self):
        from harness.tui.app import ApprovalScreen
        box = {"event": threading.Event(), "answer": None}
        info = {"tool": "exec", "args": {"command": "rm -rf x"},
                "reason": "mutating", "timeout": 120}
        scr = ApprovalScreen(info, box)
        keys = {b[0] for b in scr.BINDINGS}
        self.assertTrue({"a", "s", "d", "escape"} <= keys)
        self.assertEqual(scr._left, 120)
        self.assertIsNone(box["answer"])  # modal untouched: no answer yet
        self.assertEqual(normalize_answer(None), "deny")  # default-deny


if __name__ == "__main__":
    unittest.main()
