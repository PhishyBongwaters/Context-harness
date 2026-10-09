"""Red-first tests for T10: migration from old context.md layout.

Old: single accumulating context.md (## user/assistant/tool).
New: stamped history.md, per-turn archives + episode pointers, seeded
turn counter. Loud on ambiguity, never lossy (original kept as .bak).
"""
import glob
import json
import tempfile
import unittest
from pathlib import Path

from harness.session import (MigrationError, init_layout, is_old_layout,
                             migrate_session)


OLD_TRANSCRIPT = """# Context

This file is your live context -- the transcript itself.

## user
what is 2+2?

## assistant
let me compute
```tool-calls
[{"id": "c1", "name": "exec", "arguments": {"command": "echo 4"}}]
```
## tool c1
exit=0
4

## assistant
4

## user
and 3+3?

## assistant
6
"""


def old_session_dir():
    d = Path(tempfile.mkdtemp())
    (d / "context.md").write_text(OLD_TRANSCRIPT, encoding="utf-8")
    return d


class TestMigration(unittest.TestCase):
    def test_detects_old_layout(self):
        d = old_session_dir()
        self.assertTrue(is_old_layout(d))
        # Fresh dir: no.
        d2 = Path(tempfile.mkdtemp())
        self.assertFalse(is_old_layout(d2))
        # New layout: no.
        init_layout(d2, "P")
        self.assertFalse(is_old_layout(d2))

    def test_migrate_returns_none_for_new_layout(self):
        d = Path(tempfile.mkdtemp())
        init_layout(d, "P")
        self.assertIsNone(migrate_session(d))

    def test_users_become_stamped_history(self):
        d = old_session_dir()
        rep = migrate_session(d)
        self.assertEqual(rep["turns"], 2)
        history = (d / "history.md").read_text(encoding="utf-8")
        self.assertIn("## user t0001", history)
        self.assertIn("what is 2+2?", history)
        self.assertIn("## user t0002", history)
        self.assertIn("and 3+3?", history)

    def test_episode_archived_with_pointer(self):
        d = old_session_dir()
        rep = migrate_session(d)
        self.assertEqual(rep["episodes"], 2)
        arch1 = d / "archive" / "migrated-t0001.md"
        self.assertTrue(arch1.exists())
        body = arch1.read_text(encoding="utf-8")
        self.assertIn("let me compute", body)
        self.assertIn("## tool c1", body)
        self.assertIn("exit=0\n4", body)
        history = (d / "history.md").read_text(encoding="utf-8")
        self.assertIn("## episode t0001", history)
        self.assertIn("archive/migrated-t0001.md", history)
        self.assertIn("tool_calls: 1", history)
        # Turn 2 had no tools: archived too, count 0.
        self.assertIn("## episode t0002", history)
        self.assertIn("tool_calls: 0", history)

    def test_orphan_tools_dropped_and_reported(self):
        d = Path(tempfile.mkdtemp())
        (d / "context.md").write_text(
            "## user\nq?\n## tool zx\nnever sent\n## assistant\na\n",
            encoding="utf-8")
        rep = migrate_session(d)
        self.assertEqual(rep["dropped_orphans"], 1)
        arch = d / "archive" / "migrated-t0001.md"
        self.assertNotIn("zx", arch.read_text(encoding="utf-8"))

    def test_loud_on_leading_assistant(self):
        d = Path(tempfile.mkdtemp())
        (d / "context.md").write_text("## assistant\nhi\n## user\nq\n",
                                      encoding="utf-8")
        with self.assertRaises(MigrationError):
            migrate_session(d)
        # Nothing was written: no history, no state, original intact.
        self.assertFalse((d / "history.md").exists())
        self.assertFalse((d / "state.json").exists())
        self.assertTrue((d / "context.md").exists())

    def test_loud_on_leading_tool(self):
        d = Path(tempfile.mkdtemp())
        (d / "context.md").write_text("## tool c9\nx\n## user\nq\n",
                                      encoding="utf-8")
        with self.assertRaises(MigrationError):
            migrate_session(d)

    def test_loud_on_empty_transcript(self):
        d = Path(tempfile.mkdtemp())
        (d / "context.md").write_text("# Context\n\nnothing yet\n",
                                      encoding="utf-8")
        with self.assertRaises(MigrationError):
            migrate_session(d)

    def test_turn_counter_seeded(self):
        d = old_session_dir()
        migrate_session(d)
        state = json.loads((d / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(state["turn"], 2)

    def test_original_backed_up(self):
        d = old_session_dir()
        migrate_session(d)
        baks = glob.glob(str(d / "context.md.pre-migration-*.bak"))
        self.assertEqual(len(baks), 1)
        with open(baks[0], encoding="utf-8") as f:
            self.assertEqual(f.read(), OLD_TRANSCRIPT)
        self.assertFalse((d / "context.md").exists())

    def test_init_layout_runs_migration(self):
        d = old_session_dir()
        paths = init_layout(d, "PROMPT")
        self.assertIsNotNone(paths["migration"])
        self.assertEqual(paths["migration"]["turns"], 2)
        # Layout files exist alongside migrated content.
        self.assertTrue((d / "prompt.md").exists())
        self.assertTrue((d / "sats" / "facts.md").exists())
        # Second run: no re-migration.
        paths2 = init_layout(d, "PROMPT")
        self.assertIsNone(paths2["migration"])


if __name__ == "__main__":
    unittest.main()
