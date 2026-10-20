"""Handoff-per-turn: every turn ends with a shift handoff (issue #5).

The model tracks "next" as well as "now" in sats/handoff.md, rewritten
every turn. The harness validates freshness / shape / size at the turn
boundary; a stale or missing handoff triggers a forced handoff round.
This is the write-path thesis applied to the handoff itself: governed
writes, not polite requests.

Pure functions here; the Loop owns the enforcement timing.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

HANDOFF_SECTIONS = ("Now", "Next", "Blockers", "Pointers")
HANDOFF_MAX_LINES = 40
HANDOFF_MAX_ATTEMPTS = 2

# Canary tuning: how the ledger decides the handoff is "climbing"
# (dumping). Heuristic, deliberately simple and explainable.
HANDOFF_LEDGER_KEEP = 30      # entries kept in handoff.json
HANDOFF_CLIMB_WINDOW = 5      # turns of history the slope is measured over
HANDOFF_CLIMB_MIN_SLOPE = 1.0  # lines/turn above this counts as climbing
HANDOFF_CLIMB_MIN_LINES = HANDOFF_MAX_LINES // 2  # only warn when sizable

HANDOFF_TEMPLATE = """# Handoff

Your shift handoff to your next self. Rewrite this whole file at the
end of every turn -- never append. There is no other memory: anything
not in the sats or history is gone when this turn ends.

## Handoff -- t0000
### Now
(nothing yet -- first turn)
### Next
- [ ] (read the task and decide the first step)
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
  {sdir}/sats/handoff.md -- your shift handoff to your next self. There
    is no other memory: pruned and archived turns leave your context,
    and anything not written into the sats or history is gone. Assume
    each turn is a handoff -- the next turn wakes up knowing ONLY what
    is in the assembled sources.
  Rewrite the whole handoff at the end of every turn, before you finish:
    ## Handoff -- {turn_stamp}
    ### Now -- where things stand (2-4 lines, not a biography)
    ### Next -- the intended next steps, in order; the hypothesis in
      flight and what would prove or disprove it; dead ends already
      tried, one line each. Each step is a board card: `- [ ]` open,
      `- [x]` done -- check them off as you finish, so the next turn
      sees what remains at a glance
    ### Blockers -- what is stuck, and what would unstick it
    ### Pointers -- where the state lives (file paths, sat sections),
      never copies of the state itself
  Rules: rewrite, never append. Under 40 lines -- it is a shift board,
  not a biography. If it's still over 40 lines after your round, the
  harness trims it mechanically -- and it won't trim kindly. Write
  tight. "Unchanged -- still <reason>" is a legal handoff,
  but the stamp must read {turn_stamp}. The harness checks the handoff
  before your turn ends; a stale or missing handoff sends you back for
  one more round.
  HANDOFF CADENCE: Close the turn and rewrite sats/handoff.md after you complete a logical sub-task, or after ~8-12 tool calls, whichever comes first. Do not chain tool calls indefinitely. Update sats/current.md live and ensure the handoff is rewritten with the correct t{turn_stamp} stamp before finishing.
"""
HANDOFF_PROMPT_MARKER = "HANDOFF (every turn ends with one"

# Forced-round nudge: a user-role message, built per turn in the Loop.
# {reason} carries the validator's complaint so the model knows exactly
# what failed. .format() fields only -- no other braces in this string.
HANDOFF_NUDGE = (
    "HANDOFF REQUIRED before this turn can end: rewrite "
    "{handoff_path} with the edit tool so its header reads "
    "\"## Handoff -- {turn_stamp}\" and all four sections "
    "(Now / Next / Blockers / Pointers) are current. {reason} "
    "Do the handoff now -- no other work this round."
)

_HEADER_RE = re.compile(r"(?m)^## Handoff -- t(\d{4})\s*$")
_SECTION_RES = {
    name: re.compile(r"(?m)^### " + re.escape(name) + r"\s*$")
    for name in HANDOFF_SECTIONS
}


def parse_handoff(text: str) -> dict | None:
    """Parse a handoff file. None when there is no handoff header."""
    m = _HEADER_RE.search(text or "")
    if not m:
        return None
    return {
        "stamp": int(m.group(1)),
        "sections": {n: bool(rx.search(text))
                     for n, rx in _SECTION_RES.items()},
        "lines": len(text.splitlines()),
    }


# Board card states in ### Next: `- [ ]` open, `- [x]`/`- [X]` done.
# Plain lines (numbered or otherwise) predate the card states and count
# as open, so old handoffs still parse.
_NEXT_DONE_RE = re.compile(r"^\s*(?:\d+[.)]\s*)?- \[[xX]\]\s*(.+?)\s*$")
_NEXT_OPEN_RE = re.compile(r"^\s*(?:\d+[.)]\s*)?- \[ \]\s*(.+?)\s*$")


def parse_next_items(handoff_text: str) -> list[tuple[bool, str]]:
    """Parse the ### Next section into (done, text) board cards.

    Returns [] when there is no Next section. Never raises on odd
    input: an unparseable section is no cards, not an exception.
    """
    try:
        text = handoff_text or ""
        m = _SECTION_RES["Next"].search(text)
        if not m:
            return []
        body = text[m.end():]
        nxt = re.search(r"(?m)^### \S+", body)
        if nxt:
            body = body[:nxt.start()]
        cards = []
        for line in body.splitlines():
            if not line.strip():
                continue
            dm = _NEXT_DONE_RE.match(line)
            if dm:
                cards.append((True, dm.group(1)))
                continue
            om = _NEXT_OPEN_RE.match(line)
            if om:
                cards.append((False, om.group(1)))
                continue
            cards.append((False, line.strip()))
        return cards
    except (AttributeError, TypeError, IndexError):
        return []


def validate_handoff(sdir: str | Path, turn_no: int) -> tuple[bool, str]:
    """Check sats/handoff.md for this turn.

    Returns (True, "") or (False, reason). Freshness, not novelty: an
    "Unchanged" handoff is legal as long as the stamp is this turn's.
    """
    p = Path(sdir) / "sats" / "handoff.md"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False, "sats/handoff.md is missing -- rewrite it"
    parsed = parse_handoff(text)
    if parsed is None:
        return False, ("sats/handoff.md has no '## Handoff -- tNNNN' "
                       "header -- rewrite it in the required shape")
    # Allow bootstrap stamp t0000 on the first real turn
    if parsed["stamp"] != turn_no and not (parsed["stamp"] == 0 and turn_no == 1):
        return False, (
            f"handoff stamp is t{parsed['stamp']:04d} but this turn is "
            f"t{turn_no:04d} -- rewrite it")
    missing = [n for n, ok in parsed["sections"].items() if not ok]
    if missing:
        return False, (
            "handoff is missing section(s): "
            + ", ".join(f"### {n}" for n in missing))
    if parsed["lines"] > HANDOFF_MAX_LINES:
        return False, (
            f"handoff is {parsed['lines']} lines (cap {HANDOFF_MAX_LINES}) "
            "-- trim it to a shift board, not a biography")
    return True, ""


def ensure_handoff_file(sdir: str | Path) -> None:
    """Recreate sats/handoff.md from the template when absent.

    Defensive: the model has exec, so the file can disappear between
    validation and the forced round. The harness restores the shape;
    the model still has to fill it.
    """
    p = Path(sdir) / "sats" / "handoff.md"
    if not p.exists():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(HANDOFF_TEMPLATE, encoding="utf-8")


def handoff_size(sdir: str | Path) -> tuple[int, int]:
    """(lines, tokens) of the current handoff -- the dumping canary."""
    from .context import count_tokens
    try:
        text = (Path(sdir) / "sats" / "handoff.md").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return 0, 0
    return len(text.splitlines()), count_tokens(text)


def is_over_cap(sdir: str | Path) -> bool:
    """True when sats/handoff.md exceeds the line cap."""
    try:
        text = (Path(sdir) / "sats" / "handoff.md").read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return False
    return len(text.splitlines()) > HANDOFF_MAX_LINES


def trim_handoff(text: str) -> tuple[str, int]:
    """Deterministically trim an over-cap handoff to the line cap.

    Keeps the "# Handoff" title, the handoff header and all four
    section headers; cuts body lines from the END of the longest
    sections first (agents front-load the important lines -- documented
    assumption). Appends a one-line marker noting the harness cut, so
    the next turn knows content was clipped.

    Pure function. Returns (trimmed_text, lines_cut).
    """
    lines = (text or "").splitlines()
    if len(lines) <= HANDOFF_MAX_LINES:
        return text, 0
    # Split into: preamble (title + header) and per-section bodies.
    # A section starts at a "### <Name>" line; everything before the
    # first section header is preamble.
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []  # (header, body lines)
    for ln in lines:
        m = re.match(r"^### (\S+)\s*$", ln)
        if m and m.group(1) in HANDOFF_SECTIONS:
            sections.append((ln, []))
        elif sections:
            sections[-1][1].append(ln)
        else:
            preamble.append(ln)
    # Guarantee the four headers exist even in a mangled file.
    have = {h for h, _ in sections}
    for name in HANDOFF_SECTIONS:
        header = f"### {name}"
        if header not in have:
            sections.append((header, []))
    marker = "<!-- trimmed by harness: content cut to fit the cap -->"
    # Budget: cap minus preamble, headers and marker; bodies share it.
    fixed = len(preamble) + len(sections) + 1  # +1 marker
    body_budget = max(0, HANDOFF_MAX_LINES - fixed)
    bodies = [b for _, b in sections]
    total_body = sum(len(b) for b in bodies)
    cut = max(0, total_body - body_budget)
    # Cut from the end of the longest bodies first.
    remaining = cut
    while remaining > 0:
        longest = max(range(len(bodies)),
                      key=lambda i: len(bodies[i]),
                      default=None)
        if longest is None or not bodies[longest]:
            break
        bodies[longest].pop()
        remaining -= 1
    actual_cut = total_body - sum(len(b) for b in bodies)
    out: list[str] = list(preamble)
    for (header, _), body in zip(sections, bodies):
        out.append(header)
        out.extend(body)
    out.append(marker)
    return "\n".join(out) + "\n", actual_cut


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
