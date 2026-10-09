"""Blank-slate assembly (T3): build the per-turn transcript from sources.

Fixed order: sats (facts, decisions, tasks) -> history -> scratch.
Satellite files carry no `##` transcript headers of their own, so each
is wrapped in a `## sat <name>` section the parser maps to a user-role
message. Pure functions; the caller (T6/T7/T9) decides when to assemble.

Spec: docs/assembly-spec.md section 3.
"""
from __future__ import annotations

from pathlib import Path

SAT_ORDER = ("facts", "decisions", "tasks")


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def assemble(sdir: str | Path) -> str:
    """Assemble the transcript text. Deterministic: unchanged sources
    produce byte-identical output."""
    sdir = Path(sdir)
    parts: list[str] = []
    for name in SAT_ORDER:
        body = _read(sdir / "sats" / f"{name}.md").rstrip()
        parts.append(f"## sat {name}\n{body}\n")
    history = _read(sdir / "history.md").rstrip()
    if history:
        parts.append(history + "\n")
    scratch = _read(sdir / "scratch.md").rstrip()
    if scratch:
        parts.append(scratch + "\n")
    return "\n".join(parts)


def load_prompt(sdir: str | Path, *, ctx_path: str, hard: int,
                soft: int) -> str:
    """Read prompt.md fresh and fill the known template placeholders.

    Only the harness-known placeholders are substituted; any other
    braces in a custom prompt are left literal (str.format would blow
    up on them).
    """
    text = _read(Path(sdir) / "prompt.md")
    return (text.replace("{ctx_path}", str(ctx_path))
                .replace("{hard}", str(hard))
                .replace("{soft}", str(soft)))


def write_assembled(sdir: str | Path, text: str) -> Path:
    """Write the assembled transcript as context.md (inspectable build
    artifact; the model may read it but never edit it)."""
    p = Path(sdir) / "context.md"
    p.write_text(text, encoding="utf-8")
    return p
