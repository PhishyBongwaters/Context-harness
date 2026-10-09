"""Parent model can see the subagent's session dir for debugging."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness.context import Budget
from harness.loop import Loop, Session
from harness.session import init_layout


def _session():
    d = Path(tempfile.mkdtemp())
    init_layout(d, "PROMPT")
    return Session(id="t", dir=d, workdir=str(d))


def _loop():
    provider = MagicMock()
    provider.name = "openai"
    return Loop(provider=provider,
                budget=Budget(hard=100_000, soft=80_000),
                approver=None, usage_tracker=None)


class TestSubagentVisibility(unittest.TestCase):
    def test_delegate_returns_session_dir(self):
        loop = _loop()
        sess = _session()
        with patch("threading.Thread"):
            out = json.loads(loop._delegate(sess, {"task": "x"}))
        self.assertEqual(out["status"], "running")
        self.assertIn("session_dir", out)
        self.assertTrue(Path(out["session_dir"]).is_dir())
        self.assertIn("subagents", out["session_dir"])

    def test_wait_returns_session_dir(self):
        loop = _loop()
        sess = _session()
        with patch("threading.Thread"):
            d_out = json.loads(loop._delegate(sess, {"task": "x"}))
        # simulate finished thread with a result file
        sdir = Path(d_out["session_dir"])
        (sdir / "result.md").write_text("# Subagent result\n- status: completed\n",
                                        encoding="utf-8")
        slot = loop._subagent
        slot["thread"] = MagicMock()
        slot["thread"].is_alive.return_value = False
        out = json.loads(loop._wait_subagent({}))
        self.assertIn("session_dir", out)
        self.assertEqual(out["session_dir"], d_out["session_dir"])

    def test_parent_can_read_subagent_history(self):
        # source_gate must not block the parent reading the child's history
        from harness.tools import source_gate
        loop = _loop()
        sess = _session()
        with patch("threading.Thread"):
            d_out = json.loads(loop._delegate(sess, {"task": "x"}))
        hist = str(Path(d_out["session_dir"]) / "history.md")
        denial = source_gate(sess.dir, "read", hist, sess.workdir)
        self.assertIsNone(denial)


if __name__ == "__main__":
    unittest.main()
