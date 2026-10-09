"""Red-first: subagent session layout (D1)."""
import tempfile
import unittest
from pathlib import Path

from harness.session import init_layout, init_subagent_session


class TestInitSubagentSession(unittest.TestCase):
    def setUp(self):
        self.parent = Path(tempfile.mkdtemp())
        init_layout(self.parent, "PARENT PROMPT")

    def test_creates_subagents_dir(self):
        paths = init_subagent_session(self.parent, "Do the thing")
        sdir = paths["dir"]
        self.assertTrue(sdir.is_dir())
        self.assertEqual(sdir.parent.parent, self.parent)
        self.assertEqual(sdir.parent.name, "subagents")

    def test_seeds_current_and_goals(self):
        paths = init_subagent_session(self.parent, "Do the thing")
        sdir = paths["dir"]
        current = (sdir / "sats" / "current.md").read_text()
        self.assertIn("Do the thing", current)
        goals = (sdir / "sats" / "goals.md").read_text()
        self.assertIn("[active]", goals)

    def test_prompt_has_subagent_prefix(self):
        paths = init_subagent_session(self.parent, "Do the thing")
        prompt = (paths["dir"] / "prompt.md").read_text()
        self.assertIn("subagent", prompt.lower())
        self.assertIn("PARENT PROMPT", prompt)

    def test_unique_ids(self):
        a = init_subagent_session(self.parent, "task one")
        b = init_subagent_session(self.parent, "task two")
        self.assertNotEqual(a["id"], b["id"])
        self.assertNotEqual(a["dir"], b["dir"])

    def test_full_blank_slate_layout(self):
        paths = init_subagent_session(self.parent, "Do the thing")
        sdir = paths["dir"]
        for name in ("prompt.md", "state.json", "index.md",
                     "history.md", "scratch.md"):
            self.assertTrue((sdir / name).is_file(), name)
        for name in ("current", "goals", "facts", "decisions", "tasks"):
            self.assertTrue((sdir / "sats" / f"{name}.md").is_file(),
                            name)

    def test_prompt_says_no_delegation(self):
        # The subagent must not follow the parent's "prefer delegate"
        # guidance: it has no delegate tool, and trying it loops.
        paths = init_subagent_session(self.parent, "Do the thing")
        prompt = (paths["dir"] / "prompt.md").read_text(encoding="utf-8")
        self.assertIn("NO delegate tool", prompt)
        self.assertIn("cannot spawn subagents", prompt)


if __name__ == "__main__":
    unittest.main()
