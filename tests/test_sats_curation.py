"""Forced sats-curation rounds at turn end (fix for d8ada47 regime).

The handoff renders from sats state, so a long turn that leaves the
sats at template text renders a placeholder handoff and the next turn
remembers nothing. Mirrors the goals enforcement: a long turn
(6+ tool calls) with uncurated sats triggers forced edit-only rounds
before the handoff render; persistent failure is loud, never blocking.
"""
import tempfile
import unittest
from pathlib import Path

from harness.context import Budget
from harness.loop import Loop, Session
from harness.providers import MockProvider
from harness.session import init_layout, sats_curated


def fresh():
    d = Path(tempfile.mkdtemp())
    init_layout(d, "PROMPT")
    return d


class TestSatsCurated(unittest.TestCase):
    def test_template_only_is_not_curated(self):
        d = fresh()
        self.assertFalse(sats_curated(d))

    def test_one_fact_line_is_curated(self):
        d = fresh()
        (d / "sats" / "facts.md").write_text(
            "# Facts\n\nKernel trace shows submit_bio calls block layer.\n",
            encoding="utf-8")
        self.assertTrue(sats_curated(d))

    def test_one_current_line_is_curated(self):
        d = fresh()
        (d / "sats" / "current.md").write_text(
            "# Current task\n\nTracing kernel block layer submit_bio.\n",
            encoding="utf-8")
        self.assertTrue(sats_curated(d))


class TestEnsureSats(unittest.TestCase):
    def test_curated_returns_immediately_without_provider_calls(self):
        d = fresh()
        (d / "sats" / "facts.md").write_text(
            "# Facts\n\nKernel trace shows submit_bio calls block layer.\n",
            encoding="utf-8")
        prov = MockProvider([])
        loop = Loop(prov, Budget(hard=100000, soft=80000))
        session = Session(id="t", dir=d, workdir=str(d))
        self.assertTrue(loop._ensure_sats(session, 1, "sys", []))
        self.assertEqual(prov.calls, [])

    def test_fix_round_curates(self):
        d = fresh()
        facts = d / "sats" / "facts.md"
        prov = MockProvider([
            {"content": None, "tool_calls": [
                {"id": "s1", "name": "edit",
                 "arguments": {
                     "path": str(facts),
                     "old_text": ("Discovered truths about the world, "
                                  "one line each."),
                     "new_text": ("Kernel trace shows submit_bio calls "
                                  "block layer.")}}]},
        ])
        loop = Loop(prov, Budget(hard=100000, soft=80000))
        session = Session(id="t", dir=d, workdir=str(d))
        self.assertTrue(loop._ensure_sats(session, 1, "sys", []))
        body = facts.read_text(encoding="utf-8")
        self.assertIn("Kernel trace shows submit_bio calls block layer.",
                      body)

    def test_fail_loud_after_rounds(self):
        d = fresh()
        events = []
        prov = MockProvider([{"content": "ignoring"},
                             {"content": "ignoring"}])
        loop = Loop(prov, Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append((k, v)))
        session = Session(id="t", dir=d, workdir=str(d))
        self.assertFalse(loop._ensure_sats(session, 1, "sys", []))
        bad = [v for k, v in events if k == "sats-invalid"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["turn"], 1)

    def test_check_only_makes_no_calls(self):
        d = fresh()
        events = []
        prov = MockProvider([])
        loop = Loop(prov, Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append((k, v)))
        session = Session(id="t", dir=d, workdir=str(d))
        self.assertFalse(loop._ensure_sats(session, 1, "sys", [],
                                           allow_model_calls=False))
        self.assertEqual(prov.calls, [])
        self.assertTrue(any(k == "sats-invalid" for k, _ in events))


class TestTurnEndSatsCuration(unittest.TestCase):
    def test_long_turn_curates_before_handoff(self):
        d = fresh()
        current = d / "sats" / "current.md"
        script = [
            {"content": None, "tool_calls": [
                {"id": f"c{i}", "name": "exec",
                 "arguments": {"command": f"echo tool{i}"}}
                for i in range(6)]},
            {"content": "done"},
            {"content": None, "tool_calls": [
                {"id": "s1", "name": "edit",
                 "arguments": {
                     "path": str(current),
                     "old_text": ("What you are actively working on right "
                                  "now, one line. Update it when the task "
                                  "changes or completes."),
                     "new_text": ("Tracing kernel block layer "
                                  "submit_bio.")}}]},
        ]
        loop = Loop(MockProvider(script),
                    Budget(hard=100000, soft=80000))
        session = Session(id="t", dir=d, workdir=str(d))
        self.assertEqual(loop.run_turn(session, "trace it"), "done")
        body = current.read_text(encoding="utf-8")
        self.assertIn("Tracing kernel block layer submit_bio.", body)
        handoff = (d / "sats" / "handoff.md").read_text(encoding="utf-8")
        self.assertIn("Tracing kernel block layer submit_bio.", handoff)
        self.assertNotIn("(nothing recorded in sats/current.md)", handoff)

    def test_short_turn_skips_sats_rounds(self):
        d = fresh()
        events = []
        loop = Loop(MockProvider([{"content": "done"}]),
                    Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append(k))
        session = Session(id="t", dir=d, workdir=str(d))
        self.assertEqual(loop.run_turn(session, "hi"), "done")
        self.assertNotIn("sats-invalid", events)


if __name__ == "__main__":
    unittest.main()
