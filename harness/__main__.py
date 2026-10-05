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
    elif kind == "context-diff":
        rec = data["recovered"]
        sign = "+" if rec >= 0 else ""
        print(f"\n[context diff: {sign}{rec:,} tokens]", flush=True)
        for r in data["removed"][:5]:
            print(f"  - {r['header']} ({r['tokens']:,} tokens)", flush=True)
        if len(data["removed"]) > 5:
            print(f"  - ... +{len(data['removed']) - 5} more", flush=True)
        for a in data["added"][:5]:
            print(f"  + {a['header']} ({a['tokens']:,} tokens)", flush=True)
        if len(data["added"]) > 5:
            print(f"  + ... +{len(data['added']) - 5} more", flush=True)
    elif kind == "request" and "tokens_est" in (data or {}):
        toks, hard = data["tokens_est"], data.get("hard") or 0
        pct = 100.0 * toks / hard if hard else 0
        print(f"\n[context {toks:,} / {hard:,} tokens ({pct:.0f}%)]",
              flush=True)
    elif kind == "usage":
        pass  # quiet; available for metering
    elif kind == "approval-result":
        print(f"\n[approval {data.get('decision')} ({data.get('scope')})]",
              flush=True)
    elif kind == "approval-wait":
        pass  # the prompt itself prints; event feeds debug log + notifier


def _require_key(provider_name: str, base_url: str | None,
                 api_key: str | None, key_env: str, role: str) -> None:
    # Cloud endpoints need a key; a custom base_url (e.g. LM Studio,
    # llama.cpp) is assumed local and goes without one.
    default_base = PROVIDER_DEFAULTS.get(provider_name, {}).get("base_url")
    if not api_key and base_url == default_base:
        sys.exit(f"No API key for {role} model: "
                 f"set {key_env} in your environment or .env file.")


def parse_repl_command(text: str) -> tuple[str, str] | None:
    """Split '/cmd rest' -> (cmd, rest). None for non-command input."""
    if not text.startswith("/"):
        return None
    parts = text[1:].split(None, 1)
    if not parts or not parts[0]:
        return None
    return parts[0].lower(), (parts[1].strip() if len(parts) > 1 else "")


def _list_sessions(cfg) -> None:
    sessions = _sessions(cfg)
    cur = _current_id(sessions)
    for d in sorted(p.name for p in sessions.iterdir() if p.is_dir()):
        mark = " *" if d == cur else ""
        print(f"{d}{mark}")


def _match_session(cfg, ident: str) -> str | None:
    """Exact session id, else unique prefix. None when missing/ambiguous."""
    sessions = _sessions(cfg)
    ids = sorted(p.name for p in sessions.iterdir() if p.is_dir())
    if ident in ids:
        return ident
    hits = [i for i in ids if i.startswith(ident)]
    return hits[0] if len(hits) == 1 else None


def _show_session(cfg, session: Session) -> None:
    try:
        toks = session.context.tokens()
    except Exception:
        toks = None
    using = f" using {toks:,} tokens" if toks is not None else ""
    print(f"[session {session.id}] provider={cfg.provider} model={cfg.model} "
          f"budget={cfg.budget_hard:,}{using} ctx={session.context.path}")


REPL_HELP = ("/new [task]  fresh session (runs task when given)\n"
             "/open <id>  switch session (id prefix ok)\n"
             "/list        list sessions (* = current)\n"
             "/help        this list\n"
             "/quit        leave (empty line also quits)")


def _approver(cfg, args, session, on_event):
    from .approvals import Approver
    timeout = getattr(args, "approval_timeout", None) or cfg.approval_timeout
    return Approver(session.dir, on_event=on_event,
                    approval_timeout=timeout,
                    auto_approve=getattr(args, "yes", False))


def _build_loop(cfg, args, on_event=None, approver=None) -> Loop:
    if args.provider:
        cfg.provider = args.provider
    if args.model:
        cfg.model = args.model
    _require_key(cfg.provider, cfg.base_url, cfg.api_key, cfg.api_key_env,
                 "main")
    provider = make_provider(cfg)

    # Janitor model for prune-only turns; each setting falls back to main.
    prune_provider = cfg.prune_provider or cfg.provider
    prune_model = cfg.prune_model or cfg.model
    prune_base_url = cfg.prune_base_url or cfg.base_url
    prune_key_env = (cfg.prune_api_key_env
                     or PROVIDER_DEFAULTS.get(prune_provider, {})
                     .get("api_key_env"))
    prune_key = os.environ.get(prune_key_env) if prune_key_env else None
    _require_key(prune_provider, prune_base_url, prune_key, prune_key_env,
                 "prune")
    prune = make_provider(cfg, provider=prune_provider, model=prune_model,
                           base_url=prune_base_url, api_key=prune_key)

    return Loop(provider, Budget(cfg.budget_hard, cfg.budget_soft),
                on_event=on_event or _print_event, prune_provider=prune,
                approver=approver,
                exec_timeout=getattr(args, "exec_timeout", None)
                or cfg.exec_timeout,
                exec_timeout_max=getattr(args, "exec_timeout_max", None)
                or cfg.exec_timeout_max)


def main(argv: list[str] | None = None) -> int:
    try:  # model output may contain emoji; cp1252 consoles would crash
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
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
    ap.add_argument("--env-file", default=None,
                    help="Path to .env file (default: <config-dir>/.env, then ./.env).")
    ap.add_argument("--debug", action="store_true",
                    help="Write a JSONL debug log next to the transcript.")
    ap.add_argument("--debug-file", default=None,
                    help="Explicit debug log path (implies --debug).")
    ap.add_argument("--yes", action="store_true",
                    help="Auto-approve tool prompts (denylist still denied).")
    ap.add_argument("--approval-timeout", type=int, default=None,
                    help="Seconds to wait for an approval (default 120).")
    ap.add_argument("--exec-timeout", type=int, default=None,
                    help="Default exec runtime in seconds (default 60).")
    ap.add_argument("--exec-timeout-max", type=int, default=None,
                    help="Ceiling on exec runtime in seconds (default 300).")
    ap.add_argument("--workdir", default=os.getcwd(),
                    help="Working directory for tools.")
    args = ap.parse_args(argv)

    if args.config:
        p = write_example_config()
        print(f"Wrote example config to {p}")
        print("Set your API key in .env or env var, then run: python -m harness \"task\"")
        return 0

    cfg = load_config(dotenv_path=args.env_file)

    if args.list:
        _list_sessions(cfg)
        return 0

    from .debug import DebugLog

    box: dict = {}

    def attach() -> None:
        sess = box["session"]
        dbg = None
        if args.debug_file:
            dbg = DebugLog(args.debug_file, session_id=sess.id)
        elif args.debug:
            dbg = DebugLog(sess.dir / "debug.jsonl", session_id=sess.id)
        handler = dbg.handler(_print_event) if dbg else _print_event
        if dbg:
            dbg.write("session", {"id": sess.id,
                                  "provider": cfg.provider, "model": cfg.model,
                                  "ctx": str(sess.context.path)})
            print(f"[debug log {dbg.path}]")
        box["on_event"] = handler
        box["loop"] = _build_loop(
            cfg, args, on_event=handler,
            approver=_approver(cfg, args, sess, handler))

    if args.new or args.session:
        box["session"] = (_new_session(cfg, args.workdir) if args.new
                          else _open_session(cfg, args.session, args.workdir))
    else:
        box["session"] = _open_session(cfg, None, args.workdir)
    _show_session(cfg, box["session"])
    attach()

    def do_turn(text: str) -> int:
        try:
            box["loop"].run_turn(box["session"], text)
        except BudgetExceeded as e:
            box["on_event"]("error", {"type": "budget-exceeded",
                                      "message": str(e)})
            print(f"\nBUDGET EXCEEDED: {e}")
            return 1
        except ProviderError as e:
            box["on_event"]("error", {"type": "provider-error",
                                      "message": str(e)})
            print(f"\nPROVIDER ERROR: {e}")
            return 1
        except KeyboardInterrupt:
            box["on_event"]("error", {"type": "interrupted"})
            print("\n[interrupted]")
            return 130
        return 0

    def switch_session(sid: str) -> None:
        box["session"] = _open_session(cfg, sid, args.workdir)
        _show_session(cfg, box["session"])
        attach()

    if args.task:
        return do_turn(" ".join(args.task))

    # REPL — stays in the current session until /new or /open.
    # Reads via the shared stdin pump so a timed-out approval prompt
    # can never steal the next line (see StdinPump).
    from .approvals import stdin_line
    print("Type your task (/help for commands, empty line quits).")
    while True:
        print("\n> ", end="", flush=True)
        try:
            line = stdin_line()
        except KeyboardInterrupt:
            print()
            break
        if line is None:  # EOF
            print()
            break
        text = line.strip()
        if not text:
            break
        parsed = parse_repl_command(text)
        if parsed is None:
            do_turn(text)
            continue
        cmd, rest = parsed
        if cmd in ("quit", "exit", "q"):
            break
        elif cmd == "help":
            print(REPL_HELP)
        elif cmd == "list":
            _list_sessions(cfg)
        elif cmd == "new":
            box["session"] = _new_session(cfg, args.workdir)
            _show_session(cfg, box["session"])
            attach()
            if rest:
                do_turn(rest)
        elif cmd in ("open", "session"):
            if not rest:
                print("usage: /open <id>  (/list to see ids)")
            else:
                sid = _match_session(cfg, rest.split()[0])
                if sid is None:
                    print(f"No unique session matches '{rest}'. (/list)")
                else:
                    switch_session(sid)
        else:
            print(f"Unknown command /{cmd} (/help).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
