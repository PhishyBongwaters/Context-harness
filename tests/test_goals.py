"""Red-first: user-creatable goals via /goal and --goal."""
import tempfile
import unittest
from pathlib import Path

from harness.context import Budget
from harness.loop import Loop, Session
from harness.providers import MockProvider
from harness.session import (active_goals, add_goal, complete_goal,
                             goals_done, init_layout)
from harness.tools import tool_definitions


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


class TestCompleteGoal(unittest.TestCase):
    def test_flips_named_goal(self):
        d = fresh()
        add_goal(d, "Ship the widget")
        add_goal(d, "Write the docs")
        status, matched, remaining = complete_goal(d, "Ship the widget")
        self.assertEqual((status, matched), ("done", "Ship the widget"))
        self.assertEqual(remaining, ["Write the docs"])
        text = (d / "sats" / "goals.md").read_text(encoding="utf-8")
        self.assertIn("- [done] Ship the widget", text)
        self.assertIn("- [active] Write the docs", text)

    def test_no_arg_with_single_goal(self):
        d = fresh()
        add_goal(d, "Only goal")
        status, matched, remaining = complete_goal(d)
        self.assertEqual((status, matched, remaining),
                         ("done", "Only goal", []))
        self.assertTrue(goals_done(d))

    def test_no_arg_with_several_is_ambiguous(self):
        d = fresh()
        add_goal(d, "First goal")
        add_goal(d, "Second goal")
        status, matched, remaining = complete_goal(d)
        self.assertEqual(status, "ambiguous")
        self.assertIsNone(matched)
        self.assertEqual(remaining, ["First goal", "Second goal"])
        self.assertEqual(active_goals(d), ["First goal", "Second goal"])

    def test_unknown_text_is_not_found(self):
        d = fresh()
        add_goal(d, "Real goal")
        status, matched, remaining = complete_goal(d, "No such goal")
        self.assertEqual((status, matched, remaining),
                         ("not_found", None, ["Real goal"]))
        self.assertEqual(active_goals(d), ["Real goal"])

    def test_unique_substring_matches(self):
        d = fresh()
        add_goal(d, "Ship the widget")
        status, matched, _ = complete_goal(d, "widget")
        self.assertEqual((status, matched), ("done", "Ship the widget"))

    def test_ambiguous_substring_is_not_found(self):
        d = fresh()
        add_goal(d, "Ship widget A")
        add_goal(d, "Ship widget B")
        status, _, remaining = complete_goal(d, "widget")
        self.assertEqual(status, "not_found")
        self.assertEqual(remaining, ["Ship widget A", "Ship widget B"])
        self.assertEqual(len(active_goals(d)), 2)

    def test_missing_file_is_no_active(self):
        d = fresh()
        (d / "sats" / "goals.md").unlink()
        self.assertEqual(complete_goal(d), ("no_active", None, []))
        self.assertFalse((d / "sats" / "goals.md").exists())


class TestGoalDoneTool(unittest.TestCase):
    def test_definition_present_and_goal_optional(self):
        defs = {t["name"]: t for t in tool_definitions()}
        self.assertIn("goal_done", defs)
        required = defs["goal_done"]["parameters"].get("required", [])
        self.assertNotIn("goal", required)


class _NeverCalledApprover:
    def resolve(self, policy, name, args, subagent_id=None):
        raise AssertionError("approver must not be consulted for goal_done")


class TestGoalDoneLoop(unittest.TestCase):
    def test_execute_tool_flips_without_approval(self):
        d = fresh()
        add_goal(d, "Loop goal")
        session = Session(id="t", dir=d, workdir=str(d))
        loop = Loop(MockProvider([]), Budget(hard=100000, soft=80000),
                    approver=_NeverCalledApprover())
        result = loop._execute_tool(
            session, {"name": "goal_done", "arguments": {}})
        self.assertIn("Goal marked done: Loop goal", result)
        self.assertIn("No active goals remain", result)
        self.assertTrue(goals_done(d))
        text = (d / "sats" / "goals.md").read_text(encoding="utf-8")
        self.assertIn("- [done] Loop goal", text)

    def test_execute_tool_unknown_goal_lists_active(self):
        d = fresh()
        add_goal(d, "Kept goal")
        session = Session(id="t", dir=d, workdir=str(d))
        loop = Loop(MockProvider([]), Budget(hard=100000, soft=80000),
                    approver=_NeverCalledApprover())
        result = loop._execute_tool(
            session, {"name": "goal_done",
                      "arguments": {"goal": "Missing goal"}})
        self.assertIn("No active goal matches", result)
        self.assertIn("Kept goal", result)
        self.assertEqual(active_goals(d), ["Kept goal"])


if __name__ == "__main__":
    unittest.main()
