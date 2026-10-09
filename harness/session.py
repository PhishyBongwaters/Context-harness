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
    "current": ("# Current task\n\nWhat you are actively working on right "
                "now, one line. Update it when the task changes or "
                "completes.\n"),
    "goals": ("# Goals\n\nEnduring objectives, one per line: "
              "`- [active] ...` / `- [done] ...`.\n"),
    "facts": "# Facts\n\nDiscovered truths about the world, one line each.\n",
    "decisions": "# Decisions\n\nChoices made and why, one line each.\n",
    "tasks": "# Tasks\n\nOpen work items, one line each.\n",
}

INDEX_TEMPLATE = """# Index

Harness-owned pointer map. The model reads this file; only the harness
writes it.

## satellites
- sats/current.md
- sats/goals.md
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


class MigrationError(Exception):
    """Old transcript is structurally ambiguous; migrate by hand."""


def is_old_layout(sdir: str | Path) -> bool:
    """True when sdir holds an old single-file session.

    Old layout: context.md exists (accumulating transcript) but the
    blank-slate state.json was never created.
    """
    sdir = Path(sdir)
    return (sdir / "context.md").is_file() and not (sdir / "state.json").exists()


def migrate_session(sdir: str | Path) -> dict | None:
    """Migrate an old single-file session to the blank-slate layout (T10).

    ## user sections become stamped history; each turn's assistant/tool
    run becomes an archived episode (archive/migrated-t<NNNN>.md) with a
    ## episode pointer in history. The turn counter is seeded so new
    turns continue monotonically. The original file is kept as
    context.md.pre-migration-<stamp>.bak -- never deleted.

    Returns a report dict, or None when sdir is not an old layout.
    Raises MigrationError on structural ambiguity: loud, never lossy.
    Nothing is written before the transcript validates.
    """
    from .context import _split_sections, _split_tool_calls
    import datetime as _dt

    sdir = Path(sdir)
    if not is_old_layout(sdir):
        return None

    raw = (sdir / "context.md").read_text(encoding="utf-8",
                                          errors="replace")
    sections = _split_sections(raw)

    turn_no = 0
    history_blocks: list[str] = []
    archive_writes: list[tuple[str, str]] = []
    dropped_orphans = 0
    # Current episode under construction.
    ep_turn = 0
    ep_sections: list[tuple[str, str | None, str, str]] = []
    ep_fence_ids: set[str] = set()

    def flush_episode() -> None:
        if not ep_sections:
            return
        tag = f"t{ep_turn:04d}"
        tool_count = sum(1 for r, _, _, _ in ep_sections if r == "tool")
        name = f"migrated-{tag}.md"
        body = "".join(f"{h}\n{b.strip()}\n"
                       for _, _, h, b in ep_sections)
        archive_writes.append((name, body))
        history_blocks.append(
            f"## episode {tag}\n"
            f"archive: archive/{name}\n"
            f"turns: {tag}-{tag}\n"
            f"tool_calls: {tool_count}\n")

    for role, label, header, body in sections:
        if role == "user":
            flush_episode()
            turn_no += 1
            ep_turn = turn_no
            ep_sections = []
            ep_fence_ids = set()
            history_blocks.append(
                f"## user t{turn_no:04d}\n{body.strip()}\n")
        elif role == "assistant":
            if turn_no == 0:
                raise MigrationError(
                    f"assistant section before any user message ({header})")
            _, tool_calls = _split_tool_calls(body.strip())
            for tc in tool_calls or []:
                if isinstance(tc, dict) and tc.get("id"):
                    ep_fence_ids.add(tc["id"])
            ep_sections.append((role, label, header, body))
        elif role == "tool":
            if turn_no == 0:
                raise MigrationError(
                    f"tool section before any user message ({header})")
            if label not in ep_fence_ids:
                # Orphans were never sent to the provider; drop loudly.
                dropped_orphans += 1
                continue
            ep_sections.append((role, label, header, body))
        else:
            # sat/episode sections cannot appear in old transcripts.
            raise MigrationError(
                f"unexpected section in old transcript ({header})")
    flush_episode()

    if turn_no == 0:
        raise MigrationError("no user sections in old context.md")

    # All validated: write. Archive + history + state first, original
    # renamed to .bak last.
    archive_dir = sdir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    for name, body in archive_writes:
        _write_if_missing(archive_dir / name, body)
    _write_if_missing(sdir / "history.md",
                      HISTORY_TEMPLATE + "".join(history_blocks))
    save_state(sdir, {"turn": turn_no, "section_seq": 0})
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    (sdir / "context.md").rename(
        sdir / f"context.md.pre-migration-{stamp}.bak")

    return {"turns": turn_no,
            "episodes": len(archive_writes),
            "dropped_orphans": dropped_orphans}


def init_layout(sdir: str | Path, prompt_text: str) -> dict:
    """Create the blank-slate session layout.

    Idempotent: existing files are never clobbered, so re-opening a
    session keeps its history, satellites, and counters.
    Old single-file sessions are migrated first (T10); the report (or
    None) is returned under the "migration" key.
    Returns the key paths.
    """
    sdir = Path(sdir)
    migration = migrate_session(sdir)
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
    paths["migration"] = migration
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
