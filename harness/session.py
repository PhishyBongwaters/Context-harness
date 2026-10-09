"""Blank-slate session layout (T1) and harness-owned state (T2).

The session directory holds every source the harness assembles each
turn. Files the model may never edit are created here; the edit gates
(T5) enforce that in the tool layer. All state the harness owns
(turn counter, section sequence) lives in state.json -- never in a
model-editable file.

Spec: docs/assembly-spec.md sections 2, 4, 5.
"""
from __future__ import annotations

import json
from pathlib import Path

from .context import stamp

SAT_FILES = {
    "facts": "# Facts\n\nDiscovered truths about the world, one line each.\n",
    "decisions": "# Decisions\n\nChoices made and why, one line each.\n",
    "tasks": "# Tasks\n\nOpen work items, one line each.\n",
}

INDEX_TEMPLATE = """# Index

Harness-owned pointer map. The model reads this file; only the harness
writes it.

## satellites
- sats/facts.md
- sats/decisions.md
- sats/tasks.md

## episodes
"""

HISTORY_TEMPLATE = """# History

Curated durable transcript. Edit with the edit tool (exact old_string /
new_string). Sections: `## user t<NNNN>`, `## assistant t<NNNN>`,
`## episode t<NNNN>`.
"""


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def init_layout(sdir: str | Path, prompt_text: str) -> dict[str, Path]:
    """Create the blank-slate session layout.

    Idempotent: existing files are never clobbered, so re-opening a
    session keeps its history, satellites, and counters.
    Returns the key paths.
    """
    sdir = Path(sdir)
    sats = sdir / "sats"
    archive = sdir / "archive"
    sats.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {
        "prompt": sdir / "prompt.md",
        "state": sdir / "state.json",
        "index": sdir / "index.md",
        "history": sdir / "history.md",
        "scratch": sdir / "scratch.md",
        "archive": archive,
    }
    _write_if_missing(paths["prompt"], prompt_text)
    _write_if_missing(paths["state"],
                      json.dumps({"turn": 0, "section_seq": 0}))
    _write_if_missing(paths["index"], INDEX_TEMPLATE)
    _write_if_missing(paths["history"], HISTORY_TEMPLATE)
    _write_if_missing(paths["scratch"], "")
    for name, header in SAT_FILES.items():
        p = sats / f"{name}.md"
        _write_if_missing(p, header)
        paths[name] = p
    return paths


def load_state(sdir: str | Path) -> dict:
    """Read the harness-owned counters. Corrupt/missing -> zeros."""
    try:
        data = json.loads((Path(sdir) / "state.json")
                         .read_text(encoding="utf-8"))
        return {"turn": int(data.get("turn", 0)),
                "section_seq": int(data.get("section_seq", 0))}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"turn": 0, "section_seq": 0}


def save_state(sdir: str | Path, state: dict) -> None:
    (Path(sdir) / "state.json").write_text(
        json.dumps({"turn": int(state.get("turn", 0)),
                    "section_seq": int(state.get("section_seq", 0))}),
        encoding="utf-8")


def next_turn(sdir: str | Path) -> int:
    """Increment the monotonic turn counter; returns the new turn number.

    Harness-owned: the model never counts turns itself.
    """
    state = load_state(sdir)
    state["turn"] += 1
    save_state(sdir, state)
    return state["turn"]


def next_section(sdir: str | Path) -> int:
    """Increment the section sequence; returns the new sequence number."""
    state = load_state(sdir)
    state["section_seq"] += 1
    save_state(sdir, state)
    return state["section_seq"]


def turn_stamp(sdir: str | Path) -> str:
    """Current turn as a header stamp, e.g. 't0042' (no increment)."""
    return stamp(load_state(sdir)["turn"])
