"""End-goal stop handoff: goal done means chat, not tracking.

After every goal is done, handoff tracking stops, and the session
treats further turns like a chat until a new goal is activated.
Sats that were going into context keep going into context: only
handoff tracking stops, assembly is unchanged. A goal completed
during a turn still gets its final handoff; a new goal activated
during a turn resumes tracking.
"""
import tempfile
import unittest
from pathlib import Path

from harness.assembly import assemble
from harness.context import Budget
from harness.handoff import HANDOFF_SECTIONS, validate_handoff
from harness.loop import Loop, Session
from harness.providers import MockProvider
from harness.session import goals_done, init_layout


def make_session():
    d = tempfile.mkdtemp()
    return Session(id="t", dir=Path(d), workdir=d)


def write_goals(sdir, lines):
    (Path(sdir) / "sats" / "goals.md").write_text(
        "# Goals\n\n" + "\n".join(lines) + "\n", encoding="utf-8")


def write_handoff(sdir, stamp="t0001"):
    p = Path(sdir) / "sats" / "handoff.md"
    body = [f"## Handoff -- {stamp}"]
    for name in HANDOFF_SECTIONS:
        body += [f"### {name}", f"{name} findings"]
    p.write_text("# Handoff\n\n" + "\n".join(body) + "\n",
                 encoding="utf-8")
    return p


class TestGoalsDone(unittest.TestCase):
    def test_done_only_is_done(self):
        s = make_session()
        write_goals(s.dir, ["- [done] Trace buffered write"])
        self.assertTrue(goals_done(s.dir))

    def test_active_present_is_not_done(self):
        s = make_session()
        write_goals(s.dir, ["- [done] Old", "- [active] New"])
        self.assertFalse(goals_done(s.dir))

    def test_no_goals_is_not_done(self):
        s = make_session()
        self.assertFalse(goals_done(s.dir))

    def test_template_only_is_not_done(self):
        s = make_session()
        body = (s.dir / "sats" / "goals.md").read_text(encoding="utf-8")
        self.assertIn("[active]", body)
        self.assertIn("[done]", body)
        self.assertFalse(goals_done(s.dir))

    def test_missing_goals_file_is_not_done(self):
        s = make_session()
        (s.dir / "sats" / "goals.md").unlink()
        self.assertFalse(goals_done(s.dir))


class TestSatsStayInContext(unittest.TestCase):
    def test_done_still_assembles_all_sats(self):
        s = make_session()
        write_goals(s.dir, ["- [done] Trace buffered write"])
        write_handoff(s.dir, stamp="t0001")
        (s.dir / "sats" / "facts.md").write_text(
            "# Facts\n\nOTHER FACT\n", encoding="utf-8")
        (s.dir / "sats" / "current.md").write_text(
            "# Current task\n\nOTHER CURRENT\n", encoding="utf-8")
        out = assemble(s.dir)
        self.assertIn("findings", out)
        self.assertIn("OTHER FACT", out)
        self.assertIn("OTHER CURRENT", out)

    def test_assembly_same_done_and_active(self):
        s = make_session()
        write_handoff(s.dir, stamp="t0001")
        (s.dir / "sats" / "facts.md").write_text(
            "# Facts\n\nOTHER FACT\n", encoding="utf-8")
        write_goals(s.dir, ["- [done] Trace buffered write"])
        done_out = assemble(s.dir)
        write_goals(s.dir, ["- [active] Trace direct write"])
        active_out = assemble(s.dir)
        self.assertIn("OTHER FACT", done_out)
        self.assertIn("OTHER FACT", active_out)


class TestEndGoalHandoffSkip(unittest.TestCase):
    def _loop(self):
        events = []
        loop = Loop(MockProvider([{"content": "chat reply"}]),
                    Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append(k))
        return loop, events

    def test_done_at_start_and_end_skips_handoff(self):
        s = make_session()
        write_goals(s.dir, ["- [done] Trace buffered write"])
        # Stale handoff: would normally force handoff rounds.
        write_handoff(s.dir, stamp="t0000")
        before = (s.dir / "sats" / "handoff.md").read_text(
            encoding="utf-8")
        loop, events = self._loop()
        out = loop.run_turn(s, "keep chatting about the trace")
        self.assertEqual(out, "chat reply")
        # No forced handoff round, no handoff validation event.
        self.assertEqual(len(loop.provider.calls), 1)
        self.assertNotIn("handoff", events)
        self.assertNotIn("handoff-invalid", events)
        # Handoff frozen, not rewritten.
        after = (s.dir / "sats" / "handoff.md").read_text(
            encoding="utf-8")
        self.assertEqual(after, before)

    def test_completion_turn_still_gets_final_handoff(self):
        s = make_session()
        write_goals(s.dir, ["- [active] Trace buffered write"])
        write_handoff(s.dir, stamp="t0001",
                      )
        # Model marks the goal done during the turn, then replies.
        goals_path = str(s.dir / "sats" / "goals.md")
        script = [
            {"content": None, "tool_calls": [
                {"id": "g1", "name": "edit",
                 "arguments": {
                     "path": goals_path,
                     "old_text": "- [active] Trace buffered write",
                     "new_text": "- [done] Trace buffered write"}}]},
            {"content": "done"},
        ]
        events = []
        loop = Loop(MockProvider(script),
                    Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append(k))
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "done")
        self.assertTrue(goals_done(s.dir))
        self.assertIn("handoff", events)
        ok, reason = validate_handoff(s.dir, 1)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
