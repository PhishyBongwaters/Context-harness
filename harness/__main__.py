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


_spin: list = []


def _spin_start(label: str) -> None:
    from .spinner import Spinner
    _spin_stop()
    sp = Spinner(label)
    sp.start()
    _spin.append(sp)


def _spin_stop() -> None:
    while _spin:
        _spin.pop().stop()


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
    _spin_stop()
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
        bd = data.get("breakdown") or {}
        parts = (f"sys {bd.get('system', 0):,} + "
                 f"chat {bd.get('transcript', 0):,} + "
                 f"tools {bd.get('tools', 0):,}")
        ut = data.get("usage_total") or {}
        sess = (f" | sess in {ut.get('input', 0):,} "
                f"out {ut.get('output', 0):,}") if ut else ""
        print(f"\n[context {toks:,} ({parts}) / {hard:,} ({pct:.0f}%){sess}]",
              flush=True)
        _spin_start("pruning" if (data or {}).get("phase") == "prune"
                    else "thinking")
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


def _show_session(cfg, session: Session, totals=None,
                  project: str | None = None) -> None:
    try:
        toks = session.context.tokens()
    except Exception:
        toks = None
    using = f" using {toks:,} tokens" if toks is not None else ""
    life = ""
    if totals and (totals.get("input") or totals.get("output")):
        life = (f" | lifetime in {totals['input']:,} "
                f"out {totals['output']:,}")
    proj = f" project={project}" if project else ""
    print(f"[session {session.id}{proj}] provider={cfg.provider} "
          f"model={cfg.model} "
          f"budget={cfg.budget_hard:,}{using}{life} "
          f"ctx={session.context.path}")


def _workdir(args) -> str:
    return args.workdir or os.getcwd()


def _latest_project_session(cfg, name: str, workdir: str):
    from .project import session_project
    sessions = _sessions(cfg)
    hits = [p for p in sessions.iterdir()
            if p.is_dir() and session_project(p) == name]
    if not hits:
        return None
    return _open_session(cfg, max(p.name for p in hits), workdir)


REPL_HELP = ("/new [task]  fresh session (runs task when given)\n"
             "/open <id>  switch session (id prefix ok)\n"
             "/project [name]  show/switch project\n"
             "/list        list sessions (* = current)\n"
             "/usage [N]   ledger totals + last N calls (default 5)\n"
             "/config      show effective config (redacted)\n"
             "/providers   list known providers\n"
             "/models      list models from current provider (OpenAI-compatible)\n"
             "/help        this list\n"
             "/quit        leave (empty line also quits)")


def _show_config(cfg) -> None:
    import json
    data = {
        "provider": cfg.provider,
        "model": cfg.model,
        "base_url": cfg.base_url,
        "budget_hard": cfg.budget_hard,
        "budget_soft": cfg.budget_soft,
        "approval_timeout": cfg.approval_timeout,
        "exec_timeout": cfg.exec_timeout,
        "exec_timeout_max": cfg.exec_timeout_max,
        "request_timeout": cfg.request_timeout,
        "usage_note": cfg.usage_note,
        "prune_target": cfg.prune_target,
        "prune_keep_tools": cfg.prune_keep_tools,
        "prune_section_cap": cfg.prune_section_cap,
        "sessions_dir": cfg.sessions_dir,
        "config_file": str(config_path()),
    }
    print("[config]")
    for k, v in data.items():
        print(f"  {k}: {v}")


def _show_providers() -> None:
    from .config import PROVIDER_DEFAULTS
    print("[providers]")
    for name, meta in sorted(PROVIDER_DEFAULTS.items()):
        print(f"  {name}: base_url={meta.get('base_url')} api_key_env={meta.get('api_key_env')}")


def _show_models(cfg) -> None:
    import urllib.request, json
    # Only OpenAI-compatible providers support /v1/models
    if cfg.provider not in ("openai", "nvidia"):
        print(f"[models] provider {cfg.provider} does not support /v1/models listing")
        return
    base = (cfg.base_url or "").rstrip("/")
    if not base:
        print("[models] no base_url configured")
        return
    url = f"{base}/models"
    headers = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data", [])
        print(f"[models] {len(models)} models from {cfg.provider}")
        for m in models[:100]:
            print(f"  {m.get('id')}")
        if len(models) > 100:
            print(f"  ... +{len(models)-100} more")
    except Exception as e:
        print(f"[models] error fetching models: {e}")


def _show_usage(tracker, rest: str) -> None:
    if tracker is None or not tracker.turns:
        print("No usage recorded yet.")
        return
    t = tracker.totals
    print(f"[usage] session in {t['input']:,} out {t['output']:,} "
          f"est {t['estimated']:,} over {len(tracker.turns)} calls")
    try:
        n = int((rest.split() or ["5"])[0])
    except ValueError:
        n = 5
    for turn in tracker.turns[-max(1, n):]:
        bd, sv = turn["breakdown"], turn["server"]
        print(f"  turn {turn['turn']} {turn['phase']}/{turn['step']}: "
              f"est {bd['total']:,} (sys {bd['system']:,} "
              f"chat {bd['transcript']:,} tools {bd['tools']:,}) "
              f"server in {sv['input']:,} out {sv['output']:,}")


def _approver(cfg, args, session, on_event):
    from .approvals import Approver
    timeout = getattr(args, "approval_timeout", None) or cfg.approval_timeout
    return Approver(session.dir, on_event=on_event,
                    approval_timeout=timeout,
                    auto_approve=getattr(args, "yes", False))


def _build_loop(cfg, args, on_event=None, approver=None,
                usage_tracker=None, project=None) -> Loop:
    if args.provider:
        cfg.provider = args.provider
    if args.model:
        cfg.model = args.model
    if getattr(args, "request_timeout", None):
        cfg.request_timeout = args.request_timeout
    if getattr(args, "budget_hard", None):
        cfg.budget_hard = args.budget_hard
    if getattr(args, "budget_soft", None):
        cfg.budget_soft = args.budget_soft
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
                approver=approver, usage_tracker=usage_tracker,
                exec_timeout=getattr(args, "exec_timeout", None)
                or cfg.exec_timeout,
                exec_timeout_max=getattr(args, "exec_timeout_max", None)
                or cfg.exec_timeout_max,
                usage_note=cfg.usage_note
                and not getattr(args, "no_usage_note", False),
                project=project,
                prune_target=cfg.prune_target,
                prune_keep_tools=cfg.prune_keep_tools,
                prune_section_cap=cfg.prune_section_cap)


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
    ap.add_argument("--provider", choices=["openai", "anthropic", "nvidia"])
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
    ap.add_argument("--no-usage-note", action="store_true",
                    help="Omit the ephemeral per-request usage line.")
    ap.add_argument("--request-timeout", type=int, default=None,
                    help="HTTP seconds per model call (default 120; raise "
                         "for huge prompts on slow local servers).")
    ap.add_argument("--budget-hard", type=int, default=None,
                    help="Hard token budget override (default from config).")
    ap.add_argument("--budget-soft", type=int, default=None,
                    help="Soft token budget override (default from config).")
    ap.add_argument("--project", default=None,
                    help="Project name: resume it or start it.")
    ap.add_argument("--workdir", default=None,
                    help="Working directory for tools (default: cwd; with "
                         "--project, sets/updates the project workdir).")
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
    from .project import (resolve_project, session_project,
                          set_session_project)
    from .usage import UsageTracker

    box: dict = {}

    if args.project:
        proj = resolve_project(args.project, workdir=args.workdir)
        box["project"] = proj["name"]
        args.workdir = proj["workdir"]
    else:
        box["project"] = None

    def stamp(sess) -> None:
        if box["project"]:
            set_session_project(sess.dir, box["project"])
        else:
            box["project"] = session_project(sess.dir)

    def attach() -> None:
        sess = box["session"]
        tracker = UsageTracker(sess.dir)
        box["tracker"] = tracker
        # Apply CLI overrides for display before showing session header
        cfg_disp = Config(**cfg.__dict__)
        if args.provider:
            cfg_disp.provider = args.provider
        if args.model:
            cfg_disp.model = args.model
        _show_session(cfg_disp, sess, tracker.totals, box["project"])
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
            approver=_approver(cfg, args, sess, handler),
            usage_tracker=tracker, project=box["project"])

    if args.new or args.session:
        box["session"] = (_new_session(cfg, _workdir(args)) if args.new
                          else _open_session(cfg, args.session,
                                             _workdir(args)))
    else:
        box["session"] = _open_session(cfg, None, _workdir(args))
    stamp(box["session"])
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
        box["session"] = _open_session(cfg, sid, _workdir(args))
        stamp(box["session"])
        attach()

    def switch_project(name: str) -> None:
        proj = resolve_project(name)
        box["project"] = proj["name"]
        args.workdir = proj["workdir"]
        sess = _latest_project_session(cfg, name, _workdir(args))
        box["session"] = (sess if sess is not None
                          else _new_session(cfg, _workdir(args)))
        stamp(box["session"])
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
        elif cmd == "usage":
            _show_usage(box.get("tracker"), rest)
        elif cmd == "new":
            box["session"] = _new_session(cfg, _workdir(args))
            stamp(box["session"])
            attach()
            if rest:
                do_turn(rest)
        elif cmd == "project":
            if not rest:
                from .project import load_registry
                cur = box["project"] or "(none)"
                names = sorted(load_registry())
                print(f"project: {cur}" +
                      (f"  (known: {', '.join(names)})" if names else ""))
            else:
                switch_project(rest.split()[0])
        elif cmd in ("open", "session"):
            if not rest:
                print("usage: /open <id>  (/list to see ids)")
            else:
                sid = _match_session(cfg, rest.split()[0])
                if sid is None:
                    print(f"No unique session matches '{rest}'. (/list)")
                else:
                    switch_session(sid)
        elif cmd == "config":
            _show_config(cfg)
        elif cmd == "providers":
            _show_providers()
        elif cmd == "models":
            _show_models(cfg)
        else:
            print(f"Unknown command /{cmd} (/help).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
