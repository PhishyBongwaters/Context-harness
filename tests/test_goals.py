"""Red-first: user-creatable goals via /goal and --goal."""
import tempfile
import unittest
from pathlib import Path

from harness.session import add_goal, init_layout


def fresh():
    d = Path(tempfile.mkdtemp())
    init_layout(d, "PROMPT")
    return d


class TestAddGoal(unittest.TestCase):
    def test_adds_active_goal_line(self):
        d = fresh()
        self.assertEqual(add_goal(d, "Ship the widget"), "added")
        text = (d / "sats" / "goals.md").read_text(encoding="utf-8")
        self.assertIn("- [active] Ship the widget", text)

    def test_strips_surrounding_quotes(self):
        d = fresh()
        add_goal(d, '"Build the thing"')
        text = (d / "sats" / "goals.md").read_text(encoding="utf-8")
        self.assertIn("- [active] Build the thing", text)
        self.assertNotIn('"Build', text)

    def test_duplicate_is_noop(self):
        d = fresh()
        self.assertEqual(add_goal(d, "Ship it"), "added")
        self.assertEqual(add_goal(d, "Ship it"), "exists")
        text = (d / "sats" / "goals.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("Ship it"), 1)

    def test_empty_is_rejected(self):
        d = fresh()
        self.assertEqual(add_goal(d, "   "), "empty")

    def test_creates_sat_in_old_sessions(self):
        d = fresh()
        (d / "sats" / "goals.md").unlink()
        self.assertEqual(add_goal(d, "New goal"), "added")
        self.assertTrue((d / "sats" / "goals.md").is_file())

    def test_done_goals_not_duplicated(self):
        d = fresh()
        (d / "sats" / "goals.md").write_text(
            "# Goals\n\n- [done] Ship it\n", encoding="utf-8")
        # Re-adding a completed goal re-activates instead of duplicating.
        self.assertEqual(add_goal(d, "Ship it"), "added")
        text = (d / "sats" / "goals.md").read_text(encoding="utf-8")
        self.assertEqual(text.count("Ship it"), 1)
        self.assertIn("- [active] Ship it", text)


if __name__ == "__main__":
    unittest.main()
