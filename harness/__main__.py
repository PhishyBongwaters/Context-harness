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
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from .config import (DEFAULTS, PROVIDER_DEFAULTS, Config, config_path,
                     load_config, detect_context_window,
                     probe_provider, resolve_provider,
                     save_current_provider, set_active_provider,
                     split_budgets, write_example_config)
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


def _dry_run(cfg, args, sess: Session) -> int:
    """Report what the next turn would cost (S8).

    Assembles the transcript and system prompt, prints per-section
    token counts against the budgets. No provider is created, no model
    calls are made, nothing is written.
    """
    from .assembly import assemble, load_prompt
    from .context import _split_sections, count_tokens
    hard = getattr(args, "budget_hard", None) or cfg.budget_hard
    soft = getattr(args, "budget_soft", None) or cfg.budget_soft
    system = load_prompt(sess.dir,
                         ctx_path=str(sess.dir / "context.md"),
                         hard=hard, soft=soft,
                         workdir=sess.workdir)
    text = assemble(sess.dir)
    sys_toks = count_tokens(system)
    print(f"session: {sess.id}")
    print(f"system prompt: {sys_toks:,} tokens")
    print("transcript sections:")
    for _role, _label, header, body in _split_sections(text):
        sec_toks = count_tokens(f"{header}\n{body}")
        print(f"  {header.strip()}: {sec_toks:,} tokens")
    total = count_tokens(text)
    turn_total = sys_toks + total
    print(f"transcript total: {total:,} tokens")
    print(f"turn total (system + transcript): {turn_total:,} tokens")
    print(f"budgets: soft={soft:,} hard={hard:,}")
    print("verdict: "
          + ("fits" if turn_total <= hard else "OVER HARD BUDGET"))
    return 0


def _open_session(cfg, sid: str | None, workdir: str) -> Session:
    sessions = _sessions(cfg)
    sid = sid or _current_id(sessions)
    if sid is None:
        return _new_session(cfg, workdir)
    _set_current(sessions, sid)
    return Session(id=sid, dir=sessions / sid, workdir=workdir)


def _save_session_model(session: Session, provider: str, model: str) -> None:
    """Persist the last-used provider/model with the session."""
    try:
        meta = session.dir / "meta.json"
        data = {}
        if meta.is_file():
            data = json.loads(meta.read_text(encoding="utf-8"))
        data["provider"] = provider
        data["model"] = model
        meta.write_text(json.dumps(data), encoding="utf-8")
    except Exception:
        pass


def _load_session_model(session: Session) -> tuple:
    """Return (provider, model) saved with the session, or (None, None)."""
    try:
        meta = session.dir / "meta.json"
        if meta.is_file():
            data = json.loads(meta.read_text(encoding="utf-8"))
            return data.get("provider"), data.get("model")
    except Exception:
        pass
    return None, None


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


def sessions_info(cfg) -> list[dict]:
    """Structured session rows, newest-first. Never throws per-row.

    Each row: {id, current, project|None, tokens|None}. Project and
    token reads are best-effort (missing/corrupt -> None); token
    counting never creates a context file.
    """
    from .project import session_project
    sessions = _sessions(cfg)
    cur = _current_id(sessions)
    ids = sorted((p.name for p in sessions.iterdir() if p.is_dir()),
                 reverse=True)
    rows: list[dict] = []
    for sid in ids:
        try:
            proj = session_project(sessions / sid)
        except Exception:
            proj = None
        toks = None
        try:
            from .context import ContextFile
            ctx = sessions / sid / "context.md"
            if ctx.is_file():
                toks = ContextFile(ctx).tokens()
        except Exception:
            toks = None
        rows.append({"id": sid, "current": sid == cur,
                     "project": proj, "tokens": toks})
    return rows


def sessions_lines(cfg) -> list[str]:
    """Session ids for /list, current marked. Pure (no printing)."""
    rows = sorted(sessions_info(cfg), key=lambda r: r["id"])
    return [f"{r['id']}{' *' if r['current'] else ''}" for r in rows]


def _list_sessions(cfg) -> None:
    for line in sessions_lines(cfg):
        print(line)


def _match_session(cfg, ident: str) -> str | None:
    """Exact session id, else unique prefix. None when missing/ambiguous."""
    sessions = _sessions(cfg)
    ids = sorted(p.name for p in sessions.iterdir() if p.is_dir())
    if ident in ids:
        return ident
    hits = [i for i in ids if i.startswith(ident)]
    return hits[0] if len(hits) == 1 else None


def session_header_line(cfg, session: Session, totals=None,
                          project: str | None = None) -> str:
    """One-line session header. Pure (no printing)."""
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
    src = getattr(cfg, "budget_source", "default")
    win = getattr(cfg, "context_window", None)
    if win:
        mid = (f"window={win:,} [{src}] "
               f"prune-at={cfg.budget_hard:,}")
    else:
        mid = f"prune-at={cfg.budget_hard:,} [{src}]"
    return (f"[session {session.id}{proj}] provider={cfg.provider} "
            f"model={cfg.model} "
            f"{mid}{using}{life} "
            f"ctx={session.context.path}")


def _show_session(cfg, session: Session, totals=None,
                  project: str | None = None) -> None:
    print(session_header_line(cfg, session, totals, project))


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
             "/goal \"text\"  add a goal to sats/goals.md\n"
             "/list        list sessions (* = current)\n"
             "/usage [N]   ledger totals + last N calls (default 5)\n"
             "/config      show effective config (redacted)\n"
             "/providers   list known providers (registry entries when set)\n"
             "/providers save-current <name>  snapshot live connection\n"
             "/models      list models from current provider (OpenAI-compatible)\n"
             "/help        this list\n"
             "/quit        leave (empty line also quits)")


def config_lines(cfg) -> list[str]:
    """Effective config, redacted (no api_key). Pure (no printing)."""
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
    return ["[config]"] + [f"  {k}: {v}" for k, v in data.items()]


def _show_config(cfg) -> None:
    for line in config_lines(cfg):
        print(line)


def providers_lines(cfg=None, _probe=None) -> list[str]:
    """Known providers. Pure except the registry reachability dots.

    Registry non-empty: one row per entry (dot, * = active). Else the
    legacy kind list. Dots are display-only, never block.
    """
    providers = getattr(cfg, "providers", None) if cfg is not None else None
    if isinstance(providers, dict) and providers:
        probe = _probe or probe_provider
        lines = ["[providers]"]
        for name in sorted(providers):
            entry = providers[name] or {}
            kind = entry.get("kind", "?")
            model = entry.get("model", "")
            base = entry.get("base_url", "")
            try:
                state = probe(base, kind)
            except Exception:
                state = False
            dot = "●" if state is True else ("○" if state is False else "·")
            star = (" *" if getattr(cfg, "provider", None) == name else "")
            lines.append(f"  {dot} {name}{star}: kind={kind} "
                         f"model={model} base_url={base}")
        return lines
    return (["[providers]"]
            + [f"  {name}: base_url={meta.get('base_url')} "
               f"api_key_env={meta.get('api_key_env')}"
               for name, meta in sorted(PROVIDER_DEFAULTS.items())])


def _show_providers(cfg=None) -> None:
    for line in providers_lines(cfg):
        print(line)


_USE_CFG = object()  # models_lines override sentinel (None is valid)


def models_lines(cfg, *, base_url=_USE_CFG, api_key=_USE_CFG,
                 kind=None) -> list[str]:
    """Models from an OpenAI-compatible provider.

    Pure except the network fetch (TUI runs it in a worker thread).
    Overrides fetch from a CANDIDATE endpoint (picker model step) instead
    of the live cfg: base_url/api_key None reaches a keyless local
    server, kind skips registry resolution. Defaults preserve the old
    live-cfg behavior exactly.
    """
    import urllib.request
    import json
    # Only OpenAI-compatible kinds support /v1/models (registry names
    # resolve to their kind first).
    kind = kind if kind is not None else resolve_provider(cfg).kind
    if kind not in ("openai", "nvidia"):
        return [f"[models] provider {kind} does not support "
                "/v1/models listing"]
    if base_url is _USE_CFG:
        base_url = cfg.base_url
    if api_key is _USE_CFG:
        api_key = cfg.api_key
    base = (base_url or "").rstrip("/")
    if not base:
        return ["[models] no base_url configured"]
    url = f"{base}/models"
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        models = data.get("data", [])
        lines = [f"[models] {len(models)} models from {cfg.provider}"]
        lines += [f"  {m.get('id')}" for m in models[:100]]
        if len(models) > 100:
            lines.append(f"  ... +{len(models)-100} more")
        return lines
    except Exception as e:
        return [f"[models] error fetching models: {e}"]


def _show_models(cfg) -> None:
    for line in models_lines(cfg):
        print(line)


def usage_lines(tracker, rest: str) -> list[str]:
    """Ledger totals + last N calls. Pure (no printing)."""
    if tracker is None or not tracker.turns:
        return ["No usage recorded yet."]
    t = tracker.totals
    lines = [f"[usage] session in {t['input']:,} out {t['output']:,} "
             f"est {t['estimated']:,} over {len(tracker.turns)} calls"]
    try:
        n = int((rest.split() or ["5"])[0])
    except ValueError:
        n = 5
    for turn in tracker.turns[-max(1, n):]:
        bd, sv = turn["breakdown"], turn["server"]
        lines.append(
            f"  turn {turn['turn']} {turn['phase']}/{turn['step']}: "
            f"est {bd['total']:,} (sys {bd['system']:,} "
            f"chat {bd['transcript']:,} tools {bd['tools']:,}) "
            f"server in {sv['input']:,} out {sv['output']:,}")
    return lines


def _show_usage(tracker, rest: str) -> None:
    for line in usage_lines(tracker, rest):
        print(line)


def project_status_lines(current: str | None,
                         known: list[str]) -> list[str]:
    """Current project + known names. Pure (no printing)."""
    cur = current or "(none)"
    suffix = f"  (known: {', '.join(known)})" if known else ""
    return [f"project: {cur}{suffix}"]

def _approver(cfg, args, session, on_event):
    from .approvals import Approver
    timeout = getattr(args, "approval_timeout", None) or cfg.approval_timeout
    return Approver(session.dir, on_event=on_event,
                    approval_timeout=timeout,
                    auto_approve=getattr(args, "yes", False))


def _tui_approver(cfg, args, session, on_event):
    from .tui.approvals import TUIApprover
    timeout = getattr(args, "approval_timeout", None) or cfg.approval_timeout
    return TUIApprover(session.dir, on_event=on_event,
                       approval_timeout=timeout,
                       auto_approve=getattr(args, "yes", False))


def _apply_provider_override(cfg, args, name: str) -> None:
    """Apply a --provider/retarget name: registry entry first, legacy kind
    fallback (with today's base_url-reset semantics). Mutates cfg."""
    providers = getattr(cfg, "providers", None) or {}
    cfg.provider = name
    if isinstance(providers, dict) and name in providers:
        res = resolve_provider(cfg)
        if getattr(args, "base_url", None) is not None:
            cfg.base_url = args.base_url
        else:
            cfg.base_url = res.base_url
        cfg.api_key_env = res.api_key_env
        cfg.api_key = res.api_key
        if getattr(args, "model", None) is None and res.model:
            cfg.model = res.model
        return
    # Legacy kind path, identical to the old inline block.
    if getattr(args, "base_url", None) is None:
        default_url = PROVIDER_DEFAULTS.get(cfg.provider, {}).get("base_url")
        if default_url:
            cfg.base_url = default_url
    default_key_env = PROVIDER_DEFAULTS.get(cfg.provider, {}).get(
        "api_key_env")
    if default_key_env:
        cfg.api_key_env = default_key_env
        cfg.api_key = os.environ.get(cfg.api_key_env)


def _resolved_kind(cfg) -> str:
    """Effective kind for connection decisions (registry name -> kind)."""
    return resolve_provider(cfg).kind


def _prune_timeout(cfg) -> int:
    """HTTP timeout for prune-only calls: explicit prune setting, else
    3x the normal request timeout (prune prompts carry the whole file)."""
    if getattr(cfg, "prune_request_timeout", None):
        return int(cfg.prune_request_timeout)
    return 3 * int(getattr(cfg, "request_timeout", 120))


def _prune_settings(cfg) -> tuple:
    """(kind, model, base_url, key_env, key) for the janitor model.

    Each prune_* falls back to main; a registry name resolves through
    its entry, else the legacy path.
    """
    providers = getattr(cfg, "providers", None) or {}
    prune_name = (getattr(cfg, "prune_provider", None) or cfg.provider)
    if isinstance(providers, dict) and prune_name in providers:
        tmp = SimpleNamespace(provider=prune_name,
                              model=(getattr(cfg, "prune_model", None)
                                     or cfg.model),
                              base_url=None, api_key_env=None, api_key=None,
                              providers=providers)
        res = resolve_provider(tmp)
        base_url = getattr(cfg, "prune_base_url", None) or res.base_url
        key_env = getattr(cfg, "prune_api_key_env", None) or res.api_key_env
        model = getattr(cfg, "prune_model", None) or res.model or cfg.model
        key = os.environ.get(key_env) if key_env else None
        return res.kind, model, base_url, key_env, key
    model = getattr(cfg, "prune_model", None) or cfg.model
    base_url = getattr(cfg, "prune_base_url", None) or cfg.base_url
    if getattr(cfg, "prune_api_key_env", None):
        key_env = cfg.prune_api_key_env
    elif (prune_name == cfg.provider and base_url == cfg.base_url):
        # Janitor is the main model: inherit its key env, which may be a
        # custom value rather than the provider default (fixes #1).
        key_env = cfg.api_key_env
    else:
        key_env = PROVIDER_DEFAULTS.get(prune_name, {}).get("api_key_env")
    key = os.environ.get(key_env) if key_env else None
    return prune_name, model, base_url, key_env, key


def _build_loop(cfg, args, on_event=None, approver=None,
                usage_tracker=None, project=None) -> Loop:
    if args.provider:
        _apply_provider_override(cfg, args, args.provider)
    if args.model:
        cfg.model = args.model
    if getattr(args, "delegate_model", None):
        cfg.delegate_model = args.delegate_model
    if getattr(args, "request_timeout", None):
        cfg.request_timeout = args.request_timeout
    if getattr(args, "budget_hard", None):
        cfg.budget_hard = args.budget_hard
        cfg.budget_hard_auto = False
    if getattr(args, "budget_soft", None):
        cfg.budget_soft = args.budget_soft
        cfg.budget_soft_auto = False
    if getattr(args, "no_prune", False):
        cfg.prune_enabled = False
    kind = _resolved_kind(cfg)
    # Sync display fields from the registry entry (no-op for legacy, so
    # load_config values and CLI overrides keep working untouched).
    res = resolve_provider(cfg)
    if res.registry_hit:
        if getattr(args, "base_url", None) is None:
            cfg.base_url = res.base_url
        cfg.api_key_env = res.api_key_env
        cfg.api_key = res.api_key
        if getattr(args, "model", None) is None and res.model:
            cfg.model = res.model
    _require_key(kind, cfg.base_url, cfg.api_key, cfg.api_key_env,
                 "main")
    provider = make_provider(cfg, provider=kind, model=cfg.model,
                             base_url=cfg.base_url, api_key=cfg.api_key)

    # Janitor model for prune-only turns; each setting falls back to main.
    prune_kind, prune_model, prune_base_url, prune_key_env, prune_key = \
        _prune_settings(cfg)
    _require_key(prune_kind, prune_base_url, prune_key, prune_key_env,
                 "prune")
    prune = make_provider(cfg, provider=prune_kind, model=prune_model,
                           base_url=prune_base_url, api_key=prune_key,
                           timeout=_prune_timeout(cfg))

    return Loop(provider, Budget(cfg.budget_hard, cfg.budget_soft,
                              window=getattr(cfg, "context_window", None)),
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
                prune_section_cap=cfg.prune_section_cap,
                prune_enabled=getattr(cfg, "prune_enabled", True),
                subagent_budget_fraction=cfg.subagent_budget_fraction,
                delegate_model=cfg.delegate_model,
                provider_factory=(
                    lambda provider=None, model=None: make_provider(
                        cfg, provider=provider, model=model)))


def _candidate_for(cfg, name: str) -> SimpleNamespace:
    """Connection preview for the picker model step (never live state).

    The model list must come from the endpoint about to be selected,
    not the currently active one -- fetching from live cfg is how a
    llama.cpp model id ends up confirmed against nvidia. Registry hit
    resolves entry fields; legacy kind resolves kind defaults with the
    key reloaded from the environment (None = keyless local probe).
    Unknown names pass through so models_lines reports them.
    """
    providers = getattr(cfg, "providers", None) or {}
    if isinstance(providers, dict) and name in providers:
        tmp = SimpleNamespace(provider=name, model=None, base_url=None,
                              api_key_env=None, api_key=None,
                              providers=providers)
        res = resolve_provider(tmp)
        return SimpleNamespace(name=name, kind=res.kind,
                               base_url=res.base_url, api_key=res.api_key,
                               model=res.model or "")
    pdefs = PROVIDER_DEFAULTS.get(name)
    if pdefs is None:
        return SimpleNamespace(name=name, kind=name,
                               base_url=getattr(cfg, "base_url", None),
                               api_key=getattr(cfg, "api_key", None),
                               model="")
    key_env = pdefs.get("api_key_env")
    return SimpleNamespace(
        name=name, kind=name, base_url=pdefs.get("base_url"),
        api_key=(os.environ.get(key_env) if key_env else None),
        model=(getattr(cfg, "model", "")
               if getattr(cfg, "provider", None) == name else ""))


def _autosize_budget(cfg, loop, model: str | None,
                     timeout: int = 10, reset: bool = False) -> str | None:
    """Auto-size budget from live server values, unless user set it.

    Chain: live server API first, last-resort registry second
    (get_model_context_window), built-in defaults last. Uses
    budget_hard_auto/budget_soft_auto flags (not == default checks) so
    repeated model switches keep updating auto-detected budgets.
    Returns the winning source ("live"/"registry") or None when nothing
    was detected (or nothing is auto). With reset=True (provider/model
    switch) undetected auto budgets fall back to defaults so a previous
    model's window never sticks; with reset=False (startup probes) they
    are left alone. Pinned budgets are never touched. cfg.budget_source
    always reflects the current values.
    """
    try:
        kind = _resolved_kind(cfg)
        window = detect_context_window(cfg.base_url, model, cfg.api_key,
                                       kind=kind, timeout=timeout)
        source = "live"
        if not window:
            from .config import get_model_context_window
            window = get_model_context_window(model)
            source = "registry"
        if window:
            applied = False
            if getattr(cfg, "budget_hard_auto", True):
                cfg.budget_hard, auto_soft = split_budgets(window)
                if hasattr(loop, "budget") and loop.budget:
                    loop.budget.hard = cfg.budget_hard
                applied = True
            else:
                auto_soft = None
            if getattr(cfg, "budget_soft_auto", True):
                if getattr(cfg, "budget_hard_auto", True):
                    new_soft = auto_soft
                else:
                    # Hard is user-pinned: keep soft at 80% of hard.
                    new_soft = int(cfg.budget_hard * 0.8)
                cfg.budget_soft = new_soft
                if hasattr(loop, "budget") and loop.budget:
                    loop.budget.soft = new_soft
                applied = True
            if applied:
                cfg.budget_source = source
                cfg.context_window = window
                if hasattr(loop, "budget") and loop.budget:
                    loop.budget.window = window
                return source
            return None
        elif reset:
            # Undetectable model on a switch: fall back so the previous
            # model's window never sticks around enforcing the wrong
            # limit. Pinned budgets are untouched.
            if getattr(cfg, "budget_hard_auto", True):
                cfg.budget_hard, auto_soft = split_budgets(
                    DEFAULTS["budget_hard"])
                if hasattr(loop, "budget") and loop.budget:
                    loop.budget.hard = cfg.budget_hard
            if getattr(cfg, "budget_soft_auto", True):
                if getattr(cfg, "budget_hard_auto", True):
                    new_soft = auto_soft
                else:
                    new_soft = int(cfg.budget_hard * 0.8)
                cfg.budget_soft = new_soft
                if hasattr(loop, "budget") and loop.budget:
                    loop.budget.soft = new_soft
            if getattr(cfg, "budget_hard_auto", True):
                # Only relabel when hard itself fell back; a pinned
                # hard keeps its explicit provenance.
                cfg.budget_source = "default"
                cfg.context_window = None
                if hasattr(loop, "budget") and loop.budget:
                    loop.budget.window = None
    except Exception:
        pass
    return None


def _budget_change_lines(cfg, old, model):
    """Honest one-liners from cfg.budget_source (kept accurate by every
    budget writer): live values get a live line, registry fallbacks get
    a labeled fallback line, undetected budgets say so. Unchanged values
    and pinned budgets stay silent.
    """
    new = (cfg.budget_hard, cfg.budget_soft)
    auto = (getattr(cfg, "budget_hard_auto", True)
            or getattr(cfg, "budget_soft_auto", True))
    if not auto:
        return []
    src = getattr(cfg, "budget_source", "default")
    if new != old:
        if src == "registry":
            return [f"[budget] registry fallback for model={model} (API "
                    f"exposes no window): "
                    f"hard={cfg.budget_hard:,} soft={cfg.budget_soft:,}"]
        if src == "live":
            return [f"[budget] live window for model={model}: "
                    f"hard={cfg.budget_hard:,} soft={cfg.budget_soft:,}"]
        return [f"[budget] no live window for model={model} "
                f"(using unconfigured default; set budget_hard "
                f"explicitly)"]
    if src == "default":
        return [f"[budget] no live window for model={model} "
                f"(using unconfigured default; set budget_hard "
                f"explicitly)"]
    return []


def _startup_probe(cfg, loop, timeout: int = 2) -> bool:
    """Bounded localhost-only budget probe for startup. Returns True when
    budgets changed. Never raises, never touches the network unless an
    auto flag is set AND the endpoint is local: localhost answers in ms
    and refused connections fail instantly, so unlike the old unbounded
    cloud probe this cannot stall startup. Cloud endpoints are skipped
    (their APIs expose no window); pinned budgets are skipped.
    """
    try:
        if (not getattr(cfg, "budget_hard_auto", True)
                and not getattr(cfg, "budget_soft_auto", True)):
            return False
        from urllib.parse import urlparse as _up
        host = (_up(getattr(cfg, "base_url", None) or "").hostname
                or "").lower()
        if host not in ("localhost", "127.0.0.1", "::1"):
            return False
        old = (cfg.budget_hard, cfg.budget_soft)
        _autosize_budget(cfg, loop, getattr(cfg, "model", None),
                         timeout=timeout)
        return (cfg.budget_hard, cfg.budget_soft) != old
    except Exception:
        return False


def retarget_loop(box: dict, cfg, args, provider: str, model: str):
    """Retarget the live loop to provider/model without rebuilding.

    Mirrors _build_loop's provider-override semantics exactly: registry
    name first, legacy kind fallback (base_url resets to the new
    provider default unless explicitly set on args), api_key_env
    reloads from the environment, _require_key gates both main and
    prune, prune_* falls back to main. A registry hit also persists
    the model into that entry (registry is the source of truth; CLI
    --model stays ephemeral). Mutates cfg + the live loop in place;
    session/tracker/approver untouched. On failure the old cfg is
    restored and the error propagates (caller keeps old).
    """
    providers = getattr(cfg, "providers", None) or {}
    old_entry = None
    if isinstance(providers, dict) and provider in providers:
        old_entry = dict(providers[provider])
    old = (cfg.provider, cfg.model, cfg.base_url, cfg.api_key_env,
           cfg.api_key)
    cfg.provider = provider
    if old_entry is not None:
        # Registry is the source of truth: persist the confirmed model.
        providers[provider] = {**old_entry, "model": model}
    if getattr(args, "base_url", None) is None:
        if old_entry is not None:
            cfg.base_url = resolve_provider(cfg).base_url
        else:
            default_url = PROVIDER_DEFAULTS.get(cfg.provider, {}).get(
                "base_url")
            if default_url:
                cfg.base_url = default_url
    if old_entry is not None:
        res = resolve_provider(cfg)
        cfg.api_key_env = res.api_key_env
        cfg.api_key = res.api_key
    else:
        default_key_env = PROVIDER_DEFAULTS.get(cfg.provider, {}).get(
            "api_key_env")
        if default_key_env:
            cfg.api_key_env = default_key_env
            cfg.api_key = os.environ.get(cfg.api_key_env)
    cfg.model = model
    try:
        kind = _resolved_kind(cfg)
        _require_key(kind, cfg.base_url, cfg.api_key,
                     cfg.api_key_env, "main")
        new_main = make_provider(cfg, provider=kind, model=cfg.model,
                                 base_url=cfg.base_url, api_key=cfg.api_key)
        prune_kind, prune_model, prune_base_url, prune_key_env, prune_key = \
            _prune_settings(cfg)
        _require_key(prune_kind, prune_base_url, prune_key,
                     prune_key_env, "prune")
        prune = make_provider(cfg, provider=prune_kind,
                              model=prune_model, base_url=prune_base_url,
                              api_key=prune_key,
                              timeout=_prune_timeout(cfg))
    except BaseException:
        (cfg.provider, cfg.model, cfg.base_url, cfg.api_key_env,
         cfg.api_key) = old
        if old_entry is not None:
            providers[provider] = old_entry
        raise
    if old_entry is not None:
        # Best-effort file persist; in-memory entry already updated.
        try:
            from .config import save_provider_entry
            save_provider_entry(config_path(), provider,
                                providers[provider], activate=True)
        except (OSError, ValueError):
            pass
    loop = box["loop"]
    loop.provider = new_main
    loop.prune_provider = prune
    _autosize_budget(cfg, loop, model, reset=True)
    # Persist the selection (name or kind) so the file matches live
    # state -- otherwise the next launch resurrects the old endpoint
    # and the switch looks "stuck". Best-effort, never fails the turn.
    try:
        set_active_provider(config_path(), provider)
    except (OSError, ValueError):
        pass
    return loop


def _restore_session_model(box: dict, cfg, args) -> bool:
    """If the session saved a provider/model, retarget to it.

    Called after a session is opened so reloading restores the last
    used model. No-op if nothing saved or already current. Returns True
    when it retargeted (callers refresh the header: attach() paints
    before this runs, so without a refresh the display shows the
    pre-restore budgets).
    """
    sess = box.get("session")
    if sess is None or box.get("loop") is None:
        return False
    try:
        provider, model = _load_session_model(sess)
    except Exception:
        return False
    if not provider or not model:
        return False
    if provider == cfg.provider and model == cfg.model:
        return False
    try:
        retarget_loop(box, cfg, args, provider, model)
    except Exception:
        return False
    return True


def _enable_windows_ansi() -> None:
    """Enable ANSI escape-sequence processing on Windows consoles.

    Plain print() of \\x1b[... sequences shows as garbage text on conhost
    unless ENABLE_VIRTUAL_TERMINAL_PROCESSING is set. No-op on non-Windows
    and where ANSI already works (Windows Terminal, VS Code). Never raises:
    console setup must not break startup.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong(0)
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            if not mode.value & 0x0004:  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
                kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    try:  # model output may contain emoji; cp1252 consoles would crash
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError, OSError):
        pass
    _enable_windows_ansi()  # conhost: interpret \\x1b[... instead of printing them
    ap = argparse.ArgumentParser(prog="ctx",
                                 description="Agent loop: context as a file.")
    ap.add_argument("task", nargs="*", help="One-shot task text.")
    ap.add_argument("--new", action="store_true", help="Start a new session.")
    ap.add_argument("--session", help="Open a specific session id.")
    ap.add_argument("--list", action="store_true", help="List sessions.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Assemble the session transcript and report token "
                         "costs vs budgets without creating a provider or "
                         "making any model calls. Writes nothing.")
    ap.add_argument("--goal", default=None, metavar="TEXT",
                    help="Add a goal to sats/goals.md in the session "
                         "(--session or current), then exit.")
    ap.add_argument("--config", action="store_true",
                    help="Write an example config file.")
    ap.add_argument("--provider",
                      help="Registry name or kind "
                           "(openai/anthropic/nvidia); registry first, "
                           "legacy kind fallback.")
    ap.add_argument("--model", help="Model id override.")
    ap.add_argument("--env-file", default=None,
                    help="Path to .env file (default: <config-dir>/.env, then ./.env).")
    ap.add_argument("--debug", action="store_true",
                    help="Write a JSONL debug log next to the transcript.")
    ap.add_argument("--debug-file", default=None,
                    help="Explicit debug log path (implies --debug).")
    ap.add_argument("--tui", action="store_true",
                     help="Run the optional Textual TUI (needs the textual extra).")
    ap.add_argument("--no-mouse", action="store_true",
                     help="TUI: don't capture the mouse, so the terminal's "
                          "own selection/copy works (keyboard still drives "
                          "everything in-app).")
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
    ap.add_argument("--no-prune", action="store_true",
                    help="Naive mode (condition A): disable all pruning, "
                         "dedupe, and archive. Append-only; truncate oldest "
                         "at window when over. For A/B evaluation.")
    ap.add_argument("--delegate-model", default=None,
                    metavar="PROVIDER/MODEL",
                    help="Subagent model override ('provider/model' or bare "
                         "'model'; default from config delegate_model).")
    ap.add_argument("--project", default=None,
                    help="Project name: resume it or start it.")
    ap.add_argument("--workdir", default=None,
                    help="Working directory for tools (default: cwd; with "
                         "--project, sets/updates the project workdir).")
    args = ap.parse_args(argv)
    is_tui = getattr(args, "tui", False)
    if is_tui:  # deferred import: CLI path never touches textual
        from .tui import has_tui
        if not has_tui():
            print("TUI needs the extra: "
                  "pip install -r requirements-tui.txt "
                  "then run: python -m harness --tui",
                  file=sys.stderr)
            return 2

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

    if is_tui:
        from .tui.bridge import TuiBridge
        box["bridge"] = TuiBridge(user_name=cfg.user_name,
                                  assistant_name=cfg.assistant_name)

    def _disp_cfg():
        # CLI overrides applied for display (header parity with attach).
        cfg_disp = Config(**cfg.__dict__)
        if args.provider:
            cfg_disp.provider = args.provider
        if args.model:
            cfg_disp.model = args.model
        return cfg_disp

    def attach() -> None:
        sess = box["session"]
        tracker = UsageTracker(sess.dir)
        box["tracker"] = tracker
        # Apply CLI overrides for display before showing session header
        if is_tui:
            # TUI: stash for the system panel instead of printing.
            box["tui_session_header"] = session_header_line(
                _disp_cfg(), sess, tracker.totals, box["project"])
        else:
            _show_session(_disp_cfg(), sess, tracker.totals, box["project"])
        dbg = None
        if args.debug_file:
            dbg = DebugLog(args.debug_file, session_id=sess.id)
        elif args.debug:
            dbg = DebugLog(sess.dir / "debug.jsonl", session_id=sess.id)
        if is_tui:
            handler = (dbg.handler(box["bridge"]) if dbg
                       else box["bridge"])
        else:
            handler = dbg.handler(_print_event) if dbg else _print_event
        if dbg:
            dbg.write("session", {"id": sess.id,
                                  "provider": cfg.provider, "model": cfg.model,
                                  "ctx": str(sess.context.path)})
            print(f"[debug log {dbg.path}]")
        box["on_event"] = handler
        if is_tui:  # TUI never touches StdinPump (see TUIApprover)
            approver = _tui_approver(cfg, args, sess, handler)
        else:
            approver = _approver(cfg, args, sess, handler)
        box["loop"] = _build_loop(
            cfg, args, on_event=handler,
            approver=approver,
            usage_tracker=tracker, project=box["project"])
        # Live values before first paint: localhost-only, bounded, so the
        # session header shows the detected window instead of defaults.
        _startup_probe(cfg, box["loop"])
        # Header paints after the probe so it shows live values, not
        # defaults (CLI prints it, TUI stashes it for the system panel).
        if is_tui:
            # TUI: stash for the system panel instead of printing.
            box["tui_session_header"] = session_header_line(
                _disp_cfg(), sess, tracker.totals, box["project"])
        else:
            _show_session(_disp_cfg(), sess, tracker.totals, box["project"])

    if args.new or args.session:
        box["session"] = (_new_session(cfg, _workdir(args)) if args.new
                          else _open_session(cfg, args.session,
                                             _workdir(args)))
    else:
        box["session"] = _open_session(cfg, None, _workdir(args))
    stamp(box["session"])
    if getattr(args, "dry_run", False):
        return _dry_run(cfg, args, box["session"])
    if getattr(args, "goal", None):
        from .session import add_goal
        res = add_goal(box["session"].dir, args.goal)
        if res == "added":
            print(f"[goal added to {box['session'].id}: {args.goal}]")
        elif res == "exists":
            print(f"[goal already active in {box['session'].id}]")
        else:
            print("error: empty goal text", file=sys.stderr)
            return 2
        return 0
    attach()
    if _restore_session_model(box, cfg, args):
        # Restore retargeted behind attach()'s back: repaint so the
        # header shows the restored model's budgets, not stale ones.
        if is_tui:
            box["tui_session_header"] = session_header_line(
                _disp_cfg(), box["session"],
                box["tracker"].totals, box["project"])
        else:
            _show_session(_disp_cfg(), box["session"],
                          box["tracker"].totals, box["project"])

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
        if _restore_session_model(box, cfg, args):
            if is_tui:
                box["tui_session_header"] = session_header_line(
                    _disp_cfg(), box["session"],
                    box["tracker"].totals, box["project"])
            else:
                _show_session(_disp_cfg(), box["session"],
                              box["tracker"].totals, box["project"])

    def switch_project(name: str) -> None:
        proj = resolve_project(name)
        box["project"] = proj["name"]
        args.workdir = proj["workdir"]
        sess = _latest_project_session(cfg, name, _workdir(args))
        box["session"] = (sess if sess is not None
                          else _new_session(cfg, _workdir(args)))
        stamp(box["session"])
        attach()

    if is_tui:
        from .tui.app import run_app

        def _tui_header() -> str:
            return session_header_line(
                _disp_cfg(), box["session"],
                box.get("tracker").totals if box.get("tracker") else None,
                box["project"])

        def _tui_new(rest: str) -> list[str]:
            box["session"] = _new_session(cfg, _workdir(args))
            stamp(box["session"])
            attach()
            return [_tui_header()]

        def _tui_open(ident: str) -> list[str]:
            sid = _match_session(cfg, ident.split()[0])
            if sid is None:
                return [f"No unique session matches '{ident}'. (/list)"]
            switch_session(sid)
            return [_tui_header()]

        def _tui_project(rest: str) -> list[str]:
            if not rest:
                from .project import load_registry
                return project_status_lines(
                    box["project"], sorted(load_registry()))
            switch_project(rest.split()[0])
            return [_tui_header()]

        def _tui_debug_path():
            if args.debug_file:
                return Path(args.debug_file)
            if args.debug:
                return box["session"].dir / "debug.jsonl"
            return None

        def _tui_retarget(provider: str, model: str) -> list[str]:
            old = (cfg.budget_hard, cfg.budget_soft)
            retarget_loop(box, cfg, args, provider, model)
            # Persist with the session so reloading restores it.
            try:
                _save_session_model(box["session"], provider, model)
            except Exception:
                pass
            # Keep CLI-override display in sync so the header
            # reflects the picker (not stale args).
            try:
                args.provider = provider
                args.model = model
            except Exception:
                pass
            lines = _budget_change_lines(cfg, old, model)
            lines.append(_tui_header())
            return lines

        def _tui_detect_budget() -> list[str]:
            """Live-detect the initial model's window (TUI worker thread).

            Startup never blocks on the network: the TUI fires this in
            a background thread on mount. Skipped when both budgets are
            user-pinned (nothing to detect).
            """
            if (not getattr(cfg, "budget_hard_auto", True)
                    and not getattr(cfg, "budget_soft_auto", True)):
                return []
            old = (cfg.budget_hard, cfg.budget_soft)
            _autosize_budget(cfg, box["loop"], cfg.model)
            lines = _budget_change_lines(cfg, old, cfg.model)
            if (cfg.budget_hard, cfg.budget_soft) != old:
                lines.append(_tui_header())
            return lines

        control = SimpleNamespace(
            do_new=_tui_new,
            do_open=_tui_open,
            do_project=_tui_project,
            do_retarget=_tui_retarget,
            do_detect_budget=_tui_detect_budget,
            list_lines=lambda: sessions_lines(cfg),
            sessions_info=lambda: sessions_info(cfg),
            usage_lines=lambda rest: usage_lines(box.get("tracker"), rest),
            config_lines=lambda: config_lines(cfg),
            providers_lines=lambda: providers_lines(cfg),
            save_current_lines=lambda name: save_current_provider(cfg,
                                                                  name),
            models_lines=lambda: models_lines(cfg),
            candidate_for=lambda name: _candidate_for(cfg, name),
            models_lines_for=lambda cand: models_lines(
                cfg, base_url=cand.base_url, api_key=cand.api_key,
                kind=cand.kind),
            get_tracker=lambda: box.get("tracker"),
            get_provider_model=lambda: (cfg.provider, cfg.model),
            sync_state=lambda: (box["loop"], box["session"]),
            debug_path=_tui_debug_path,
        )
        return run_app(box["loop"], box["session"], box["bridge"],
                       provider_name=cfg.provider, model=cfg.model,
                       initial=" ".join(args.task) or None,
                       cfg=cfg, control=control,
                       session_header=box.get("tui_session_header"),
                       debug_path_getter=_tui_debug_path,
                       mouse=not getattr(args, "no_mouse", False))

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
                for line in project_status_lines(
                        box["project"], sorted(load_registry())):
                    print(line)
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
            if rest.startswith("save-current"):
                parts = rest.split()
                name = parts[1] if len(parts) > 1 else ""
                for line in save_current_provider(cfg, name):
                    print(line)
            else:
                _show_providers(cfg)
        elif cmd == "models":
            _show_models(cfg)
        else:
            print(f"Unknown command /{cmd} (/help).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
