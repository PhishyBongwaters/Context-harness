"""Red-first: charm strip shows running subagent."""
import unittest

from harness.tui.lcars import charms_segments


def _flat(segs):
    return "".join(t for t, _, _ in segs)


class TestCharmsSubagent(unittest.TestCase):
    def test_subagent_replaces_stream(self):
        segs = charms_segments(80, tick=0, stardate="SD 1",
                               subagent="Review the auth module")
        flat = _flat(segs)
        self.assertIn("SUBAGENT", flat)
        self.assertIn("Review the auth module", flat)
        self.assertLessEqual(len(flat), 80)

    def test_no_subagent_no_label(self):
        segs = charms_segments(80, tick=0, stardate="SD 1")
        self.assertNotIn("SUBAGENT", _flat(segs))

    def test_long_task_truncates(self):
        segs = charms_segments(80, tick=0, stardate="SD 1",
                               subagent="x" * 200)
        self.assertLessEqual(len(_flat(segs)), 80)

    def test_narrow_still_fits(self):
        segs = charms_segments(40, tick=0, stardate="SD 1",
                               subagent="Do things")
        self.assertLessEqual(len(_flat(segs)), 40)


if __name__ == "__main__":
    unittest.main()
