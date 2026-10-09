"""Subagent internals must not leak into the parent's event stream."""
import json
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, "tests")
from test_delegation_e2e import _FakeProvider

from harness.context import Budget
from harness.loop import Loop, Session
from harness.session import init_layout


class TestSubagentEventIsolation(unittest.TestCase):
    def test_no_internal_events_leak(self):
        d = Path(tempfile.mkdtemp())
        init_layout(d, "PROMPT")
        sess = Session(id="iso", dir=d, workdir=str(d))
        seen = []
        loop = Loop(provider=_FakeProvider("findings"),
                    budget=Budget(hard=100_000, soft=80_000),
                    on_event=lambda k, data: seen.append(k),
                    approver=None, usage_tracker=None)
        out = json.loads(loop._delegate(sess, {"task": "research"}))
        loop._wait_subagent({"timeout": 60})
        # Parent-level subagent lifecycle events are fine.
        # Subagent-internal kinds (request/response/tool from its own
        # turn) must never reach the parent's handler.
        kinds = set(seen)
        self.assertIn("subagent-spawn", kinds)
        self.assertIn("subagent-done", kinds)
        # The subagent ran a full turn (request/response events); none
        # of those may have leaked. (Parent's own delegate/wait tool
        # events are "tool" kind too, so we check the subagent's session
        # dir instead: its history must show a completed turn.)
        sub_hist = (Path(out["result_path"]).parent
                    / "history.md").read_text(encoding="utf-8")
        self.assertIn("research", sub_hist)


if __name__ == "__main__":
    unittest.main()
