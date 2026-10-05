import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.__main__ import _match_session, parse_repl_command


class TestReplCommands(unittest.TestCase):
    def test_non_command(self):
        self.assertIsNone(parse_repl_command("hello world"))
        self.assertIsNone(parse_repl_command(""))

    def test_bare_commands(self):
        self.assertEqual(parse_repl_command("/list"), ("list", ""))
        self.assertEqual(parse_repl_command("/HELP"), ("help", ""))
        self.assertEqual(parse_repl_command("/"), None)

    def test_command_with_arg(self):
        self.assertEqual(parse_repl_command("/new do the thing"),
                         ("new", "do the thing"))
        self.assertEqual(parse_repl_command("/open 20261005"),
                         ("open", "20261005"))

    def test_match_session(self):
        with tempfile.TemporaryDirectory() as d:
            for sid in ("20261005-080000", "20261005-090000"):
                (Path(d) / sid).mkdir()
            cfg = SimpleNamespace(sessions_path=Path(d))
            self.assertEqual(
                _match_session(cfg, "20261005-080000"), "20261005-080000")
            self.assertEqual(
                _match_session(cfg, "20261005-09"), "20261005-090000")
            self.assertIsNone(_match_session(cfg, "20261005"))  # ambiguous
            self.assertIsNone(_match_session(cfg, "nope"))


if __name__ == "__main__":
    unittest.main()
