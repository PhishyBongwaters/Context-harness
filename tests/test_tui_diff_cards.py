"""Diff cards for model edits of context sources (opencode behavior).

- diff_transcripts() pairs removed/added sections by header into capped
  unified diffs (section_diffs), without changing the existing keys.
- format_event("context-diff") renders a colored diff card: red - lines,
  green + lines, dim @@ hunks.
- The TUI routes context-diff to the main transcript as a
  message-context-diff card (not the system panel).
Red-first: fails until implemented.
"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from harness.context import diff_transcripts
from harness.tui import has_tui
from harness.tui.widgets import format_event, strip_ansi


def _ctx(sections):
    return "".join(f"## {h}\n{b}\n" for h, b in sections)


class TestSectionDiffs(unittest.TestCase):
    def test_changed_section_pairs_into_unified_diff(self):
        before = _ctx([("user t1", "keep\nchange me\nkeep2"),
                       ("assistant t1", "stable")])
        after = _ctx([("user t1", "keep\nCHANGED entirely and expanded "
                                 "with several more words\nkeep2"),
                      ("assistant t1", "stable")])
        d = diff_transcripts(before, after)
        # Existing keys untouched.
        self.assertIn("recovered", d)
        self.assertEqual(len(d["removed"]), 1)
        self.assertEqual(len(d["added"]), 1)
        secs = d["section_diffs"]
        self.assertEqual(len(secs), 1)
        self.assertEqual(secs[0]["header"], "## user t1")
        lines = secs[0]["lines"]
        self.assertTrue(any(l == "-change me" for l in lines),
                        f"missing - line in {lines}")
        self.assertTrue(any(l.startswith("+CHANGED") for l in lines),
                        f"missing + line in {lines}")

    def test_added_only_section_is_all_plus(self):
        before = _ctx([("user t1", "stable")])
        after = _ctx([("user t1", "stable"),
                      ("assistant t1", "brand new\nlines")])
        secs = diff_transcripts(before, after)["section_diffs"]
        self.assertEqual(len(secs), 1)
        self.assertTrue(all(l.startswith("+") for l in secs[0]["lines"]
                            if not l.startswith("@@")),
                        secs[0]["lines"])

    def test_removed_only_section_is_all_minus(self):
        before = _ctx([("user t1", "stable"),
                       ("assistant t1", "gone\nforever")])
        after = _ctx([("user t1", "stable")])
        secs = diff_transcripts(before, after)["section_diffs"]
        self.assertEqual(len(secs), 1)
        self.assertTrue(all(l.startswith("-") for l in secs[0]["lines"]
                            if not l.startswith("@@")),
                        secs[0]["lines"])

    def test_reorder_without_changes_has_no_diffs(self):
        before = _ctx([("user t1", "a"), ("assistant t1", "b")])
        after = _ctx([("assistant t1", "b"), ("user t1", "a")])
        d = diff_transcripts(before, after)
        self.assertEqual(d["section_diffs"], [])
        self.assertEqual(d["removed"], [])
        self.assertEqual(d["added"], [])

    def test_caps_are_respected(self):
        before = _ctx([(f"user t{i}", "x") for i in range(30)])
        after = _ctx([(f"user t{i}", "y") for i in range(30)])
        secs = diff_transcripts(before, after)["section_diffs"]
        self.assertLessEqual(len(secs), 8)
        total = sum(len(s["lines"]) for s in secs)
        self.assertLessEqual(total, 160)
        for s in secs:
            self.assertLessEqual(len(s["lines"]), 40)


class TestDiffCardFormat(unittest.TestCase):
    def test_minus_lines_red_plus_lines_green(self):
        data = diff_transcripts(
            _ctx([("user t1", "old line")]),
            _ctx([("user t1", "new line")]))
        card = format_event("context-diff", data)
        self.assertIn("\x1b[31m-old line", card)
        self.assertIn("\x1b[32m+new line", card)
        self.assertIn("[context diff:", card)
        # Copy path strips the color back out.
        plain = strip_ansi(card)
        self.assertIn("-old line", plain)
        self.assertIn("+new line", plain)

    def test_minimal_data_still_renders_header(self):
        line = format_event("context-diff", {"recovered": 10,
                                             "removed": [], "added": []})
        self.assertIn("+10", line)


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestDiffCardRouting(unittest.IsolatedAsyncioTestCase):
    async def test_context_diff_lands_in_transcript(self):
        from harness.tui.app import HarnessApp
        from harness.tui.widgets import MessageWidget, TranscriptContainer
        from harness.tui.bridge import TuiBridge
        with tempfile.TemporaryDirectory() as d:
            sdir = Path(d) / "sess"
            sdir.mkdir()
            sess = SimpleNamespace(id="t", dir=sdir, workdir=d)
            loop = SimpleNamespace(approver=SimpleNamespace(
                decide=None, auto_approve=False))
            bridge = TuiBridge()
            app = HarnessApp(loop, sess, bridge)
            async with app.run_test() as pilot:
                data = diff_transcripts(
                    _ctx([("user t1", "old line")]),
                    _ctx([("user t1", "new line")]))
                bridge("context-diff", data)
                app._poll()
                await pilot.pause()
                box = app.query_one("#transcript", TranscriptContainer)
                self.assertEqual(box.message_count, 1)
                widgets = box.message_widgets()
                self.assertEqual(len(widgets), 1)
                w = widgets[0]
                self.assertIsInstance(w, MessageWidget)
                self.assertIn("message-context-diff", w.classes)
                self.assertIn("-old line", w.plain_text)
                self.assertIn("+new line", w.plain_text)
