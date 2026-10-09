"""delegate/wait/cancel bypass approval; mutations still need it."""
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


class TestDelegationNoApproval(unittest.TestCase):
    def test_delegate_skips_approver(self):
        approver = MagicMock()
        approver.resolve.return_value = (False, "DENIED by test")
        provider = MagicMock()
        provider.name = "openai"
        loop = Loop(provider=provider,
                    budget=Budget(hard=100_000, soft=80_000),
                    approver=approver, usage_tracker=None)
        sess = _session()
        with patch("threading.Thread"):
            out = json.loads(loop._delegate(sess, {"task": "x"}))
        # would have been denied if the approver ran
        self.assertEqual(out["status"], "running")
        approver.resolve.assert_not_called()

    def test_exec_still_needs_approver(self):
        approver = MagicMock()
        approver.resolve.return_value = (False, "DENIED by test")
        provider = MagicMock()
        provider.name = "openai"
        loop = Loop(provider=provider,
                    budget=Budget(hard=100_000, soft=80_000),
                    approver=approver, usage_tracker=None)
        sess = _session()
        # _execute_tool path: approver denies exec
        result = loop._execute_tool(
            sess, {"name": "exec", "arguments": {"command": "ls"}})
        self.assertIn("DENIED", result)
        approver.resolve.assert_called_once()


if __name__ == "__main__":
    unittest.main()
