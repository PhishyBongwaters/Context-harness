"""ctx: daily-driver agent loop with context-as-file.

Usage:
  python -m harness "do the thing"   # one task in the current session
  python -m harness                  # REPL in the current session
  python -m harness --new "task"     # start a fresh session
  python -m harness --list           # list sessions
  python -m harness --config         # write an example config file
  python -m harness --provider anthropic --model <id> "task"
"""
from __future__ import annotations

import argparse
import datetime
import os
import sys
from pathlib import Path

from .config import (PROVIDER_DEFAULTS, config_path, load_config,
                     write_example_config)
from .context import Budget
from .loop import BudgetExceeded, Loop, Session
from .providers import ProviderError, make_provider


def _sessions(cfg) -> Path:
    p = cfg.sessions_path
    p.mkdir(parents=True, exist_ok=True)
    return p


def _current_id(sessions: Path) -> str | None:
    marker = sessions / ".current"
    if marker.exists():
        sid = marker.read_text(encoding="utf-8").strip()
        if sid and (sessions / sid).exists():
            return sid
    return None


def _set_current(sessions: Path, sid: str) -> None:
    (sessions / ".current").write_text(sid, encoding="utf-8")


def _new_session(cfg, workdir: str) -> Session:
    sessions = _sessions(cfg)
    sid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    sdir = sessions / sid
    sdir.mkdir(parents=True, exist_ok=True)
    _set_current(sessions, sid)
    return Session(id=sid, dir=sdir, workdir=workdir)


def _open_session(cfg, sid: str | None, workdir: str) -> Session:
    sessions = _sessions(cfg)
    sid = sid or _current_id(sessions)
    if sid is None:
        return _new_session(cfg, workdir)
    _set_current(sessions, sid)
    return Session(id=sid, dir=sessions / sid, workdir=workdir)


def _print_event(kind: str, data) -> None:
    if kind == "assistant":
        print(data, flush=True)
    elif kind == "tool":
        args = data.get("args") or {}
        brief = args.get("command") or args.get("path") or ""
        print(f"\n$ {data['name']} {brief}", flush=True)
    elif kind == "budget":
        print(f"\n[{data['status'].upper()} budget: {data['tokens']:,} tokens]",
              flush=True)
    elif kind == "prune":
        print(f"\n[prune-only turn {data['attempt']}: "
              f"{data['tokens']:,} tokens]", flush=True)
    elif kind == "usage":
        pass  # quiet; available for metering


def _build_loop(cfg, args) -> Loop:
    if args.provider:
        cfg.provider = args.provider
    if args.model:
        cfg.model = args.model
    # Cloud endpoints need a key; a custom base_url (e.g. LM Studio,
    # llama.cpp) is assumed local and goes without one.
    default_base = PROVIDER_DEFAULTS.get(cfg.provider, {}).get("base_url")
    if not cfg.api_key and cfg.base_url == default_base:
        sys.exit(f"No API key: set {cfg.api_key_env} in your environment.")
    provider = make_provider(cfg)
    return Loop(provider, Budget(cfg.budget_hard, cfg.budget_soft),
                on_event=_print_event)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ctx",
                                 description="Agent loop: context as a file.")
    ap.add_argument("task", nargs="*", help="One-shot task text.")
    ap.add_argument("--new", action="store_true", help="Start a new session.")
    ap.add_argument("--session", help="Open a specific session id.")
    ap.add_argument("--list", action="store_true", help="List sessions.")
    ap.add_argument("--config", action="store_true",
                    help="Write an example config file.")
    ap.add_argument("--provider", choices=["openai", "anthropic"])
    ap.add_argument("--model", help="Model id override.")
    ap.add_argument("--workdir", default=os.getcwd(),
                    help="Working directory for tools.")
    args = ap.parse_args(argv)

    if args.config:
        p = write_example_config()
        print(f"Wrote example config to {p}")
        print("Set your API key env var, then run: python -m harness \"task\"")
        return 0

    cfg = load_config()

    if args.list:
        sessions = _sessions(cfg)
        cur = _current_id(sessions)
        for d in sorted(p.name for p in sessions.iterdir() if p.is_dir()):
            mark = " *" if d == cur else ""
            print(f"{d}{mark}")
        return 0

    if args.new or args.session:
        session = (_new_session(cfg, args.workdir) if args.new
                   else _open_session(cfg, args.session, args.workdir))
    else:
        session = _open_session(cfg, None, args.workdir)
    print(f"[session {session.id}] provider={cfg.provider} model={cfg.model} "
          f"budget={cfg.budget_hard:,} ctx={session.context.path}")

    loop = _build_loop(cfg, args)

    def do_turn(text: str) -> int:
        try:
            loop.run_turn(session, text)
        except BudgetExceeded as e:
            print(f"\nBUDGET EXCEEDED: {e}")
            return 1
        except ProviderError as e:
            print(f"\nPROVIDER ERROR: {e}")
            return 1
        except KeyboardInterrupt:
            print("\n[interrupted]")
            return 130
        return 0

    if args.task:
        return do_turn(" ".join(args.task))

    # REPL
    print("Type your task (empty line quits).")
    while True:
        try:
            text = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            break
        do_turn(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
