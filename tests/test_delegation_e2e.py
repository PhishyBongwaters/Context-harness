"""End-to-end: delegate -> thread runs -> result.md -> parent reads.

Uses a mocked provider (no network). Slow-ish (thread + full turn
machinery); skipped if threading is unavailable.
"""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from harness.context import Budget
from harness.loop import Loop, Session
from harness.session import init_layout
from harness.tools import source_gate


class _FakeProvider:
    """Minimal provider: no MagicMock attributes leaking into JSON."""
    def __init__(self, reply="subagent findings here"):
        self._reply = reply
        self.model = "fake-model"

    def chat(self, *, system, messages, tools=None, **kw):
        return {"content": self._reply, "tool_calls": []}


def _mock_provider(reply="subagent findings here"):
    return _FakeProvider(reply)


class TestDelegationE2E(unittest.TestCase):
    def test_full_cycle(self):
        d = Path(tempfile.mkdtemp())
        init_layout(d, "PROMPT")
        sess = Session(id="e2e", dir=d, workdir=str(d))
        loop = Loop(provider=_mock_provider(),
                    budget=Budget(hard=100_000, soft=80_000),
                    approver=None, usage_tracker=None)

        # 1. delegate
        out = json.loads(loop._delegate(
            sess, {"task": "Summarize the repo layout"}))
        self.assertEqual(out["status"], "running")
        result_path = Path(out["result_path"])
        self.assertTrue(str(result_path).startswith(str(d)))

        # 2. wait (blocking, generous timeout for CI slowness)
        out = json.loads(loop._wait_subagent({"timeout": 60}))
        self.assertEqual(out["status"], "completed",
                         f"wait returned {out}")
        self.assertEqual(out["result_path"], str(result_path))

        # 3. result.md exists with the findings
        text = result_path.read_text(encoding="utf-8")
        self.assertIn("- status: completed", text)
        self.assertIn("subagent findings here", text)
        self.assertIn("## Findings", text)

        # 4. parent can read it through the source gate
        denial = source_gate(sess.dir, "read", str(result_path),
                             str(d))
        self.assertIsNone(denial,
                          f"parent blocked from reading result: {denial}")

        # 5. slot cleared for the next delegation
        out2 = json.loads(loop._delegate(
            sess, {"task": "Second task"}))
        self.assertEqual(out2["status"], "running")
        # clean up the second thread
        loop._cancel_subagent()
        loop._subagent["thread"].join(timeout=30)

    def test_cancel(self):
        d = Path(tempfile.mkdtemp())
        init_layout(d, "PROMPT")
        sess = Session(id="e2e-cancel", dir=d, workdir=str(d))

        class _SlowProvider(_FakeProvider):
            def chat(self, *, system, messages, tools=None, **kw):
                # one slow call, then fast ones: cancel lands mid-turn
                # and the stop event is honored between steps
                import time as _t
                _t.sleep(2)
                return {"content": "", "tool_calls": []}

        loop = Loop(provider=_SlowProvider(),
                    budget=Budget(hard=100_000, soft=80_000),
                    approver=None, usage_tracker=None)
        out = json.loads(loop._delegate(sess, {"task": "slow"}))
        self.assertEqual(out["status"], "running")
        time.sleep(0.5)  # let the thread start the model call
        cout = json.loads(loop._cancel_subagent())
        self.assertEqual(cout["status"], "cancelled")
        self.assertTrue(loop._subagent["cancelled"])
        # after the slow call returns, the stop event ends the turn
        loop._subagent["thread"].join(timeout=30)
        self.assertFalse(loop._subagent["thread"].is_alive())
        text = Path(out["result_path"]).read_text(encoding="utf-8")
        self.assertIn("- status: cancelled", text)


if __name__ == "__main__":
    unittest.main()
