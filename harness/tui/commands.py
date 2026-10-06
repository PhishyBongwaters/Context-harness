"""TUI slash-command dispatch (Phase 3): stdlib-only, no Textual import.

Parses with the same parse_repl_command as the CLI REPL and renders
read-only views with the same *_lines helpers, so the input box and
the REPL can never drift apart. Session/project switches and the
network-backed /models fetch stay with the caller (control object
built in harness.__main__); this module only classifies.
"""
from __future__ import annotations

QUIT_COMMANDS = frozenset({"quit", "exit", "q"})
OPEN_COMMANDS = frozenset({"open", "session"})
# Handled fully locally (pure lines, no session switch, no network).
LOCAL_COMMANDS = frozenset({"help", "list", "usage", "config", "providers"})
# Need the control object (mutate session/project state).
STATE_COMMANDS = frozenset({"new", "open", "session", "project"})
WORKER_COMMANDS = frozenset({"models"})  # network: never block the UI


def parse_slash(text: str):
    """Split '/cmd rest' -> (cmd, rest). None for non-command input."""
    from ..__main__ import parse_repl_command
    return parse_repl_command(text)


def unknown_hint(cmd: str) -> str:
    """Same wording as the CLI REPL for an unknown /cmd."""
    return f"Unknown command /{cmd} (/help)."


def is_quit(cmd: str) -> bool:
    return cmd in QUIT_COMMANDS


def needs_worker(cmd: str) -> bool:
    return cmd in WORKER_COMMANDS


def local_lines(cmd: str, rest: str, *, cfg=None, tracker=None,
                list_lines=None) -> list[str] | None:
    """Lines for read-only commands, via the CLI's own helpers.

    Returns None when the command needs session state, a worker
    thread, or app exit (new/open/project/models/quit/unknown-alias).
    Unknown commands return the CLI's usage hint.
    """
    from ..__main__ import (REPL_HELP, config_lines, providers_lines,
                            usage_lines)
    if cmd == "help":
        return REPL_HELP.splitlines()
    if cmd == "list":
        if list_lines is not None:
            return list(list_lines())
        if cfg is None:
            return ["[list] no session registry available"]
        from ..__main__ import sessions_lines
        return sessions_lines(cfg)
    if cmd == "usage":
        return usage_lines(tracker, rest)
    if cmd == "config":
        if cfg is None:
            return ["[config] unavailable"]
        return config_lines(cfg)
    if cmd == "providers":
        return providers_lines(cfg)
    if cmd in QUIT_COMMANDS or cmd in STATE_COMMANDS or cmd in WORKER_COMMANDS:
        return None
    return [unknown_hint(cmd)]
