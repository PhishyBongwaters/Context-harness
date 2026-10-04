"""Tool definitions (provider-agnostic) and executors.

The four v1 tools: exec, read, write, edit. The model's context file
(context.md) is an ordinary file -- it is curated with write/edit,
no special tool required.
"""
from __future__ import annotations

import os
import subprocess
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
                               "description": "Timeout in seconds (default 60)."},
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
            "description": ("Replace the first exact occurrence of old_text "
                            "with new_text in a file."),
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
    ]


def _resolve(path: str, workdir: str) -> Path:
    p = Path(os.path.expanduser(path))
    return p if p.is_absolute() else Path(workdir) / p


def exec_tool(args: dict, workdir: str) -> str:
    cmd = args["command"]
    cwd = args.get("workdir") or workdir
    timeout = int(args.get("timeout") or 60)
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
    p = _resolve(args["path"], workdir)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return f"ERROR reading {p}: {e}"
    old, new = args["old_text"], args["new_text"]
    if old not in text:
        return f"ERROR: old_text not found in {p}"
    p.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"Edited {p} (1 occurrence replaced)"


EXECUTORS = {
    "exec": exec_tool,
    "read": read_tool,
    "write": write_tool,
    "edit": edit_tool,
}


def run_tool(name: str, args: dict, workdir: str) -> str:
    fn = EXECUTORS.get(name)
    if fn is None:
        return f"ERROR: unknown tool '{name}'"
    try:
        return fn(args or {}, workdir)
    except Exception as e:  # never let a tool crash the loop
        return f"ERROR executing {name}: {e}"
