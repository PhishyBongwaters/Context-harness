"""Handoff-per-turn: every turn ends with a shift handoff (issue #5).

The harness renders sats/handoff.md from sats state at turn end --
the model never writes it. The harness validates the render's
freshness / shape / size as a self-check; a bad render is loud, never
blocking. This is the write-path thesis applied to the handoff
itself: governed writes, not polite requests.

Pure functions here; the Loop owns the enforcement timing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HANDOFF_SECTIONS = ("Now", "Next", "Blockers", "Pointers")
HANDOFF_MAX_LINES = 40

# Canary tuning: how the ledger decides the handoff is "climbing"
# (dumping). Heuristic, deliberately simple and explainable.
HANDOFF_LEDGER_KEEP = 30      # entries kept in handoff.json
HANDOFF_CLIMB_WINDOW = 5      # turns of history the slope is measured over
HANDOFF_CLIMB_MIN_SLOPE = 1.0  # lines/turn above this counts as climbing
HANDOFF_CLIMB_MIN_LINES = HANDOFF_MAX_LINES // 2  # only warn when sizable

HANDOFF_TEMPLATE = """# Handoff

Your shift handoff to your next self, rendered by the harness from
sats state at turn end. Never edit this file directly -- curate
sats/current.md and sats/tasks.md instead; the harness renders from
those.

## Handoff -- t0000
### Now
(nothing yet -- first turn)
### Next
(read the task and decide the first step)
### Blockers
(none yet)
### Pointers
(sats/current.md)
"""

# Single source for the handoff guidance. loop.py splices it into
# SYSTEM_PROMPT for new sessions; assembly.py appends it as a load-time
# addendum for sessions whose prompt.md predates it. {sdir} and
# {turn_stamp} are filled by load_prompt's placeholder substitution.
HANDOFF_PROMPT_PARAGRAPH = """
HANDOFF (every turn ends with one -- this is how you survive the night):
  {sdir}/sats/handoff.md -- your shift handoff to your next self,
  RENDERED BY THE HARNESS from sats state at turn end. You do not
  write it: never edit handoff.md directly (the harness denies the
  edit). There is no other memory: pruned and archived turns leave
  your context, and anything not written into the sats or history is
  gone. Assume each turn is a handoff -- the next turn wakes up
  knowing ONLY what is in the assembled sources.
  Keep the state the handoff renders from current, every turn:
    {sdir}/sats/current.md -- where things stand (one line, updated
      when the task changes or completes)
    {sdir}/sats/tasks.md -- open work items, one line each; prefix a
      line BLOCKED: when it is stuck, and say what would unstick it
    goals via the goal_done tool when one completes (never hand-edit
      goals.md)
  The rendered file reads:
    ## Handoff -- {turn_stamp}
    ### Now -- from sats/current.md
    ### Next -- your active goals and sats/tasks.md
    ### Blockers -- BLOCKED: lines from sats/tasks.md
    ### Pointers -- the sats files present
  Write tight sats, not a biography -- the render is capped at 40 lines.
"""
HANDOFF_PROMPT_MARKER = "HANDOFF (every turn ends with one"

_HEADER_RE = re.compile(r"(?m)^## Handoff -- t(\d{4})\s*$")
_SECTION_RES = {
    name: re.compile(r"(?m)^### " + re.escape(name) + r"\s*$")
    for name in HANDOFF_SECTIONS
}


def _section_bodies(text: str) -> dict[str, str]:
    """Map each handoff section name to its body text.

    The body runs from the section's `### Name` header to the next
    `###` header or EOF. First occurrence wins if a section header
    repeats. A section with no header maps to nothing (absent).
    """
    headers = [(m.group(1), m.end())
               for m in re.finditer(r"(?m)^### (Now|Next|Blockers|Pointers)"
                                   r"\s*$", text or "")]
    bodies: dict[str, str] = {}
    for i, (name, start) in enumerate(headers):
        end = len(text)
        if i + 1 < len(headers):
            nxt = re.search(r"(?m)^### (Now|Next|Blockers|Pointers)"
                            r"\s*$", text[start:])
            end = start + nxt.start() if nxt else len(text)
        if name not in bodies:
            bodies[name] = text[start:end]
    return bodies


def parse_handoff(text: str) -> dict | None:
    """Parse a handoff file. None when there is no handoff header.

    "sections" reports header presence (as before);
    "sections_content" reports whether each present section has at
    least one non-blank line -- a header with nothing under it is
    not a handoff. "next_bodies" carries the Next section body for
    the pointer check in validate_handoff.
    """
    m = _HEADER_RE.search(text or "")
    if not m:
        return None
    bodies = _section_bodies(text)
    return {
        "stamp": int(m.group(1)),
        "sections": {n: bool(rx.search(text))
                     for n, rx in _SECTION_RES.items()},
        "sections_content": {
            n: any(line.strip() for line in bodies.get(n, "").splitlines())
            for n in HANDOFF_SECTIONS},
        "next_body": bodies.get("Next", ""),
        "lines": len(text.splitlines()),
    }


def validate_handoff(sdir: str | Path, turn_no: int) -> tuple[bool, str]:
    """Check sats/handoff.md for this turn.

    Returns (True, "") or (False, reason). Freshness, not novelty: an
    "Unchanged" handoff is legal as long as the stamp is this turn's.
    """
    p = Path(sdir) / "sats" / "handoff.md"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, "sats/handoff.md is missing -- the harness render did not write it"
    parsed = parse_handoff(text)
    if parsed is None:
        return False, ("sats/handoff.md has no '## Handoff -- tNNNN' "
                       "header -- the harness render did not write it "
                       "in the required shape")
    # Strict stamp match (issue #13): the t0000-on-turn-1 allowance
    # waved the first turn through without a handoff. A fresh
    # session's t0000 template is overwritten by the turn-end
    # render, which is the designed path.
    if parsed["stamp"] != turn_no:
        return False, (
            f"handoff stamp is t{parsed['stamp']:04d} but this turn is "
            f"t{turn_no:04d} -- render stamp mismatch")
    missing = [n for n, ok in parsed["sections"].items() if not ok]
    if missing:
        return False, (
            "handoff is missing section(s): "
            + ", ".join(f"### {n}" for n in missing))
    empty = [n for n in HANDOFF_SECTIONS
             if parsed["sections"][n]
             and not parsed["sections_content"][n]]
    if empty:
        return False, (
            "handoff section(s) are empty: "
            + ", ".join(f"### {n}" for n in empty)
            + " -- a header with nothing under it is not a handoff")
    refs = sorted(set(re.findall(r"sats/([A-Za-z0-9_.\-/]+\.md)",
                                 parsed["next_body"])))
    gone = [f"sats/{r}" for r in refs
            if not (Path(sdir) / "sats" / r).exists()]
    if gone:
        return False, (
            "handoff Next references missing sats file(s): "
            + ", ".join(gone)
            + " -- the render copied a reference to a file that does "
            "not exist; fix sats/tasks.md or sats/goals.md")
    if parsed["lines"] > HANDOFF_MAX_LINES:
        return False, (
            f"handoff is {parsed['lines']} lines (cap {HANDOFF_MAX_LINES}) "
            "-- the render exceeded its own cap")
    return True, ""


def handoff_size(sdir: str | Path) -> tuple[int, int]:
    """(lines, tokens) of the current handoff -- the dumping canary."""
    from .context import count_tokens
    try:
        text = (Path(sdir) / "sats" / "handoff.md").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return 0, 0
    return len(text.splitlines()), count_tokens(text)


class HandoffLedger:
    """Per-turn handoff size ledger: the dumping canary.

    Stored at <sdir>/handoff.json, bounded to the last
    HANDOFF_LEDGER_KEEP entries. trend() reports "climbing" when the
    line count rises steadily over the recent window -- the signal that
    the agent is dumping into the handoff instead of curating it.
    """

    def __init__(self, sdir: str | Path):
        self.path = Path(sdir) / "handoff.json"

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except (OSError, ValueError):
            pass
        return []

    def record(self, turn_no: int, lines: int, tokens: int) -> None:
        entries = self._load()
        entries.append({"turn": turn_no, "lines": lines,
                        "tokens": tokens})
        entries = entries[-HANDOFF_LEDGER_KEEP:]
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(entries), encoding="utf-8")
        except OSError:
            pass  # best effort: the ledger must never block a turn

    def trend(self) -> str:
        """One of "climbing", "stable", "insufficient-data"."""
        entries = self._load()[-HANDOFF_CLIMB_WINDOW:]
        if len(entries) < 4:
            return "insufficient-data"
        first, last = entries[0]["lines"], entries[-1]["lines"]
        slope = (last - first) / (len(entries) - 1)
        if (slope > HANDOFF_CLIMB_MIN_SLOPE
                and last > HANDOFF_CLIMB_MIN_LINES):
            return "climbing"
        return "stable"
