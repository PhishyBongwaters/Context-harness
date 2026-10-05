import tempfile
import unittest
from pathlib import Path

from harness.context import (Budget, ContextFile, count_tokens,
                               sanitize_assistant_content)


class TestBudget(unittest.TestCase):
    def test_thresholds(self):
        b = Budget(hard=100, soft=80)
        self.assertEqual(b.status(10), "ok")
        self.assertEqual(b.status(79), "ok")
        self.assertEqual(b.status(80), "warn")
        self.assertEqual(b.status(99), "warn")
        self.assertEqual(b.status(100), "over")
        self.assertEqual(b.status(500), "over")

    def test_meter_line(self):
        line = Budget(hard=1000, soft=800).meter_line(500)
        self.assertIn("500/1,000", line)


class TestCountTokens(unittest.TestCase):
    def test_positive(self):
        self.assertGreater(count_tokens("hello world"), 0)
        self.assertGreater(count_tokens("x" * 1000),
                           count_tokens("x"))


class TestContextFile(unittest.TestCase):
    def test_creates_default_when_missing(self):
        with tempfile.TemporaryDirectory() as d:
            cf = ContextFile(Path(d) / "sub" / "context.md")
            text = cf.load()
            self.assertIn("live context", text)
            self.assertTrue((Path(d) / "sub" / "context.md").exists())

    def test_append(self):
        with tempfile.TemporaryDirectory() as d:
            cf = ContextFile(Path(d) / "context.md")
            cf.save("## user\nhello\n")
            cf.append("## user\nworld\n")
            text = cf.load()
            self.assertIn("hello", text)
            self.assertIn("world", text)

    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            cf = ContextFile(Path(d) / "context.md")
            cf.save("hello")
            self.assertEqual(cf.load(), "hello")
            self.assertGreater(cf.tokens(), 0)


if __name__ == "__main__":
    unittest.main()
