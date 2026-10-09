"""Red-first tests for T4: replace-only edit, exactly-once contract.

Spec: docs/assembly-spec.md section 5. The edit tool fails loudly
unless old_text matches exactly once: zero matches -> error naming
the file; two or more -> error telling the model to add context.
"""
import tempfile
import unittest
from pathlib import Path

from harness.tools import edit_tool, run_tool


def make_file(content: str) -> tuple[str, str]:
    td = tempfile.mkdtemp()
    p = Path(td) / "f.md"
    p.write_text(content, encoding="utf-8")
    return td, str(p)


class TestEditExactlyOnce(unittest.TestCase):
    def test_unique_match_replaces(self):
        td, p = make_file("alpha\nbeta\ngamma\n")
        out = edit_tool({"path": p, "old_text": "beta\n",
                         "new_text": "BETA\n"}, td)
        self.assertIn("1 occurrence replaced", out)
        self.assertEqual(Path(p).read_text(encoding="utf-8"),
                         "alpha\nBETA\ngamma\n")

    def test_zero_matches_errors_and_leaves_file(self):
        td, p = make_file("alpha\n")
        out = edit_tool({"path": p, "old_text": "zzz",
                         "new_text": "q"}, td)
        self.assertIn("ERROR", out)
        self.assertIn("not found", out)
        self.assertEqual(Path(p).read_text(encoding="utf-8"), "alpha\n")

    def test_two_matches_errors_and_leaves_file(self):
        td, p = make_file("x = 1\nx = 1\n")
        out = edit_tool({"path": p, "old_text": "x = 1",
                         "new_text": "x = 2"}, td)
        self.assertIn("ERROR", out)
        self.assertIn("2 times", out)
        self.assertIn("more surrounding context", out)
        self.assertEqual(Path(p).read_text(encoding="utf-8"),
                         "x = 1\nx = 1\n")

    def test_empty_old_text_rejected(self):
        td, p = make_file("alpha\n")
        out = edit_tool({"path": p, "old_text": "",
                         "new_text": "q"}, td)
        self.assertIn("ERROR", out)
        self.assertEqual(Path(p).read_text(encoding="utf-8"), "alpha\n")

    def test_deletion_allowed_when_unique(self):
        td, p = make_file("alpha\nREMOVE_ME\nbeta\n")
        out = edit_tool({"path": p, "old_text": "REMOVE_ME\n",
                         "new_text": ""}, td)
        self.assertIn("1 occurrence replaced", out)
        self.assertEqual(Path(p).read_text(encoding="utf-8"),
                         "alpha\nbeta\n")

    def test_run_tool_dispatches_exact_contract(self):
        td, p = make_file("a\na\n")
        out = run_tool("edit", {"path": p, "old_text": "a",
                                "new_text": "b"}, td)
        self.assertIn("ERROR", out)


if __name__ == "__main__":
    unittest.main()
