"""Blank-slate assembly (T3): build the per-turn transcript from sources.

Fixed order: sats (current, goals, facts, decisions, tasks)
-> history -> scratch.
Satellite files carry no `##` transcript headers of their own, so each
is wrapped in a `## sat <name>` section the parser maps to a user-role
message. Pure functions; the caller (T6/T7/T9) decides when to assemble.

Spec: docs/assembly-spec.md section 3.
"""
from __future__ import annotations

from pathlib import Path

SAT_ORDER = ("current", "goals", "facts", "decisions", "tasks")


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


# Guidance appended at load time when the on-disk prompt predates it
# (old sessions keep their prompt.md verbatim; the effective prompt
# stays current). Keep in sync with SYSTEM_PROMPT in loop.py.
_PROMPT_ADDENDUM_DELEGATE = (
    "\n  Heavy curation (large histories, many files to triage): prefer\n"
    "  `delegate` -- a subagent does the reading and editing in its own\n"
    "  context; you read back only its result file. The noise never enters\n"
    "  your transcript. This is not laziness, it is the architecture: your\n"
    "  active context is the scarce resource, and spending a fraction of\n"
    "  budget on a subagent to keep it clean is the win. Never apologize\n"
    "  for delegating; apologize for bloating your own context instead.\n"
)
_PROMPT_ADDENDUM_MARKER = "prefer\n  `delegate`"
_PROMPT_NUDGE_MARKER = "This is not laziness"
_PROMPT_NUDGE_LINES = (
    "  This is not laziness, it is the architecture: your\n"
    "  active context is the scarce resource, and spending a fraction of\n"
    "  budget on a subagent to keep it clean is the win. Never apologize\n"
    "  for delegating; apologize for bloating your own context instead.\n"
)


def load_prompt(sdir: str | Path, *, ctx_path: str, hard: int,
                soft: int, workdir: str | Path | None = None) -> str:
    """Read prompt.md fresh and fill the known template placeholders.

    Only the harness-known placeholders are substituted; any other
    braces in a custom prompt are left literal (str.format would blow
    up on them).
    """
    import platform
    text = _read(Path(sdir) / "prompt.md")
    if _PROMPT_ADDENDUM_MARKER not in text:
        text = text.rstrip("\n") + "\n" + _PROMPT_ADDENDUM_DELEGATE
    elif _PROMPT_NUDGE_MARKER not in text:
        # Has the base guidance but predates the nudge: append it.
        text = text.rstrip("\n") + "\n" + _PROMPT_NUDGE_LINES
    return (text.replace("{ctx_path}", str(ctx_path))
                .replace("{hard}", str(hard))
                .replace("{soft}", str(soft))
                .replace("{os_name}", platform.system())
                .replace("{workdir}",
                         str(workdir) if workdir else "")
                .replace("{sdir}", str(sdir)))


def write_assembled(sdir: str | Path, text: str) -> Path:
    """Write the assembled transcript as context.md (inspectable build
    artifact; the model may read it but never edit it)."""
    p = Path(sdir) / "context.md"
    p.write_text(text, encoding="utf-8")
    return p
