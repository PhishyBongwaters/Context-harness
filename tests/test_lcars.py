"""LCARS chrome: palette, bar-segment models, binding pills.

Widget classes need the textual extra and skip cleanly without it;
everything else is stdlib-only and fully tested here.
"""
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from harness.tui import has_tui
from harness.tui import lcars
from harness.tui.lcars import (_binding_pills, _dim, LCARS,
                               footer_segments, header_segments)


class TestPalette(unittest.TestCase):
    def test_hex_format(self):
        self.assertGreaterEqual(len(LCARS), 6)
        for name, value in LCARS.items():
            self.assertRegex(value, r"^#[0-9A-Fa-f]{6}$",
                             f"{name} is not a hex color")

    def test_dim_darkens(self):
        dimmed = _dim(LCARS["orange"])
        self.assertRegex(dimmed, r"^#[0-9A-Fa-f]{6}$")
        ro, go, bo = (int(LCARS["orange"][i:i + 2], 16) for i in (1, 3, 5))
        rd, gd, bd = (int(dimmed[i:i + 2], 16) for i in (1, 3, 5))
        self.assertLessEqual(rd, ro)
        self.assertLessEqual(gd, go)
        self.assertLessEqual(bd, bo)
        self.assertTrue(rd < ro or gd < go or bd < bo)


class TestHeaderSegments(unittest.TestCase):
    def test_structure_and_width(self):
        segs = header_segments("sess-1 openai/gpt", 80, phase=0)
        self.assertTrue(segs)
        # (text, bg, fg) triples
        for text, bg, fg in segs:
            self.assertIsInstance(text, str)
            self.assertRegex(bg, r"^#[0-9A-Fa-f]{6}$")
        # first pill is the LCARS brand
        self.assertIn("LCARS", segs[0][0])
        # title shows up
        flat = "".join(t for t, _, _ in segs)
        self.assertIn("sess-1", flat)
        # fits the width
        self.assertLessEqual(len(flat), 80)

    def test_title_truncates_on_narrow(self):
        segs = header_segments("x" * 200, 40, phase=0)
        flat = "".join(t for t, _, _ in segs)
        self.assertLessEqual(len(flat), 40)

    def test_phase_pulses_accent(self):
        a = header_segments("t", 80, phase=0)
        b = header_segments("t", 80, phase=1)
        bgs_a = [bg for _, bg, _ in a]
        bgs_b = [bg for _, bg, _ in b]
        # same layout, but at least one accent shade differs
        self.assertEqual([t for t, _, _ in a], [t for t, _, _ in b])
        self.assertNotEqual(bgs_a, bgs_b)


class TestFooterSegments(unittest.TestCase):
    def test_pills_from_bindings(self):
        pills = _binding_pills([("ctrl+d", "toggle_debug", "Debug tail"),
                                ("ctrl+s", "open_sessions", "Sessions")])
        self.assertEqual(pills, [("ctrl+d", "Debug tail", "toggle_debug"),
                                 ("ctrl+s", "Sessions", "open_sessions")])

    def test_hidden_and_empty_skipped(self):
        pills = _binding_pills([
            ("ctrl+c", "copy_selection", "Copy selection", False),
            ("ctrl+x", "noop", ""),
        ])
        self.assertEqual(pills, [])

    def test_binding_objects(self):
        b = SimpleNamespace(key="ctrl+o", action="pick_provider",
                            description="Provider/model", show=True)
        self.assertEqual(_binding_pills([b]),
                         [("ctrl+o", "Provider/model", "pick_provider")])

    def test_segments_colored(self):
        segs = footer_segments([("ctrl+d", "Debug tail"),
                                ("ctrl+s", "Sessions")], 80)
        flat = "".join(t for t, _, _ in segs)
        self.assertIn("ctrl+d", flat)
        self.assertIn("Sessions", flat)
        # every pill carries a real bg color
        pill_bgs = [bg for t, bg, _ in segs if t.strip()]
        self.assertTrue(pill_bgs)
        for bg in pill_bgs:
            self.assertRegex(bg, r"^#[0-9A-Fa-f]{6}$")


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestWidgets(unittest.TestCase):
    def test_header_footer_classes_exist(self):
        self.assertTrue(hasattr(lcars, "LcarsHeader"))
        self.assertTrue(hasattr(lcars, "LcarsFooter"))
        self.assertTrue(hasattr(lcars, "LcarsPill"))


@unittest.skipUnless(has_tui(), "textual extra missing")
class TestClickablePills(unittest.IsolatedAsyncioTestCase):
    """Footer pills are real buttons: clicking one runs the app action."""

    async def test_click_pill_runs_action(self):
        from textual.app import App, ComposeResult
        from harness.tui.lcars import LcarsFooter

        fired = []

        class TA(App):
            def compose(self) -> ComposeResult:
                yield LcarsFooter(
                    pills=[("ctrl+d", "Debug", "toggle_debug")],
                    id="lcars-footer")

            def action_toggle_debug(self):
                fired.append("toggle_debug")

        app = TA()
        async with app.run_test() as pilot:
            await pilot.pause()
            pills = list(app.query("LcarsPill"))
            self.assertEqual(len(pills), 1)
            await pilot.click(pills[0])
            await pilot.pause()
            self.assertEqual(fired, ["toggle_debug"])


if __name__ == "__main__":
    unittest.main()
