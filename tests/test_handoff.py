"""Handoff-per-turn (issue #5): sats/handoff.md lifecycle, validation,
harness-owned rendering, and assembly priority.
"""
import tempfile
import unittest
from pathlib import Path

from harness.assembly import SAT_ORDER, assemble, load_prompt
from harness.context import Budget
from harness.handoff import (HANDOFF_MAX_LINES, HANDOFF_SECTIONS,
                             HANDOFF_TEMPLATE, HandoffLedger,
                             handoff_size, parse_handoff,
                             validate_handoff)
from harness.loop import PRUNE_SYSTEM, Loop, Session
from harness.providers import MockProvider
from harness.session import render_handoff
from harness.tools import classify_source, source_gate


def make_session():
    d = tempfile.mkdtemp()
    return Session(id="t", dir=Path(d), workdir=d)


def handoff_response(sdir, turn_no):
    """MockProvider script entry: a handoff-compliant agent turn.

    Rewrites just the handoff stamp line (t0000 -> tNNNN); the
    template's four sections stay, which is all the validator needs.
    turn_no is the 1-based run_turn count. Models the model's old
    habit: an in-turn handoff edit. Under harness ownership the
    edit is denied and the harness renders instead; tests that use
    this response assert the denial path.
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
        # Harness-exclusive ownership: the model may not edit the
        # handoff either -- the harness renders it.
        denied_edit = source_gate(s.dir, "edit", str(p), s.workdir)
        self.assertIn("DENIED", denied_edit)
        self.assertIn("harness-owned", denied_edit)


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

    def test_stale_previous_handoff_recovers_without_raising(self):
        # Issue #13, harness-owned form: a previous turn that died
        # without a fresh handoff leaves a stale stamp. Turn-start
        # recovery re-renders it from sats state -- no model rounds,
        # no provider calls -- and the turn proceeds.
        s = make_session()
        loop, _ = self._loop([{"content": "done"}])
        self.assertEqual(loop.run_turn(s, "hi"), "done")
        self.assertTrue(validate_handoff(s.dir, 1)[0])
        # Previous turn died without a fresh handoff: stamp is stale.
        write_handoff(s.dir, stamp="t0099")
        events = []
        loop2 = Loop(MockProvider([{"content": "done2"}]),
                     Budget(hard=100000, soft=80000),
                     on_event=lambda k, v: events.append((k, v)))
        self.assertEqual(loop2.run_turn(s, "again"), "done2")
        ok, reason = validate_handoff(s.dir, 2)
        self.assertTrue(ok, reason)
        # Recovery rendered turn 1, turn end rendered turn 2; the
        # turn's reply was the only provider call.
        self.assertEqual(len(loop2.provider.calls), 1)
        rendered = [v["turn"] for k, v in events
                    if k == "handoff-rendered"]
        self.assertIn(1, rendered)
        self.assertIn(2, rendered)

    def test_fresh_handoff_is_replaced_by_render(self):
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
        # No second provider call: the render costs no round.
        self.assertEqual(len(loop.provider.calls), 1)
        body = (s.dir / "sats" / "handoff.md").read_text(
            encoding="utf-8")
        self.assertIn("## Handoff -- t0001", body)
        # The model's prose is gone: the render derives from sats.
        self.assertNotIn("more work", body)
        self.assertIn("(no active goals or tasks recorded)", body)

    def test_turn_end_renders_handoff(self):
        # Harness-owned handoff: a turn that never touches handoff.md
        # still ends with a fresh, valid render -- no model round.
        s = make_session()
        loop, events = self._loop([{"content": "done"}])
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "done")
        self.assertEqual(len(loop.provider.calls), 1)
        self.assertIn("handoff-rendered", events)
        ok, reason = validate_handoff(s.dir, 1)
        self.assertTrue(ok, reason)

    def test_model_handoff_edit_is_denied_and_render_wins(self):
        # The model tries its old habit -- editing handoff.md -- gets
        # DENIED, and the turn still ends with the harness render,
        # not the model's prose.
        s = make_session()
        handoff = str(s.dir / "sats" / "handoff.md")
        script = [
            {"content": None, "tool_calls": [
                {"id": "h1", "name": "edit",
                 "arguments": {
                     "path": handoff,
                     "old_text": "## Handoff -- t0000",
                     "new_text": ("## Handoff -- t0001\n### Now\n"
                                  "model prose\n### Next\nmore\n"
                                  "### Blockers\nnone\n### Pointers\n"
                                  "sats/current.md")}}]},
            {"content": "done"},
        ]
        loop, events = self._loop(script)
        out = loop.run_turn(s, "hi")
        self.assertEqual(out, "done")
        body = (s.dir / "sats" / "handoff.md").read_text(
            encoding="utf-8")
        self.assertNotIn("model prose", body)
        ok, reason = validate_handoff(s.dir, 1)
        self.assertTrue(ok, reason)


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
    def test_bloat_event_on_climbing_handoff(self):
        s = make_session()
        events = []
        loop = Loop(MockProvider([]), Budget(hard=100000, soft=80000),
                    on_event=lambda k, v: events.append(k))
        for i, n in enumerate([25, 28, 31], start=1):
            write_sized_handoff(s.dir, f"t{i:04d}", n)
            loop._record_handoff_ok(s, i)
        # Three points: insufficient data, no warning yet.
        self.assertNotIn("handoff-bloat", events)
        write_sized_handoff(s.dir, "t0004", 34)
        loop._record_handoff_ok(s, 4)
        self.assertIn("handoff-bloat", events)

    def test_prune_system_preserves_handoff_shape(self):
        self.assertIn("handoff.md", PRUNE_SYSTEM)
        self.assertIn("resume plan", PRUNE_SYSTEM)


class TestRenderHandoff(unittest.TestCase):
    def _write_sats(self, s, current=None, goals=None, tasks=None):
        sats = s.dir / "sats"
        if current is not None:
            (sats / "current.md").write_text(current, encoding="utf-8")
        if goals is not None:
            (sats / "goals.md").write_text(goals, encoding="utf-8")
        if tasks is not None:
            (sats / "tasks.md").write_text(tasks, encoding="utf-8")

    def test_renders_from_sats_state(self):
        s = make_session()
        self._write_sats(
            s,
            current="# Current task\n\nTracing the write path\n",
            goals="- [active] Ship it\n- [done] Wrote docs\n",
            tasks="# Tasks\n\nRefactor loop\nBLOCKED: Waiting on review\n")
        text = render_handoff(s.dir, 3)
        self.assertIn("## Handoff -- t0003", text)
        self.assertIn("Tracing the write path", text)
        self.assertIn("Ship it", text)
        self.assertNotIn("Wrote docs", text)  # done goals are not Next
        self.assertIn("Refactor loop", text)
        self.assertIn("Waiting on review", text)
        self.assertNotIn("BLOCKED:", text)  # prefix stripped in Blockers
        self.assertIn("sats/current.md", text)
        self.assertNotIn("sats/handoff.md", text)  # no self-pointer
        (s.dir / "sats" / "handoff.md").write_text(text,
                                                   encoding="utf-8")
        ok, reason = validate_handoff(s.dir, 3)
        self.assertTrue(ok, reason)

    def test_placeholders_when_empty(self):
        s = make_session()
        text = render_handoff(s.dir, 1)
        self.assertIn("(nothing recorded in sats/current.md)", text)
        self.assertIn("(no active goals or tasks recorded)", text)
        self.assertIn("(none recorded in sats/tasks.md)", text)
        (s.dir / "sats" / "handoff.md").write_text(text,
                                                   encoding="utf-8")
        ok, reason = validate_handoff(s.dir, 1)
        self.assertTrue(ok, reason)

    def test_blocked_prefix_rules(self):
        s = make_session()
        self._write_sats(
            s,
            tasks="# Tasks\n\nBLOCKED:\nBLOCKED: stuck on auth\n"
                  "mentions BLOCKED: mid-line\n")
        text = render_handoff(s.dir, 1)
        blockers = text.split("### Blockers")[1].split("### Pointers")[0]
        # Bare BLOCKED: carries no content, so it renders nowhere;
        # a mid-line mention is not a blocker, it stays a task.
        self.assertEqual(blockers.strip().splitlines(),
                         ["- stuck on auth"])
        self.assertIn("mentions BLOCKED: mid-line", text)

    def test_caps_long_sections(self):
        s = make_session()
        self._write_sats(
            s,
            current="# Current task\n"
                    + "".join(f"line {i}\n" for i in range(10)),
            goals="".join(f"- [active] goal {i}\n" for i in range(5)),
            tasks="# Tasks\n"
                  + "".join(f"task {i}\n" for i in range(30)))
        text = render_handoff(s.dir, 2)
        self.assertLessEqual(len(text.splitlines()), HANDOFF_MAX_LINES)
        now = text.split("### Now")[1].split("### Next")[0]
        self.assertLessEqual(
            len([ln for ln in now.splitlines() if ln.strip()]), 4)
        (s.dir / "sats" / "handoff.md").write_text(text,
                                                   encoding="utf-8")
        ok, reason = validate_handoff(s.dir, 2)
        self.assertTrue(ok, reason)


if __name__ == "__main__":
    unittest.main()
