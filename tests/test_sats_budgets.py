"""Turn-end sats budget enforcement (dedupe, checkbox repair, caps)."""
import tempfile
import unittest
from pathlib import Path

from harness.context import Budget
from harness.loop import Loop, Session
from harness.providers import MockProvider
from harness.session import enforce_sats_budgets, init_layout


def fresh():
    d = Path(tempfile.mkdtemp())
    init_layout(d, "PROMPT")
    return d


def make_session(workdir=None):
    d = tempfile.mkdtemp()
    return Session(id="t", dir=Path(d), workdir=workdir or d)


class TestEnforceSatsBudgets(unittest.TestCase):
    def test_dedupes_exact_lines_keeps_first(self):
        d = fresh()
        (d / "sats" / "facts.md").write_text(
            "# Facts\n\nAlpha\nBeta\nAlpha\nGamma\nBeta\n",
            encoding="utf-8")
        report = enforce_sats_budgets(d, 8000)
        self.assertIn("facts.md", report)
        self.assertEqual(report["facts.md"]["dropped_lines"], 2)
        self.assertEqual(
            (d / "sats" / "facts.md").read_text(encoding="utf-8"),
            "# Facts\n\nAlpha\nBeta\nGamma\n")
        self.assertLess(report["facts.md"]["tokens_after"],
                        report["facts.md"]["tokens_before"])

    def test_repairs_goal_checkboxes(self):
        d = fresh()
        (d / "sats" / "goals.md").write_text(
            "# Goals\n\n- [ ] First task\n- [x] Second task\n"
            "- [X] Third task\n- [active] Kept active\n"
            "- [done] Kept done\n",
            encoding="utf-8")
        report = enforce_sats_budgets(d, 8000)
        self.assertEqual(report["goals.md"]["repaired"], 3)
        body = (d / "sats" / "goals.md").read_text(encoding="utf-8")
        self.assertIn("- [active] First task", body)
        self.assertIn("- [done] Second task", body)
        self.assertIn("- [done] Third task", body)
        self.assertIn("- [active] Kept active", body)
        self.assertIn("- [done] Kept done", body)

    def test_reports_over_cap_without_changing(self):
        # Over cap is a loud signal, not a deletion: the file is
        # reported and left byte-identical for the prune turn to
        # curate. (The bloat-gate redesign settled that sats are
        # model-curated memory; test_sats_only_bloat_does_not_
        # stall_across_turns pins the preservation.)
        d = fresh()
        lines = ["# Facts"] + [
            f"Fact {i} about the thing being traced here"
            for i in range(30)]
        original = "\n".join(lines) + "\n"
        (d / "sats" / "facts.md").write_text(original, encoding="utf-8")
        report = enforce_sats_budgets(d, 50)
        info = report["facts.md"]
        self.assertTrue(info["over_cap"])
        self.assertFalse(info["changed"])
        self.assertEqual(
            (d / "sats" / "facts.md").read_text(encoding="utf-8"),
            original)

    def test_over_cap_goals_reported_not_cut(self):
        # Active goals are never cut to fit a cap: too many active
        # goals is a real signal, and the file stays as-is.
        d = fresh()
        lines = ["# Goals", ""] + [
            f"- [active] Goal number {i} with some words here"
            for i in range(5)]
        original = "\n".join(lines) + "\n"
        (d / "sats" / "goals.md").write_text(original, encoding="utf-8")
        report = enforce_sats_budgets(d, 30)
        info = report["goals.md"]
        self.assertTrue(info["over_cap"])
        self.assertFalse(info["changed"])
        body = (d / "sats" / "goals.md").read_text(encoding="utf-8")
        self.assertEqual(body, original)
        for i in range(5):
            self.assertIn(f"- [active] Goal number {i}", body)

    def test_skips_handoff(self):
        d = fresh()
        bloated = "# Handoff\n\n" + "Same line\n" * 50
        (d / "sats" / "handoff.md").write_text(bloated, encoding="utf-8")
        report = enforce_sats_budgets(d, 30)
        self.assertNotIn("handoff.md", report)
        self.assertEqual(
            (d / "sats" / "handoff.md").read_text(encoding="utf-8"),
            bloated)

    def test_quiet_when_healthy(self):
        d = fresh()
        before = {
            p.name: p.read_text(encoding="utf-8")
            for p in (d / "sats").glob("*.md")}
        self.assertEqual(enforce_sats_budgets(d, 8000), {})
        after = {
            p.name: p.read_text(encoding="utf-8")
            for p in (d / "sats").glob("*.md")}
        self.assertEqual(before, after)


class TestEnforceLoopIntegration(unittest.TestCase):
    def test_helper_backs_up_and_emits(self):
        # A changed file (dupes deduped) gets a pre-enforcement
        # backup plus a "sats-cap" event.
        d = fresh()
        (d / "sats" / "facts.md").write_text(
            "# Facts\n\nAlpha\nBeta\nAlpha\n", encoding="utf-8")
        events = []
        loop = Loop(MockProvider([]), Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append((k, v)))
        session = Session(id="t", dir=d, workdir=str(d))
        loop._enforce_sats_budgets(session)
        body = (d / "sats" / "facts.md").read_text(encoding="utf-8")
        self.assertEqual(body, "# Facts\n\nAlpha\nBeta\n")
        backups = list((d / "sats").glob("facts.pre-sats-cap-*.bak"))
        self.assertEqual(len(backups), 1)
        self.assertIn("Alpha\nBeta\nAlpha",
                      backups[0].read_text(encoding="utf-8"))
        caps = [v for k, v in events if k == "sats-cap"]
        self.assertEqual(len(caps), 1)
        self.assertEqual(caps[0]["file"], "facts.md")
        self.assertEqual(caps[0]["dropped_lines"], 1)
        self.assertTrue(caps[0]["changed"])

    def test_helper_reports_over_cap_without_backup(self):
        # An over-cap file with nothing to dedupe or repair is
        # reported but untouched: an event, no rewrite, no backup.
        d = fresh()
        lines = ["# Facts"] + [
            f"Fact {i} about the thing being traced here"
            for i in range(30)]
        original = "\n".join(lines) + "\n"
        (d / "sats" / "facts.md").write_text(original, encoding="utf-8")
        events = []
        loop = Loop(MockProvider([]), Budget(hard=100000, soft=80000),
                    sats_cap_tokens=50,
                    on_event=lambda k, v: events.append((k, v)))
        session = Session(id="t", dir=d, workdir=str(d))
        loop._enforce_sats_budgets(session)
        self.assertEqual(
            (d / "sats" / "facts.md").read_text(encoding="utf-8"),
            original)
        self.assertEqual(list((d / "sats").glob("*.bak")), [])
        caps = [v for k, v in events if k == "sats-cap"]
        self.assertEqual(len(caps), 1)
        self.assertTrue(caps[0]["over_cap"])
        self.assertFalse(caps[0]["changed"])

    def test_turn_end_enforces(self):
        # End to end: a model turn that writes duplicate lines into
        # a sats file ends the turn with the file deduped, in code.
        # Mirrors test_run_turn_sats_edit_redirects: a workdir-
        # anchored sats path redirects to the session file.
        workdir = tempfile.mkdtemp()
        s = make_session(workdir=workdir)
        target = s.dir / "sats" / "facts.md"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# Facts\n\nAlpha line\n", encoding="utf-8")
        script = [
            {"content": None, "tool_calls": [
                {"id": "e1", "name": "edit",
                 "arguments": {
                     "path": str(Path(workdir) / "sats" / "facts.md"),
                     "old_text": "Alpha line",
                     "new_text": "Alpha line\nAlpha line\nAlpha line"}}]},
            {"content": "done"},
        ]
        loop = Loop(MockProvider(script),
                    Budget(hard=100000, soft=80000))
        self.assertEqual(loop.run_turn(s, "hi"), "done")
        body = target.read_text(encoding="utf-8")
        self.assertEqual(body.count("Alpha line"), 1)


if __name__ == "__main__":
    unittest.main()
