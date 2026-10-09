"""Red-first: LCARS charm strip (blinkenlights, stardate, data stream)."""
import unittest

from harness.tui.lcars import LCARS, charms_segments


def _flat(segs):
    return "".join(t for t, _, _ in segs)


class TestCharms(unittest.TestCase):
    def test_fits_width(self):
        for w in (40, 80, 120):
            segs = charms_segments(w, tick=0, stardate="SD 41300.0")
            self.assertLessEqual(len(_flat(segs)), w)

    def test_has_pills_and_stardate(self):
        segs = charms_segments(80, tick=0, stardate="SD 41300.0")
        flat = _flat(segs)
        self.assertIn("SD 41300.0", flat)
        self.assertIn("SYS", flat)

    def test_lights_blink_with_tick(self):
        a = charms_segments(80, tick=0, stardate="SD 1")
        b = charms_segments(80, tick=2, stardate="SD 1")
        # some light changes state between ticks
        bgs_a = [bg for _, bg, _ in a]
        bgs_b = [bg for _, bg, _ in b]
        self.assertNotEqual(bgs_a, bgs_b)

    def test_stream_scrolls(self):
        a = charms_segments(80, tick=0, stardate="SD 1")
        b = charms_segments(80, tick=7, stardate="SD 1")
        self.assertNotEqual(_flat(a), _flat(b))

    def test_stream_is_hex(self):
        import re
        segs = charms_segments(120, tick=3, stardate="SD 1")
        flat = _flat(segs)
        # strip pills/stardate, the long hex run remains
        runs = re.findall(r"[0-9A-F]{8,}", flat)
        self.assertTrue(runs, "no hex data stream found")

    def test_deterministic(self):
        a = charms_segments(80, tick=42, stardate="SD 1")
        b = charms_segments(80, tick=42, stardate="SD 1")
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
