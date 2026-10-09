"""Tool definitions (provider-agnostic) and executors.

The four v1 tools: exec, read, write, edit. context.md is the
harness-written assembly artifact: the model neither reads nor edits
it (its content is already the model's injected messages).
"""
from __future__ import annotations

import os
import subprocess

from .approvals import EXEC_TIMEOUT_DEFAULT
from pathlib import Path

MAX_OUTPUT_CHARS = 30_000


def tool_definitions() -> list[dict]:
    return [
        {
            "name": "exec",
            "description": (
                "Run a shell command and capture its output. "
                "Runs with your user privileges in the session working directory. "
                "Output is truncated to ~30k chars."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string",
                               "description": "Shell command to run."},
                    "workdir": {"type": "string",
                               "description": "Working directory (default: session cwd)."},
                    "timeout": {"type": "integer",
                               "description": "Timeout in seconds."},
                },
                "required": ["command"],
            },
        },
        {
            "name": "read",
            "description": "Read a file. Returns line-numbered text, optionally paged.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    "offset": {"type": "integer",
                              "description": "1-based first line (default 1)."},
                    "limit": {"type": "integer",
                             "description": "Max lines (default 200)."},
                },
                "required": ["path"],
            },
        },
        {
            "name": "write",
            "description": ("Write content to a file, creating parent dirs. "
                            "Overwrites the whole file."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    "content": {"type": "string",
                               "description": "Full file content."},
                },
                "required": ["path", "content"],
            },
        },
        {
            "name": "edit",
            "description": ("Replace old_text with new_text in a file. "
                            "old_text must match EXACTLY ONCE: zero matches "
                            "is an error, and two or more matches is an "
                            "error -- include more surrounding context so "
                            "the match is unique. This is the way to edit "
                            "files; do not read a file and rewrite it "
                            "whole."),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path."},
                    "old_text": {"type": "string",
                                "description": "Exact text to find."},
                    "new_text": {"type": "string",
                                 "description": "Replacement text."},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
        {
            "name": "tokens",
            "description": (
                "Count cl100k_base tokens (the harness budget estimator) "
                "for a file or literal text. Use it to verify the context "
                "meter's accounting instead of guessing."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string",
                             "description": "File to count."},
                    "text": {"type": "string",
                             "description": "Literal text to count."},
                },
            },
        },
    ]


def _resolve(path: str, workdir: str) -> Path:
    p = Path(os.path.expanduser(path))
    return p if p.is_absolute() else Path(workdir) / p


# Session source kinds (spec section 5, T5). The model may edit
# history/sat sources (edit-only). Everything else under the session
# dir is harness-owned.
def classify_source(session_dir: str | Path, path: str | Path) -> str:
    """Classify a path against the blank-slate session layout.

    Returns one of: prompt, state, index, history, sat, scratch,
    archive, assembled, other-session, other.
    """
    sdir = Path(session_dir).expanduser().resolve()
    p = Path(os.path.expanduser(str(path)))
    if not p.is_absolute():
        return "other"
    try:
        rel = p.resolve().relative_to(sdir)
    except ValueError:
        return "other"
    parts = rel.parts
    if len(parts) == 1:
        return {
            "prompt.md": "prompt",
            "state.json": "state",
            "index.md": "index",
            "history.md": "history",
            "scratch.md": "scratch",
            "context.md": "assembled",
        }.get(parts[0], "other-session")
    if parts[0] == "sats" and len(parts) == 2 and parts[1].endswith(".md"):
        return "sat"
    if parts[0] == "archive":
        return "archive"
    return "other-session"


def source_gate(session_dir: str | Path, name: str, path: str,
                workdir: str) -> str | None:
    """Harness source gates (T5). Returns a DENIED message or None.

    These are harness invariants, not user choices: they run before
    the approval flow. edit is allowed only on history/sats;
    write is rejected on every session source; read is denied for
    state.json and for context.md (the assembled transcript is already
    injected as the model's messages -- reading it would duplicate the
    entire context).
    """
    if name not in ("read", "write", "edit") or not path:
        return None
    try:
        target = _resolve(path, workdir)
    except Exception:
        return None
    kind = classify_source(session_dir, target)
    if kind in ("history", "sat"):
        if name == "write":
            return (f"DENIED: {target.name} is edit-only -- use edit with "
                    f"an exact old_text/new_text match, not whole-file "
                    f"write.")
        return None
    if kind in ("prompt", "state", "index", "scratch", "archive",
                "assembled"):
        if name in ("edit", "write"):
            return (f"DENIED: {target.name} is harness-owned and cannot "
                    f"be modified by the model.")
        if name == "read" and kind == "state":
            return "DENIED: state.json is harness-owned and not readable."
        if name == "read" and kind == "assembled":
            return ("DENIED: context.md is the assembled transcript the "
                    "harness already injected as your messages -- reading "
                    "it would duplicate your entire context.")
        return None
    return None


def exec_tool(args: dict, workdir: str) -> str:
    cmd = args["command"]
    cwd = args.get("workdir") or workdir
    timeout = int(args.get("timeout") or EXEC_TIMEOUT_DEFAULT)
    try:
        proc = subprocess.run(cmd, shell=True, cwd=cwd, timeout=timeout,
                              capture_output=True, text=True,
                              errors="replace")
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {timeout}s: {cmd}"
    except OSError as e:
        return f"OS ERROR running command: {e}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS] + "\n...[truncated]"
    return f"exit={proc.returncode}\n{out}"


def read_tool(args: dict, workdir: str) -> str:
    p = _resolve(args["path"], workdir)
    offset = int(args.get("offset") or 1)
    limit = int(args.get("limit") or 200)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as e:
        return f"ERROR reading {p}: {e}"
    total = len(lines)
    chunk = lines[offset - 1: offset - 1 + limit]
    numbered = "\n".join(f"{i + offset:6d}  {ln}"
                         for i, ln in enumerate(chunk))
    more = f"\n... ({total} total lines)" if offset - 1 + limit < total else ""
    return f"{p}  [lines {offset}-{offset + len(chunk) - 1} of {total}]\n{numbered}{more}"


def write_tool(args: dict, workdir: str) -> str:
    p = _resolve(args["path"], workdir)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.get("content") or "", encoding="utf-8")
    except OSError as e:
        return f"ERROR writing {p}: {e}"
    return f"Wrote {p} ({len(args.get('content') or '')} chars)"


def edit_tool(args: dict, workdir: str) -> str:
    """Replace-only edit with an exactly-once contract (T4).

    old_text must match exactly once: zero matches is an error naming
    the file, two or more is an error telling the caller to widen the
    match with surrounding context. The file is never touched unless
    the match is unique.
    """
    p = _resolve(args["path"], workdir)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"ERROR reading {p}: {e}"
    old, new = args["old_text"], args["new_text"]
    if not old:
        return f"ERROR: old_text must not be empty ({p})"
    n = text.count(old)
    if n == 0:
        return f"ERROR: old_text not found in {p}"
    if n > 1:
        return (f"ERROR: old_text matches {n} times in {p} -- include "
                f"more surrounding context so it matches exactly once")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"Edited {p} (1 occurrence replaced)"


def tokens_tool(args: dict, workdir: str) -> str:
    from .context import ESTIMATOR, count_tokens
    if args.get("path"):
        p = _resolve(args["path"], workdir)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"ERROR reading {p}: {e}"
        label = str(p)
    elif "text" in args:
        text, label = args["text"] or "", "<literal>"
    else:
        return "ERROR: pass path or text"
    return (f"{label}: {len(text):,} chars, "
            f"{count_tokens(text):,} tokens [{ESTIMATOR}]")


EXECUTORS = {
    "exec": exec_tool,
    "read": read_tool,
    "write": write_tool,
    "edit": edit_tool,
    "tokens": tokens_tool,
}


def run_tool(name: str, args: dict, workdir: str) -> str:
    fn = EXECUTORS.get(name)
    if fn is None:
        return f"ERROR: unknown tool '{name}'"
    try:
        return fn(args or {}, workdir)
    except Exception as e:  # never let a tool crash the loop
        return f"ERROR executing {name}: {e}"
