"""list_dir and search tools: cross-platform discovery."""
import tempfile
import unittest
from pathlib import Path

from harness.tools import (list_dir_tool, run_tool, search_tool,
                           tool_definitions)


def _tree():
    td = Path(tempfile.mkdtemp())
    (td / "a.py").write_text("hello world\nfoo bar\n", encoding="utf-8")
    (td / "b.txt").write_text("nothing here\n", encoding="utf-8")
    sub = td / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("hello again\n", encoding="utf-8")
    return td


class TestListDir(unittest.TestCase):
    def test_lists_entries(self):
        td = _tree()
        out = list_dir_tool({"path": str(td)}, str(td))
        self.assertIn("a.py", out)
        self.assertIn("b.txt", out)
        self.assertIn("sub", out)

    def test_marks_dirs(self):
        td = _tree()
        out = list_dir_tool({"path": str(td)}, str(td))
        # directories are distinguishable from files
        dir_lines = [l for l in out.splitlines() if "sub" in l]
        self.assertTrue(dir_lines)
        self.assertTrue(any("dir" in l.lower() for l in dir_lines))

    def test_missing_dir_errors(self):
        td = _tree()
        out = list_dir_tool({"path": str(td / "nope")}, str(td))
        self.assertIn("ERROR", out)

    def test_recursive(self):
        td = _tree()
        out = list_dir_tool({"path": str(td), "recursive": True}, str(td))
        self.assertIn("c.py", out)

    def test_in_definitions(self):
        names = [t["name"] for t in tool_definitions()]
        self.assertIn("list_dir", names)
        self.assertIn("search", names)


class TestSearch(unittest.TestCase):
    def test_literal_match(self):
        td = _tree()
        out = search_tool({"path": str(td), "pattern": "hello"}, str(td))
        self.assertIn("a.py", out)
        self.assertIn("c.py", out)
        self.assertNotIn("b.txt", out)

    def test_line_numbers(self):
        td = _tree()
        out = search_tool({"path": str(td), "pattern": "foo"}, str(td))
        self.assertIn("2", out)  # foo bar is on line 2 of a.py
        self.assertIn("foo bar", out)

    def test_regex(self):
        td = _tree()
        out = search_tool({"path": str(td), "pattern": r"h.llo",
                           "regex": True}, str(td))
        self.assertIn("a.py", out)

    def test_file_pattern_filter(self):
        td = _tree()
        out = search_tool({"path": str(td), "pattern": "hello",
                           "file_pattern": "*.txt"}, str(td))
        self.assertNotIn("a.py", out)

    def test_no_match(self):
        td = _tree()
        out = search_tool({"path": str(td), "pattern": "zzz_nope"}, str(td))
        self.assertIn("no matches", out.lower())

    def test_via_run_tool(self):
        td = _tree()
        out = run_tool("search", {"path": str(td), "pattern": "hello"},
                       str(td))
        self.assertIn("a.py", out)


if __name__ == "__main__":
    unittest.main()
