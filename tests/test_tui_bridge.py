import tempfile
import unittest
from pathlib import Path

from harness.loop import Loop, Session
from harness.providers import MockProvider
from harness.tui import has_tui
from harness.tui.approvals import TUIApprover
from harness.tui.bridge import TuiBridge, format_event, run_turn_in_thread
from harness.approvals import Policy
from harness.context import Budget


def make_session(workdir=None):
    d = tempfile.mkdtemp()
    return Session(id="t", dir=Path(d), workdir=workdir or d)


class TestFormatEvent(unittest.TestCase):
    def test_assistant(self):
        # Assistant label is green bold, then newline, then text
        self.assertEqual(format_event("assistant", "hi"),
                         "\x1b[1m\x1b[32massistant\x1b[0m\nhi")

    def test_assistant_custom_name(self):
        # Configurable display name for the assistant role.
        line = format_event("assistant", "hi", assistant_name="Data")
        self.assertIn("Data", line)
        self.assertNotIn("assistant\n", line.replace("Data", ""))

    def test_tool(self):
        line = format_event("tool", {"name": "exec",
                                     "args": {"command": "ls"},
                                     "result": "x"})
        # Tool header is dimmed with wrench icon
        self.assertEqual(line, "\x1b[2m🔧 exec ls\x1b[0m")

    def test_tool_denied(self):
        line = format_event("tool", {"name": "exec",
                                     "args": {"command": "rm -rf /"},
                                     "result": "DENIED", "denied": True})
        self.assertIn("[denied]", line)

    def test_budget(self):
        line = format_event("budget", {"status": "warn", "tokens": 1000})
        self.assertIn("WARN", line)

    def test_prune(self):
        self.assertIn("prune-only turn 2",
                      format_event("prune", {"attempt": 2, "tokens": 9}))

    def test_context_diff(self):
        line = format_event("context-diff", {"recovered": 10,
                                             "removed": [], "added": []})
        self.assertIn("+10", line)

    def test_request(self):
        line = format_event("request", {"tokens_est": 100, "hard": 200,
                                        "breakdown": {"system": 1,
                                                      "transcript": 2,
                                                      "tools": 3}})
        self.assertIn("[context 100", line)

    def test_request_without_estimate_quiet(self):
        self.assertIsNone(format_event("request", {"phase": "x"}))

    def test_response_content(self):
        self.assertEqual(format_event("response", {"content": "done",
                                                   "tool_calls": None}),
                         "done")

    def test_error(self):
        self.assertIn("boom", format_event("error", {"message": "boom"}))

    def test_approval_wait(self):
        line = format_event("approval-wait",
                            {"tool": "exec", "args": {"command": "x"},
                             "reason": "r", "timeout": 120})
        self.assertIn("approval needed", line)

    def test_approval_result(self):
        line = format_event("approval-result",
                            {"decision": "deny", "scope": "never"})
        self.assertIn("deny", line)

    def test_usage_quiet(self):
        self.assertIsNone(format_event("usage", {}))

    def test_unknown_quiet(self):
        self.assertIsNone(format_event("nope", {}))


class TestBridge(unittest.TestCase):
    def test_queue_ordering(self):
        b = TuiBridge()
        b("assistant", "one")
        b("tool", {"name": "exec", "args": {"command": "ls"}})
        got = b.drain()
        self.assertEqual([k for k, _, _ in got], ["assistant", "tool"])
        self.assertEqual(got[0][2], "\x1b[1m\x1b[32massistant\x1b[0m\none")
        self.assertTrue(got[1][2].startswith("\x1b[2m🔧 exec"))
        self.assertEqual(b.drain(), [])

    def test_full_turn_flows_through(self):
        s = make_session()
        b = TuiBridge()
        loop = Loop(MockProvider([{"content": "hello"}]),
                    Budget(hard=100000, soft=80000), on_event=b)
        t = run_turn_in_thread(loop, s, "hi")
        t.join(timeout=30)
        self.assertFalse(t.is_alive())
        kinds = [k for k, _, _ in b.drain()]
        self.assertIn("assistant", kinds)
        self.assertIn("request", kinds)

    def test_run_turn_error_callback(self):
        class Boom:
            def run_turn(self, session, text):
                raise RuntimeError("bang")
        errs = []
        t = run_turn_in_thread(Boom(), None, "x", on_error=errs.append)
        t.join(timeout=10)
        self.assertEqual(len(errs), 1)


class TestTUIApprover(unittest.TestCase):
    def policy_for(self, s):
        return Policy(s.workdir, s.dir, s.context.path)

    def test_ask_denies_without_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            a = TUIApprover(d, approval_timeout=120)
            s = Session(id="x", dir=Path(d), workdir=d)
            ok, msg = a.resolve(self.policy_for(s), "exec",
                                {"command": "git push origin main"})
            self.assertFalse(ok)
            self.assertIn("DENIED", msg)

    def test_no_stdin_touched(self):
        from harness.approvals import StdinPump
        with tempfile.TemporaryDirectory() as d:
            pump = StdinPump(start_thread=False)
            pump._q.put("s")  # a waiting line must stay unread
            a = TUIApprover(d, approval_timeout=120)
            self.assertIsNone(a._read_answer())
            s = Session(id="x", dir=Path(d), workdir=d)
            ok, _ = a.resolve(self.policy_for(s), "exec",
                               {"command": "git push origin main"})
            self.assertFalse(ok)
            self.assertEqual(pump._q.qsize(), 1)

    def test_decide_hook_approves(self):
        with tempfile.TemporaryDirectory() as d:
            a = TUIApprover(d, decide=lambda info: "turn")
            s = Session(id="x", dir=Path(d), workdir=d)
            ok, _ = a.resolve(self.policy_for(s), "exec",
                               {"command": "git push origin main"})
            self.assertTrue(ok)

    def test_auto_approve_still_works(self):
        with tempfile.TemporaryDirectory() as d:
            a = TUIApprover(d, auto_approve=True)
            s = Session(id="x", dir=Path(d), workdir=d)
            ok, _ = a.resolve(self.policy_for(s), "exec",
                               {"command": "echo hi"})
            self.assertTrue(ok)

    def test_has_tui_bool(self):
        self.assertIsInstance(has_tui(), bool)


if __name__ == "__main__":
    unittest.main()
