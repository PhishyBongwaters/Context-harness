"""Red-first: --dry-run reports what the next turn would cost (S8).

No provider is created, no model calls are made, nothing is written.
"""
import io
import sys
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace

sys.path.insert(0, "tests")
from test_loop import make_session  # noqa: E402

from harness.__main__ import _dry_run


class TestDryRun(unittest.TestCase):
    def test_reports_sections_and_verdict(self):
        s = make_session()
        (s.dir / "history.md").write_text(
            "## user t0001\nhello there\n", encoding="utf-8")
        cfg = SimpleNamespace(budget_hard=100000, budget_soft=80000)
        args = SimpleNamespace(budget_hard=None, budget_soft=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = _dry_run(cfg, args, s)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("session:", out)
        self.assertIn("system prompt:", out)
        self.assertIn("## sat facts", out)
        self.assertIn("## user t0001", out)
        self.assertIn("verdict: fits", out)

    def test_flags_over_budget(self):
        s = make_session()
        (s.dir / "history.md").write_text(
            "## user t0001\n" + ("word " * 5000) + "\n", encoding="utf-8")
        cfg = SimpleNamespace(budget_hard=100, budget_soft=80)
        args = SimpleNamespace(budget_hard=None, budget_soft=None)
        buf = io.StringIO()
        with redirect_stdout(buf):
            _dry_run(cfg, args, s)
        self.assertIn("OVER HARD BUDGET", buf.getvalue())

    def test_writes_nothing(self):
        s = make_session()
        cfg = SimpleNamespace(budget_hard=100000, budget_soft=80000)
        args = SimpleNamespace(budget_hard=None, budget_soft=None)
        before = {p.name for p in s.dir.iterdir()}
        with redirect_stdout(io.StringIO()):
            _dry_run(cfg, args, s)
        after = {p.name for p in s.dir.iterdir()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
