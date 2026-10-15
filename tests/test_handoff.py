"""Handoff-per-turn (issue #5): sats/handoff.md lifecycle, validation,
forced handoff rounds, and assembly priority.
"""
import tempfile
import unittest
from pathlib import Path

from harness.assembly import SAT_ORDER, assemble, load_prompt
from harness.context import Budget
from harness.handoff import (HANDOFF_MAX_LINES, HANDOFF_SECTIONS,
                             HANDOFF_TEMPLATE, ensure_handoff_file,
                             handoff_size, parse_handoff, validate_handoff)
from harness.loop import Loop, Session
from harness.providers import MockProvider
from harness.tools import classify_source, source_gate


def make_session():
    d = tempfile.mkdtemp()
    return Session(id="t", dir=Path(d), workdir=d)


def handoff_response(sdir, turn_no):
    """MockProvider script entry: a handoff-compliant agent turn.

    Rewrites just the handoff stamp line (t0000 -> tNNNN); the
    template's four sections stay, which is all the validator needs.
    turn_no is the 1-based run_turn count. Lets existing multi-turn
    tests model the new contract: the agent writes its handoff inside
    the normal turn, so no forced handoff round fires.
    """
    return {"content": None, "tool_calls": [
        {"id": f"handoff-t{turn_no:04d}", "name": "edit",
         "arguments": {
             "path": str(Path(sdir) / "sats" / "handoff.md"),
             "old_text": f"## Handoff -- t{turn_no - 1:04d}",
             "new_text": f"## Handoff -- t{turn_no:04d}"}}]}


def write_handoff(sdir: Path, stamp: str = "t0001",
                  sections: dict | None = None) -> Path:
    p = sdir / "sats" / "handoff.md"
    body = [f"## Handoff -- {stamp}"]
    defaults = {n: f"{n} content" for n in HANDOFF_SECTIONS}
    defaults.update(sections or {})
    for name in HANDOFF_SECTIONS:
        if defaults[name] is not None:
            body += [f"### {name}", defaults[name]]
    p.write_text("# Handoff\n\n" + "\n".join(body) + "\n",
                 encoding="utf-8")
    return p


class TestHandoffFile(unittest.TestCase):
    def test_init_creates_handoff_sat(self):
        s = make_session()
        p = s.dir / "sats" / "handoff.md"
        self.assertTrue(p.is_file())
        parsed = parse_handoff(p.read_text(encoding="utf-8"))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["stamp"], 0)
        self.assertTrue(all(parsed["sections"].values()))

    def test_existing_session_gets_handoff_on_reopen(self):
        s = make_session()
        cur = s.dir / "sats" / "current.md"
        cur.write_text("# Current task\n\nkeep me\n", encoding="utf-8")
        (s.dir / "sats" / "handoff.md").unlink()
        s2 = Session(id="t", dir=s.dir, workdir=s.workdir)
        self.assertTrue((s2.dir / "sats" / "handoff.md").is_file())
        # Idempotent: existing sats are never clobbered.
        self.assertIn("keep me", cur.read_text(encoding="utf-8"))

    def test_handoff_is_sat_gated(self):
        s = make_session()
        p = s.dir / "sats" / "handoff.md"
        self.assertEqual(classify_source(s.dir, str(p)), "sat")
        denied = source_gate(s.dir, "write", str(p), s.workdir)
        self.assertIn("DENIED", denied)
        self.assertIsNone(source_gate(s.dir, "edit", str(p),
                                      s.workdir))


class TestValidate(unittest.TestCase):
    def test_ok(self):
        s = make_session()
        write_handoff(s.dir, stamp="t0003")
        ok, reason = validate_handoff(s.dir, 3)
        self.assertTrue(ok, reason)

    def test_unchanged_handoff_is_legal(self):
        # Freshness, not novelty: "Unchanged" with this turn's stamp
        # passes.
        s = make_session()
        write_handoff(s.dir, stamp="t0002",
                      sections={"Now": "Unchanged -- still waiting on user",
                                "Next": "Unchanged -- still waiting on user",
                                "Blockers": "user has not replied",
                                "Pointers": "sats/current.md"})
        ok, reason = validate_handoff(s.dir, 2)
        self.assertTrue(ok, reason)

    def test_missing_file(self):
        s = make_session()
        (s.dir / "sats" / "handoff.md").unlink()
        ok, reason = validate_handoff(s.dir, 1)
        self.assertFalse(ok)
        self.assertIn("missing", reason)

    def test_stale_stamp(self):
        s = make_session()
        write_handoff(s.dir, stamp="t0002")
        ok, reason = validate_handoff(s.dir, 3)
        self.assertFalse(ok)
        self.assertIn("t0002", reason)
        self.assertIn("t0003", reason)

    def test_missing_section(self):
        s = make_session()
        write_handoff(s.dir, stamp="t0001",
                      sections={"Blockers": None})
        ok, reason = validate_handoff(s.dir, 1)
        self.assertFalse(ok)
        self.assertIn("### Blockers", reason)

    def test_over_cap(self):
        s = make_session()
        filler = "\n".join(f"line {i}" for i in
                           range(HANDOFF_MAX_LINES + 10))
        write_handoff(s.dir, stamp="t0001",
                      sections={"Now": filler})
        ok, reason = validate_handoff(s.dir, 1)
        self.assertFalse(ok)
        self.assertIn("cap", reason)

    def test_ensure_recreates_missing_file(self):
        s = make_session()
        (s.dir / "sats" / "handoff.md").unlink()
        ensure_handoff_file(s.dir)
        self.assertTrue((s.dir / "sats" / "handoff.md").is_file())

    def test_handoff_size_canary(self):
        s = make_session()
        lines, tokens = handoff_size(s.dir)
        self.assertGreater(lines, 0)
        self.assertGreater(tokens, 0)


class TestAssembly(unittest.TestCase):
    def test_handoff_assembles_first(self):
        s = make_session()
        self.assertEqual(SAT_ORDER[0], "handoff")
        out = assemble(s.dir)
        self.assertTrue(out.startswith("## sat handoff\n"),
                        f"handoff not first: {out[:60]!r}")

    def test_prompt_carries_handoff_guidance(self):
        s = make_session()
        out = load_prompt(s.dir, ctx_path="x", hard=1000, soft=800,
                          turn_no=7)
        self.assertIn("HANDOFF (every turn ends with one", out)
        self.assertIn("## Handoff -- t0007", out)

    def test_old_prompt_gets_handoff_addendum(self):
        s = make_session()
        (s.dir / "prompt.md").write_text("old prompt, no marker",
                                         encoding="utf-8")
        out = load_prompt(s.dir, ctx_path="x", hard=1000, soft=800,
                          turn_no=2)
        self.assertIn("HANDOFF (every turn ends with one", out)
        self.assertIn("## Handoff -- t0002", out)


class TestEnforcement(unittest.TestCase):
    def _loop(self, script):
        events = []
        loop = Loop(MockProvider(script),
                    Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append(k))
        return loop, events

    def test_forced_handoff_round_writes_handoff(self):
        s = make_session()
        handoff = str(s.dir / "sats" / "handoff.md")
        script = [
            {"content": "done"},
            {"content": None, "tool_calls": [
                {"id": "h1", "name": "edit",
                 "arguments": {
                     "path": handoff,
                     "old_text": "## Handoff -- t0000",
                     "new_text": ("## Handoff -- t0001\n### Now\n"
                                  "did the thing\n### Next\n"
                                  "do the next thing\n### Blockers\n"
                                  "none\n### Pointers\n"
                                  "sats/current.md")}}]},
        ]
        loop, events = self._loop(script)
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "done")
        self.assertIn("handoff", events)
        self.assertNotIn("handoff-invalid", events)
        ok, reason = validate_handoff(s.dir, 1)
        self.assertTrue(ok, reason)
        # The forced round is edit-only: no wandering off to do work.
        self.assertEqual(loop.provider.calls[1]["tools"], ["edit"])

    def test_handoff_failure_is_loud_not_blocking(self):
        # Script exhausts on the forced rounds: provider errors are
        # caught, the turn still closes, and the failure is emitted.
        loop, events = self._loop([{"content": "done"}])
        s = make_session()
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "done")
        self.assertIn("handoff-invalid", events)

    def test_fresh_handoff_needs_no_forced_round(self):
        s = make_session()
        write_handoff(s.dir, stamp="t0001",
                      sections={"Now": "working",
                                "Next": "more work",
                                "Blockers": "none",
                                "Pointers": "sats/current.md"})
        loop, events = self._loop([{"content": "done"}])
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "done")
        self.assertIn("handoff", events)
        # No second provider call: no forced round was needed.
        self.assertEqual(len(loop.provider.calls), 1)


if __name__ == "__main__":
    unittest.main()
