"""Completed subagents are recorded in history.md at episode close."""
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


class TestSubagentHistory(unittest.TestCase):
    def test_completion_recorded_in_history(self):
        provider = MagicMock()
        provider.name = "openai"
        loop = Loop(provider=provider,
                    budget=Budget(hard=100_000, soft=80_000),
                    approver=None, usage_tracker=None)
        sess = _session()
        with patch("threading.Thread"):
            d_out = json.loads(loop._delegate(
                sess, {"task": "curate the history"}))
        # finish the subagent
        sdir = Path(d_out["session_dir"])
        (sdir / "result.md").write_text(
            "# Subagent result\n- status: completed\n", encoding="utf-8")
        slot = loop._subagent
        slot["thread"] = MagicMock()
        slot["thread"].is_alive.return_value = False
        w_out = json.loads(loop._wait_subagent({}))
        self.assertEqual(w_out["status"], "completed")
        # one subagent queued for the history record
        self.assertEqual(len(loop._completed_subagents), 1)
        # close the episode: record lands in history.md
        (sess.dir / "scratch.md").write_text("## tool x\n", encoding="utf-8")
        loop._close_episode(sess, 1, "done")
        hist = (sess.dir / "history.md").read_text(encoding="utf-8")
        self.assertIn("subagents:", hist)
        self.assertIn("curate the history", hist)
        self.assertIn("completed", hist)
        self.assertIn("result.md", hist)
        # drained
        self.assertEqual(loop._completed_subagents, [])

    def test_no_subagent_no_section(self):
        provider = MagicMock()
        loop = Loop(provider=provider,
                    budget=Budget(hard=100_000, soft=80_000),
                    approver=None, usage_tracker=None)
        sess = _session()
        (sess.dir / "scratch.md").write_text("", encoding="utf-8")
        loop._close_episode(sess, 1, "hi")
        hist = (sess.dir / "history.md").read_text(encoding="utf-8")
        self.assertNotIn("subagents:", hist)


if __name__ == "__main__":
    unittest.main()
