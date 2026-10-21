"""Handoff-per-turn (issue #5): sats/handoff.md lifecycle, validation,
forced handoff rounds, and assembly priority.
"""
import tempfile
import unittest
from pathlib import Path

from harness.assembly import SAT_ORDER, assemble, load_prompt
from harness.context import Budget
from harness.handoff import (HANDOFF_MAX_LINES, HANDOFF_SECTIONS,
                             HANDOFF_TEMPLATE, HandoffLedger,
                             ensure_handoff_file, handoff_size,
                             is_over_cap, parse_handoff, trim_handoff,
                             validate_handoff)
from harness.loop import PRUNE_SYSTEM, Loop, Session
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

    def test_empty_section_fails(self):
        s = make_session()
        write_handoff(s.dir, stamp="t0001", sections={"Next": ""})
        ok, reason = validate_handoff(s.dir, 1)
        self.assertFalse(ok)
        self.assertIn("### Next", reason)
        self.assertIn("empty", reason)

    def test_next_missing_sats_file_fails(self):
        s = make_session()
        write_handoff(s.dir, stamp="t0001",
                      sections={"Next": "then read sats/nope.md"})
        ok, reason = validate_handoff(s.dir, 1)
        self.assertFalse(ok)
        self.assertIn("sats/nope.md", reason)

    def test_next_existing_sats_file_passes(self):
        s = make_session()
        write_handoff(s.dir, stamp="t0001",
                      sections={"Next": "then read sats/facts.md"})
        ok, reason = validate_handoff(s.dir, 1)
        self.assertTrue(ok, reason)

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

    def test_stale_previous_handoff_recovers_without_raising(self):
        # Issue #13: turn-start recovery called _ensure_handoff with
        # the non-existent keyword system_prompt, so any session whose
        # previous handoff was stale raised TypeError before any work
        # and could never heal. Recovery now runs with the real system
        # prompt and the turn proceeds.
        s = make_session()
        handoff = str(s.dir / "sats" / "handoff.md")
        turn1 = [
            {"content": "done"},
            {"content": None, "tool_calls": [
                {"id": "h1", "name": "edit",
                 "arguments": {
                     "path": handoff,
                     "old_text": "## Handoff -- t0000",
                     "new_text": "## Handoff -- t0001"}}]},
        ]
        loop, _ = self._loop(turn1)
        self.assertEqual(loop.run_turn(s, "hi"), "done")
        self.assertTrue(validate_handoff(s.dir, 1)[0])
        # Previous turn died without a fresh handoff: stamp is stale.
        write_handoff(s.dir, stamp="t0099")
        turn2 = [
            {"content": None, "tool_calls": [
                {"id": "r1", "name": "edit",
                 "arguments": {
                     "path": handoff,
                     "old_text": "## Handoff -- t0099",
                     "new_text": "## Handoff -- t0001"}}]},
            {"content": "done2"},
            {"content": None, "tool_calls": [
                {"id": "h2", "name": "edit",
                 "arguments": {
                     "path": handoff,
                     "old_text": "## Handoff -- t0001",
                     "new_text": "## Handoff -- t0002"}}]},
        ]
        loop2, _ = self._loop(turn2)
        self.assertEqual(loop2.run_turn(s, "again"), "done2")
        ok, reason = validate_handoff(s.dir, 2)
        self.assertTrue(ok, reason)

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


def write_sized_handoff(sdir, stamp, total_lines):
    """Write a handoff with exactly total_lines lines (filler in Now)."""
    p = Path(sdir) / "sats" / "handoff.md"
    lines = ["# Handoff", "", f"## Handoff -- {stamp}"]
    for name in HANDOFF_SECTIONS:
        lines += [f"### {name}", f"{name} body"]
    pad = total_lines - len(lines)
    assert pad >= 0, f"total_lines {total_lines} too small"
    lines[5:5] = [f"filler {i}" for i in range(pad)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert len(p.read_text(encoding="utf-8").splitlines()) == total_lines
    return p


class TestHandoffLedger(unittest.TestCase):
    def test_trend_climbing(self):
        s = make_session()
        ledger = HandoffLedger(s.dir)
        for i, n in enumerate([25, 28, 31, 34, 37], start=1):
            ledger.record(i, n, n * 10)
        self.assertEqual(ledger.trend(), "climbing")

    def test_trend_stable(self):
        s = make_session()
        ledger = HandoffLedger(s.dir)
        for i, n in enumerate([20, 21, 20, 22, 20], start=1):
            ledger.record(i, n, n * 10)
        self.assertEqual(ledger.trend(), "stable")

    def test_trend_small_climb_below_threshold(self):
        # Climbing but tiny: not worth warning about.
        s = make_session()
        ledger = HandoffLedger(s.dir)
        for i, n in enumerate([5, 8, 11, 14], start=1):
            ledger.record(i, n, n * 10)
        self.assertEqual(ledger.trend(), "stable")

    def test_trend_insufficient_data(self):
        s = make_session()
        ledger = HandoffLedger(s.dir)
        ledger.record(1, 30, 300)
        ledger.record(2, 35, 350)
        self.assertEqual(ledger.trend(), "insufficient-data")

    def test_ledger_bounded(self):
        s = make_session()
        ledger = HandoffLedger(s.dir)
        for i in range(1, 36):
            ledger.record(i, 10, 100)
        self.assertEqual(len(ledger._load()), 30)
        self.assertEqual(ledger._load()[0]["turn"], 6)


class TestTrimHandoff(unittest.TestCase):
    def test_under_cap_untouched(self):
        text = "# Handoff\n\n## Handoff -- t0001\n### Now\nx\n"
        out, cut = trim_handoff(text)
        self.assertEqual(out, text)
        self.assertEqual(cut, 0)

    def test_over_cap_trimmed(self):
        s = make_session()
        p = write_sized_handoff(s.dir, "t0001", 60)
        out, cut = trim_handoff(p.read_text(encoding="utf-8"))
        self.assertLessEqual(len(out.splitlines()), HANDOFF_MAX_LINES)
        self.assertGreater(cut, 0)
        # Shape preserved: stamp + all four section headers.
        self.assertIn("## Handoff -- t0001", out)
        for name in HANDOFF_SECTIONS:
            self.assertIn(f"### {name}", out)
        # The next turn is told content was clipped.
        self.assertIn("trimmed by harness", out)

    def test_trim_keeps_front_load(self):
        s = make_session()
        p = write_sized_handoff(s.dir, "t0001", 60)
        out, _ = trim_handoff(p.read_text(encoding="utf-8"))
        self.assertIn("filler 0", out)      # front kept
        self.assertNotIn("filler 48", out)  # tail cut

    def test_is_over_cap(self):
        s = make_session()
        write_sized_handoff(s.dir, "t0001", 60)
        self.assertTrue(is_over_cap(s.dir))
        write_sized_handoff(s.dir, "t0001", 20)
        self.assertFalse(is_over_cap(s.dir))


class TestPhase2Enforcement(unittest.TestCase):
    def test_backstop_trims_when_model_wont(self):
        # Over-cap handoff; the model burns both forced rounds without
        # trimming. The deterministic backstop fires instead of giving
        # up: handoff-trimmed, backup kept, turn still closes.
        s = make_session()
        write_sized_handoff(s.dir, "t0001", 60)
        events = []
        loop = Loop(MockProvider([{"content": "done"},
                                  {"content": "x"},
                                  {"content": "y"}]),
                    Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append(k))
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "done")
        self.assertIn("handoff-trimmed", events)
        self.assertIn("handoff", events)
        self.assertNotIn("handoff-invalid", events)
        ok, reason = validate_handoff(s.dir, 1)
        self.assertTrue(ok, reason)
        backups = list((s.dir / "sats").glob(
            "handoff.pre-handoff-trim-*.bak"))
        self.assertEqual(len(backups), 1)

    def test_bloat_event_on_climbing_handoff(self):
        s = make_session()
        events = []
        loop = Loop(MockProvider([]), Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append(k))
        for i, n in enumerate([25, 28, 31], start=1):
            write_sized_handoff(s.dir, f"t{i:04d}", n)
            self.assertTrue(loop._ensure_handoff(s, i, "sys", []))
        # Three points: insufficient data, no warning yet.
        self.assertNotIn("handoff-bloat", events)
        write_sized_handoff(s.dir, "t0004", 34)
        self.assertTrue(loop._ensure_handoff(s, 4, "sys", []))
        self.assertIn("handoff-bloat", events)

    def test_prune_system_preserves_handoff_shape(self):
        self.assertIn("handoff.md", PRUNE_SYSTEM)
        self.assertIn("resume plan", PRUNE_SYSTEM)


if __name__ == "__main__":
    unittest.main()
