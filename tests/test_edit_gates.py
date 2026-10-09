"""Red-first tests for T5: edit-gate rules for session sources.

Spec: docs/assembly-spec.md section 5 (source table).
Model may edit history.md and sats/*.md (edit-only). Everything else
in the session dir is harness-owned. write is rejected on all session
sources; read is denied only for state.json.
"""
import tempfile
import unittest
from pathlib import Path

from harness.session import init_layout
from harness.tools import source_gate


def make_session():
    td = tempfile.mkdtemp()
    sdir = Path(td) / "sess"
    init_layout(sdir, "PROMPT")
    return td, sdir


class TestSourceGates(unittest.TestCase):
    def test_edit_allowed_on_history(self):
        td, sdir = make_session()
        self.assertIsNone(source_gate(sdir, "edit",
                                      str(sdir / "history.md"), td))

    def test_edit_allowed_on_sat(self):
        td, sdir = make_session()
        self.assertIsNone(source_gate(sdir, "edit",
                                      str(sdir / "sats" / "facts.md"), td))

    def test_write_denied_on_history(self):
        td, sdir = make_session()
        denial = source_gate(sdir, "write", str(sdir / "history.md"), td)
        self.assertIsNotNone(denial)
        self.assertIn("DENIED", denial)
        self.assertIn("edit", denial)

    def test_write_denied_on_sat(self):
        td, sdir = make_session()
        denial = source_gate(sdir, "write",
                             str(sdir / "sats" / "tasks.md"), td)
        self.assertIsNotNone(denial)
        self.assertIn("DENIED", denial)

    def test_edit_denied_on_harness_owned(self):
        td, sdir = make_session()
        for name in ("prompt.md", "state.json", "index.md", "scratch.md",
                     "context.md"):
            denial = source_gate(sdir, "edit", str(sdir / name), td)
            self.assertIsNotNone(denial, name)
            self.assertIn("DENIED", denial, name)
            self.assertIn("harness-owned", denial, name)

    def test_edit_denied_on_archive(self):
        td, sdir = make_session()
        denial = source_gate(sdir, "edit",
                             str(sdir / "archive" / "ep.md"), td)
        self.assertIsNotNone(denial)
        self.assertIn("DENIED", denial)

    def test_write_denied_on_harness_owned(self):
        td, sdir = make_session()
        denial = source_gate(sdir, "write", str(sdir / "prompt.md"), td)
        self.assertIsNotNone(denial)
        self.assertIn("DENIED", denial)

    def test_read_denied_on_state_json(self):
        td, sdir = make_session()
        denial = source_gate(sdir, "read", str(sdir / "state.json"), td)
        self.assertIsNotNone(denial)
        self.assertIn("DENIED", denial)

    def test_read_allowed_on_other_sources(self):
        td, sdir = make_session()
        for name in ("prompt.md", "index.md", "history.md", "scratch.md",
                     "context.md"):
            self.assertIsNone(source_gate(sdir, "read",
                                          str(sdir / name), td), name)
        self.assertIsNone(source_gate(sdir, "read",
                                      str(sdir / "sats" / "facts.md"), td))

    def test_model_files_outside_session_allowed(self):
        td, sdir = make_session()
        other = Path(td) / "work" / "code.py"
        self.assertIsNone(source_gate(sdir, "edit", str(other), td))
        self.assertIsNone(source_gate(sdir, "write", str(other), td))

    def test_relative_paths_resolve_against_workdir(self):
        td, sdir = make_session()
        # workdir == session dir here: relative source paths are gated.
        denial = source_gate(sdir, "edit", "prompt.md", str(sdir))
        self.assertIsNotNone(denial)
        self.assertIn("DENIED", denial)

    def test_exec_not_gated(self):
        td, sdir = make_session()
        self.assertIsNone(source_gate(sdir, "exec", "", td))


class TestGateWiring(unittest.TestCase):
    """The gates actually fire inside Loop._execute_tool."""

    def test_denied_edit_returns_denial_and_touches_nothing(self):
        import sys
        sys.path.insert(0, "tests")
        from test_loop import MockProvider, make_session
        from harness.loop import Loop, Budget
        s = make_session()
        before = (s.dir / "prompt.md").read_text(encoding="utf-8")
        loop = Loop(MockProvider([]), Budget(100000, 80000))
        out = loop._execute_tool(
            s, {"id": "g1", "name": "edit",
                "arguments": {"path": str(s.dir / "prompt.md"),
                              "old_text": "a", "new_text": "b"}})
        self.assertIn("DENIED", out)
        self.assertIn("harness-owned", out)
        self.assertEqual((s.dir / "prompt.md").read_text(encoding="utf-8"),
                         before)

    def test_allowed_history_edit_gets_backup(self):
        import glob
        import sys
        sys.path.insert(0, "tests")
        from test_loop import MockProvider, make_session
        from harness.loop import Loop, Budget
        s = make_session()
        hist = s.dir / "history.md"
        hist.write_text("## user t0001\nhello\n", encoding="utf-8")
        loop = Loop(MockProvider([]), Budget(100000, 80000))
        out = loop._execute_tool(
            s, {"id": "g2", "name": "edit",
                "arguments": {"path": str(hist),
                              "old_text": "hello\n",
                              "new_text": "hello world\n"}})
        self.assertIn("1 occurrence replaced", out)
        self.assertIn("hello world", hist.read_text(encoding="utf-8"))
        baks = glob.glob(str(s.dir / "history.pre-edit-*.bak"))
        self.assertEqual(len(baks), 1)


if __name__ == "__main__":
    unittest.main()
