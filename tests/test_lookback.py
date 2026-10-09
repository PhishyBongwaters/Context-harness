"""T8: archive lookback by path.

The episode pointer names the archive file; the model re-reads it on
demand with the normal read tool (deliberate, visible cost -- the read
result lands in the current episode's scratch). No new machinery: the
T5 source gates already allow reads on archive/*.
"""
import sys
import unittest

sys.path.insert(0, "tests")
from test_loop import make_session  # noqa: E402

from harness.loop import Budget, Loop
from harness.providers import MockProvider
from harness.tools import source_gate


class TestArchiveLookback(unittest.TestCase):
    def _archived_turn(self, s):
        loop = Loop(MockProvider([
            {"content": None, "tool_calls": [
                {"id": "e1", "name": "exec",
                 "arguments": {"command": "echo SECRET42"}}]},
            {"content": "done"},
        ]), Budget(100000, 80000))
        self.assertEqual(loop.run_turn(s, "run it"), "done")
        files = list((s.dir / "archive").glob("*.md"))
        self.assertEqual(len(files), 1)
        return files[0]

    def test_gate_allows_archive_read(self):
        s = make_session()
        arch = self._archived_turn(s)
        # read is allowed on archives (absolute path)...
        self.assertIsNone(
            source_gate(s.dir, "read", str(arch), s.workdir))
        # ...but edit/write stay denied there.
        self.assertIsNotNone(
            source_gate(s.dir, "edit", str(arch), s.workdir))
        self.assertIsNotNone(
            source_gate(s.dir, "write", str(arch), s.workdir))

    def test_model_reads_archive_end_to_end(self):
        s = make_session()
        arch = self._archived_turn(s)
        # Turn 2: the model follows the pointer and reads the archive.
        loop = Loop(MockProvider([
            {"content": None, "tool_calls": [
                {"id": "r1", "name": "read",
                 "arguments": {"path": str(arch)}}]},
            {"content": "saw SECRET42 in the archive"},
        ]), Budget(100000, 80000))
        out = loop.run_turn(s, "what did the last episode find?")
        self.assertEqual(out, "saw SECRET42 in the archive")
        # The lookback itself was a deliberate, visible tool call: it
        # landed in the new episode's archive.
        new_arch = [p for p in (s.dir / "archive").glob("*.md")
                    if p != arch]
        self.assertEqual(len(new_arch), 1)
        self.assertIn("SECRET42",
                      new_arch[0].read_text(encoding="utf-8"))

    def test_relative_archive_path_resolves_from_session(self):
        # The pointer names archive/<file> relative to the session dir;
        # the model derives the absolute path from the session dir it
        # knows (ctx_path in its prompt). The gate resolves it.
        s = make_session()
        arch = self._archived_turn(s)
        rel = "archive/" + arch.name
        # Relative to workdir this is NOT the archive (workdir != s.dir
        # in general); the gate classifies by resolved location.
        self.assertIsNone(
            source_gate(s.dir, "read", str(s.dir / rel), s.workdir))


if __name__ == "__main__":
    unittest.main()
