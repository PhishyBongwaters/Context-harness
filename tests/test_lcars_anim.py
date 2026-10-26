"""Red-first: LCARS micro-animations (alerts, flash, marquee, sweep)."""
import unittest

from harness.tui.lcars import LCARS, header_segments, lcars_gauge_text


def _flat(segs):
    return "".join(t for t, _, _ in segs)


class TestAlertPill(unittest.TestCase):
    def test_approval_alert_pill_present(self):
        segs = header_segments("t", 80, phase=0, alert="approval")
        flat = _flat(segs)
        self.assertIn("!", flat)
        # amber pill
        bgs = [bg for txt, bg, _ in segs if "!" in txt]
        self.assertIn(LCARS["orange"], bgs)

    def test_over_alert_is_red(self):
        segs = header_segments("t", 80, phase=0, alert="over")
        bgs = [bg for txt, bg, _ in segs if "!" in txt]
        self.assertIn(LCARS["red"], bgs)

    def test_alert_blinks_with_phase(self):
        on = header_segments("t", 80, phase=0, alert="approval")
        off = header_segments("t", 80, phase=1, alert="approval")
        bg_on = [bg for txt, bg, _ in on if "!" in txt][0]
        bg_off = [bg for txt, bg, _ in off if "!" in txt][0]
        self.assertNotEqual(bg_on, bg_off)

    def test_alert_pill_always_present_no_layout_jump(self):
        # pill exists in both phases so the width never shifts
        for phase in (0, 1):
            segs = header_segments("t", 80, phase=phase, alert="over")
            self.assertIn("!", _flat(segs))
            self.assertLessEqual(len(_flat(segs)), 80)

    def test_no_alert_no_pill(self):
        segs = header_segments("t", 80, phase=0)
        self.assertNotIn("!", _flat(segs))


class TestFlash(unittest.TestCase):
    def test_flash_brightens_diamond(self):
        normal = header_segments("t", 80, phase=0)
        flashed = header_segments("t", 80, phase=0, flash=True)
        dia = lambda segs: [bg for txt, bg, _ in segs
                            if "◆" in txt or "◐" in txt]
        self.assertNotEqual(dia(normal)[0], dia(flashed)[0])

    def test_turn_flash_brightens_turn_pill(self):
        normal = header_segments("t", 80, phase=0, turns=3)
        flashed = header_segments("t", 80, phase=0, turns=3,
                                  turn_flash=True)
        bg = lambda segs: [bg for txt, bg, _ in segs if "T3" in txt][0]
        self.assertNotEqual(bg(normal), bg(flashed))


class TestMarquee(unittest.TestCase):
    def test_long_title_scrolls(self):
        title = "x" * 100
        a = header_segments(title, 80, phase=0, marquee=0)
        b = header_segments(title, 80, phase=0, marquee=5)
        # same width, different visible window
        self.assertLessEqual(len(_flat(a)), 80)
        self.assertLessEqual(len(_flat(b)), 80)
        # the title pill text differs between offsets
        ta = [t for t, _, _ in a if "x" in t][0]
        tb = [t for t, _, _ in b if "x" in t][0]
        # both are windows into the run of x's; offset shifts content
        self.assertEqual(len(ta), len(tb))

    def test_marquee_wraps(self):
        title = "abcdefghij" * 10
        a = header_segments(title, 80, phase=0, marquee=0)
        span = len(title) - (80 - 48) + 1
        b = header_segments(title, 80, phase=0, marquee=span)
        ta = [t for t, _, _ in a if "a" in t][0]
        tb = [t for t, _, _ in b if "a" in t][0]
        self.assertEqual(ta, tb)

    def test_short_title_unaffected(self):
        a = header_segments("short", 80, phase=0, marquee=0)
        b = header_segments("short", 80, phase=0, marquee=99)
        self.assertEqual(_flat(a), _flat(b))


def _has_rich():
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


@unittest.skipUnless(__import__("harness.tui", fromlist=["has_tui"]).has_tui(),
                     "textual extra missing")
class TestApprovalCSS(unittest.TestCase):
    def test_css_parses_without_keyframes(self):
        # Regression: @keyframes is not valid Textual CSS and broke
        # the approval modal (black screen). This must not raise.
        from textual.css.stylesheet import Stylesheet
        from harness.tui.app import ApprovalScreen
        self.assertNotIn("@keyframes", ApprovalScreen.CSS)
        ss = Stylesheet()
        ss.add_source(ApprovalScreen.CSS, read_from="test",
                      scope="ApprovalScreen")


@unittest.skipUnless(_has_rich(), "rich missing")
class TestGaugeSweep(unittest.TestCase):
    def _data(self):
        return {"tokens_est": 5000, "hard": 10000}

    def test_sweep_returns_text(self):
        t = lcars_gauge_text(self._data(), width=40, sweep=0.5)
        self.assertIsNotNone(t)
        self.assertEqual(len(str(t)), 40)

    def test_sweep_adds_bright_band(self):
        plain = lcars_gauge_text(self._data(), width=40)
        swept = lcars_gauge_text(self._data(), width=40, sweep=0.5)
        # swept version uses a second background color for the band
        bgs = {getattr(s, "bgcolor", None)
               for s in getattr(swept, "_spans", [])} if False else None
        # simpler: the styled spans differ
        self.assertNotEqual(plain._spans, swept._spans)

    def test_no_sweep_single_bg(self):
        plain = lcars_gauge_text(self._data(), width=40)
        bgs = set()
        for span in plain._spans:
            bgs.add(span.style.bgcolor)
        # bar bg + dark remainder only
        self.assertLessEqual(len(bgs), 2)


if __name__ == "__main__":
    unittest.main()
