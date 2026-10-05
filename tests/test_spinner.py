import io
import time
import unittest

from harness.spinner import Spinner


class TestSpinner(unittest.TestCase):
    def test_start_stop_lifecycle(self):
        out = io.StringIO()
        sp = Spinner("thinking", out=out, enabled=True)
        sp.start()
        self.assertIsNotNone(sp._thread)
        time.sleep(0.5)
        sp.stop()
        self.assertIsNone(sp._thread)
        self.assertIn("thinking", out.getvalue())

    def test_disabled_noops(self):
        out = io.StringIO()
        sp = Spinner("thinking", out=out, enabled=False)
        sp.start()
        self.assertIsNone(sp._thread)
        sp.stop()  # must not raise
        self.assertEqual(out.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
