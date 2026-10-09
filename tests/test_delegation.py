"""Red-first: subagent delegation tools (D4/D5/D8)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from harness.loop import Loop, Session
from harness.session import init_layout
from harness.tools import tool_definitions


def _parent_loop(**kw):
    provider = MagicMock()
    provider.complete.return_value = {"content": "done",
                                      "tool_calls": []}
    from harness.context import Budget
    return Loop(provider=provider,
                budget=Budget(hard=100_000, soft=80_000),
                approver=None, usage_tracker=None,
                **kw)


def _session():
    d = Path(tempfile.mkdtemp())
    init_layout(d, "PROMPT")
    return Session(id="test", dir=d, workdir=str(d))


class TestDelegationTools(unittest.TestCase):
    def test_definitions_present(self):
        names = [t["name"] for t in tool_definitions()]
        for n in ("delegate", "wait_subagent", "cancel_subagent"):
            self.assertIn(n, names)

    def test_subagent_gets_no_delegation_tools(self):
        names = [t["name"]
                 for t in tool_definitions(include_delegation=False)]
        for n in ("delegate", "wait_subagent", "cancel_subagent"):
            self.assertNotIn(n, names)

    def test_allowlist_filters(self):
        loop = _parent_loop(is_subagent=True,
                            tools_allowlist=["read", "tokens"])
        names = [t["name"] for t in loop._tools]
        self.assertEqual(sorted(names), ["read", "tokens"])


class TestSubagentGate(unittest.TestCase):
    def test_denies_parent_session_access(self):
        loop = _parent_loop(is_subagent=True, subagent_id="abc")
        sess = _session()
        # fake the subagent dir layout: <parent>/subagents/<id>
        subdir = sess.dir / "subagents" / "abc"
        subdir.mkdir(parents=True)
        sub_sess = Session(id="sub-abc", dir=subdir,
                           workdir=str(sess.dir))
        # reading the parent's history.md -> denied
        denial = loop._subagent_gate(
            sub_sess, "read",
            {"path": str(sess.dir / "history.md")})
        self.assertIsNotNone(denial)
        self.assertIn("DENIED", denial)

    def test_allows_own_dir(self):
        loop = _parent_loop(is_subagent=True, subagent_id="abc")
        sess = _session()
        subdir = sess.dir / "subagents" / "abc"
        subdir.mkdir(parents=True)
        sub_sess = Session(id="sub-abc", dir=subdir,
                           workdir=str(sess.dir))
        denial = loop._subagent_gate(
            sub_sess, "read",
            {"path": str(subdir / "sats" / "current.md")})
        self.assertIsNone(denial)

    def test_allows_workdir(self):
        loop = _parent_loop(is_subagent=True, subagent_id="abc")
        sess = _session()
        subdir = sess.dir / "subagents" / "abc"
        subdir.mkdir(parents=True)
        wd = Path(tempfile.mkdtemp())
        sub_sess = Session(id="sub-abc", dir=subdir, workdir=str(wd))
        denial = loop._subagent_gate(
            sub_sess, "read", {"path": str(wd / "notes.txt")})
        self.assertIsNone(denial)


class TestDelegateFlow(unittest.TestCase):
    def test_delegate_refuses_while_running(self):
        loop = _parent_loop()
        sess = _session()
        # fake a running subagent
        thread = MagicMock()
        thread.is_alive.return_value = True
        loop._subagent = {"thread": thread}
        out = json.loads(loop._delegate(sess, {"task": "x"}))
        self.assertEqual(out["status"], "error")

    def test_delegate_requires_task(self):
        loop = _parent_loop()
        sess = _session()
        out = json.loads(loop._delegate(sess, {"task": "   "}))
        self.assertEqual(out["status"], "error")

    def test_wait_no_subagent(self):
        loop = _parent_loop()
        out = json.loads(loop._wait_subagent({"timeout": 0}))
        self.assertEqual(out["status"], "error")

    def test_cancel_no_subagent(self):
        loop = _parent_loop()
        out = json.loads(loop._cancel_subagent())
        self.assertEqual(out["status"], "error")


if __name__ == "__main__":
    unittest.main()
