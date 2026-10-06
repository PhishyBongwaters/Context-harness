"""InputHistory: up/down recall with draft preservation."""
import unittest

from harness.tui.widgets import InputHistory


class TestInputHistory(unittest.TestCase):
    def test_empty_history_returns_current(self):
        h = InputHistory()
        self.assertEqual(h.older("draft"), "draft")
        self.assertEqual(h.newer("draft"), "draft")

    def test_older_newer_cycle(self):
        h = InputHistory()
        h.add("one")
        h.add("two")
        h.add("three")
        self.assertEqual(h.older(""), "three")
        self.assertEqual(h.older("three"), "two")
        self.assertEqual(h.older("two"), "one")
        self.assertEqual(h.older("one"), "one")  # stays at oldest
        self.assertEqual(h.newer("one"), "two")
        self.assertEqual(h.newer("two"), "three")

    def test_draft_preserved_past_newest(self):
        h = InputHistory()
        h.add("one")
        h.add("two")
        self.assertEqual(h.older("my draft"), "two")
        self.assertEqual(h.newer("two"), "my draft")

    def test_add_resets_navigation(self):
        h = InputHistory()
        h.add("one")
        h.add("two")
        self.assertEqual(h.older(""), "two")
        h.add("three")
        self.assertEqual(h.older(""), "three")
        self.assertEqual(h.newer("three"), "")

    def test_consecutive_dupes_collapse(self):
        h = InputHistory()
        h.add("same")
        h.add("same")
        self.assertEqual(len(h), 1)
        h.add("other")
        h.add("same")
        self.assertEqual(len(h), 3)

    def test_edit_while_navigating_restarts(self):
        h = InputHistory()
        h.add("one")
        h.add("two")
        self.assertEqual(h.older(""), "two")
        # user edits the recalled entry, then goes up again: the edit
        # becomes the draft, navigation restarts from the newest
        self.assertEqual(h.older("two edited"), "two")
        self.assertEqual(h.newer("two"), "two edited")

    def test_limit(self):
        h = InputHistory(limit=3)
        for i in range(5):
            h.add(f"cmd{i}")
        self.assertEqual(len(h), 3)
        self.assertEqual(h.older(""), "cmd4")
        self.assertEqual(h.older("cmd4"), "cmd3")
        self.assertEqual(h.older("cmd3"), "cmd2")
        self.assertEqual(h.older("cmd2"), "cmd2")


if __name__ == "__main__":
    unittest.main()
