"""Textual app (Phase 3): transcript + budget bar + status + modal,
slash parity with the CLI REPL, read-only views, debug tail toggle.

Worker threads run loop.run_turn; UI updates only via call_from_thread.
Never touches StdinPump. Without textual installed, degrades to a hint.
"""
from __future__ import annotations

import threading
import time
import traceback

try:
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.screen import ModalScreen
    from textual.widgets import (Button, Footer, Header, Input, Label,
                                   OptionList, RichLog, Static)
    from textual.widgets.option_list import Option

    _HAS = True
except ImportError:  # textual extra missing
    _HAS = False

# Stdlib-only picker helpers (no Textual needed).


def format_session_row(row: dict) -> str:
    """One picker line: current marker, id, project tag, tokens."""
    mark = "*" if row.get("current") else " "
    proj = f" [{row['project']}]" if row.get("project") else ""
    toks = row.get("tokens")
    tk = f" {toks:,} tokens" if isinstance(toks, int) else ""
    return f"{mark} {row.get('id', '?')}{proj}{tk}"


TUI_KEYS_HELP = [
    "[tui keys]",
    "  ctrl+s sessions   ctrl+o provider/model   ctrl+d debug tail",
    "  ctrl+e export transcript to a file (copy from there)",
    "  drag with the mouse to select transcript text, ctrl+c copies",
    "  a/s/d/esc in approval + picker dialogs",
]


def parse_model_ids(lines) -> list[str]:
    """Model ids from models_lines output (skip headers/errors)."""
    ids: list[str] = []
    for line in lines or []:
        if isinstance(line, str) and line.startswith("  "):
            s = line.strip()
            if s and not s.startswith("..."):
                ids.append(s)
    return ids


def format_provider_row(name: str, current: str | None,
                        dot=None, detail: str = "") -> str:
    mark = "*" if name == current else " "
    if dot is None and not detail:
        return f"{mark} {name}"
    glyph = "●" if dot is True else ("○" if dot is False else "·")
    return f"{mark} {glyph} {name}{detail}"


def provider_entry_rows(cfg) -> list:
    """Registry rows for the picker: sorted, active flagged, dots unset.

    Dots are filled in by ProviderScreen._probe (worker thread); the
    picker never blocks on the network to open.
    """
    providers = getattr(cfg, "providers", None) or {}
    current = getattr(cfg, "provider", None)
    rows = []
    for name in sorted(providers):
        entry = providers[name] or {}
        rows.append({"name": name, "kind": entry.get("kind"),
                     "model": entry.get("model") or "",
                     "base_url": entry.get("base_url"),
                     "active": name == current, "dot": None})
    return rows


def format_registry_row(entry: dict, current: str | None = None) -> str:
    """One picker line: active marker, reachability dot, kind/model."""
    name = entry.get("name", "?")
    if current is None and entry.get("active"):
        current = name
    mark = "*" if name == current else " "
    dot = entry.get("dot")
    glyph = "●" if dot is True else ("○" if dot is False else "·")
    kind = entry.get("kind") or "?"
    model = entry.get("model") or ""
    detail = f"{kind}/{model}" if model else kind
    return f"{mark} {glyph} {name} ({detail})"


def validate_new_provider(name: str, entry: dict,
                          existing=None) -> str | None:
    """Add-flow validation: None when ok, else an inline error string."""
    from ..config import validate_provider_entry
    try:
        validate_provider_entry(name, entry, existing or {},
                                require_unique=True)
    except ValueError as e:
        return str(e)
    return None


def prefill_from_template(template_id: str | None) -> dict:
    """Form defaults from a PROVIDER_TEMPLATES entry (or blank custom)."""
    from ..config import PROVIDER_TEMPLATES
    data = {"name": "", "kind": "openai", "base_url": "",
            "model": "", "api_key_env": ""}
    if template_id and template_id in PROVIDER_TEMPLATES:
        tpl = PROVIDER_TEMPLATES[template_id]
        data.update({"name": template_id, "kind": tpl.get("kind") or "openai",
                     "base_url": tpl.get("base_url") or "",
                     "model": tpl.get("model") or "",
                     "api_key_env": tpl.get("api_key_env") or ""})
    return data


ADD_PROVIDER_ID = "__add__"
BLANK_TEMPLATE_ID = "__blank__"

if _HAS:
    from .approvals import TUIApprover, approval_brief
    from .bridge import run_turn_in_thread
    from . import commands
    from .widgets import (DEBUG_TAIL_LINES, BudgetBar, DebugPanel,
                          StatusLine, TranscriptDedupe, budget_bar_status,
                          budget_bar_text, debug_panel_lines, format_status)


    class ApprovalScreen(ModalScreen):
        """Mutating-tool approval: a=turn, s=session, d/esc=deny.

        Resolves via the box event the worker's decide() waits on; the
        countdown mirrors approval_timeout and denies on expiry.
        """

        BINDINGS = [("a", "approve_turn", "Approve turn"),
                    ("s", "approve_session", "Approve session"),
                    ("d", "deny", "Deny"),
                    ("escape", "deny", "Deny")]

        CSS = ("ApprovalScreen { align: center middle; } "
               "#approval-box { width: 64; height: auto; "
               "border: thick $primary; background: $surface; padding: 1 2; } "
               "#approval-title { text-style: bold; } "
               "#approval-count { color: $warning; }")

        def __init__(self, info: dict, box: dict):
            super().__init__()
            self._info = info
            self._box = box
            self._left = max(1, int(info.get("timeout") or 1))

        def compose(self) -> "ComposeResult":
            brief = approval_brief(self._info.get("args"))
            title = f"[approval needed] {self._info.get('tool', '?')}"
            if brief:
                title += f" {brief}"
            with Vertical(id="approval-box"):
                yield Label(title, id="approval-title")
                yield Label(f"reason: {self._info.get('reason', '')}",
                            id="approval-reason")
                yield Label("", id="approval-count")
                yield Label("(a)pprove turn / (s)ession / (d)eny",
                            id="approval-keys")

        def on_mount(self) -> None:
            self._show_count()
            self.set_interval(1.0, self._tick)

        def _show_count(self) -> None:
            self.query_one("#approval-count", Label).update(
                f"default deny in {self._left}s")

        def _tick(self) -> None:
            self._left -= 1
            if self._left <= 0:
                self._resolve("deny")
            else:
                self._show_count()

        def _resolve(self, ans: str) -> None:
            if self._box["event"].is_set():
                return
            self._box["answer"] = ans
            self._box["event"].set()
            self.app.pop_screen()

        def action_approve_turn(self) -> None:
            self._resolve("turn")

        def action_approve_session(self) -> None:
            self._resolve("session")

        def action_deny(self) -> None:
            self._resolve("deny")


    PICKER_PROVIDERS = ("openai", "anthropic", "nvidia")


    class SessionPickerScreen(ModalScreen):
        """Session list, newest-first. Enter opens, n makes new."""

        BINDINGS = [("escape", "close", "Close"),
                    ("n", "new_session", "New session")]

        CSS = ("SessionPickerScreen { align: center middle; } "
               "#sess-box { width: 72; height: 24; "
               "border: thick $primary; background: $surface; padding: 1 2; }")

        def __init__(self, rows):
            super().__init__()
            self._rows = list(rows or [])

        def compose(self) -> "ComposeResult":
            with Vertical(id="sess-box"):
                yield Label("[sessions] enter=open, n=new, esc=close",
                            id="sess-title")
                yield OptionList(id="sessions")
                yield Label("current marked *", id="sess-hint")

        def on_mount(self) -> None:
            opts = [Option(format_session_row(r), id=r.get("id"))
                    for r in self._rows]
            lst = self.query_one("#sessions", OptionList)
            if opts:
                lst.add_options(opts)
            else:
                lst.add_option(Option("(no sessions)", id="__none__"))
            lst.focus()

        def choose(self, sid: str) -> None:
            if sid and sid != "__none__":
                run = getattr(self.app, "_open_picked_session", None)
                if callable(run):
                    run(sid)
            try:
                self.app.pop_screen()
            except Exception:
                pass

        def on_option_list_option_selected(
                self, event: "OptionList.OptionSelected") -> None:
            self.choose(event.option_id)

        def action_close(self) -> None:
            self.app.pop_screen()

        def action_new_session(self) -> None:
            run = getattr(self.app, "_picker_new", None)
            if callable(run):
                run("")
            try:
                self.app.pop_screen()
            except Exception:
                pass


    class ProviderScreen(ModalScreen):
        """Step 1: registry entries (+ add row); legacy kinds when empty.

        Selecting an entry dives to the model list; selecting the add
        row opens the template list. Dots probe in a worker thread and
        refresh via call_from_thread (display-only, never blocks).
        """

        BINDINGS = [("escape", "close", "Close")]

        CSS = ("ProviderScreen { align: center middle; } "
               "#prov-box { width: 64; height: auto; "
               "border: thick $primary; background: $surface; padding: 1 2; }")

        def __init__(self, current: str | None = None, entries=None):
            super().__init__()
            self._current = current
            self._entries = entries  # None = legacy kind list

        def compose(self) -> "ComposeResult":
            with Vertical(id="prov-box"):
                yield Label("[provider] enter=next, esc=close",
                            id="prov-title")
                yield OptionList(id="providers")

        def _row_options(self):
            # Registry entries AND legacy kinds are always listed: kinds
            # are connection targets too (nvidia must never vanish just
            # because an entry exists). An entry named exactly like a
            # kind takes that row (registry wins on choose).
            entries = self._entries or []
            opts = [Option(format_registry_row(e, self._current),
                           id=e["name"]) for e in entries]
            have = {e["name"] for e in entries}
            opts += [Option(format_provider_row(p, self._current), id=p)
                     for p in PICKER_PROVIDERS if p not in have]
            opts.append(Option("+ add provider", id=ADD_PROVIDER_ID))
            return opts

        def on_mount(self) -> None:
            lst = self.query_one("#providers", OptionList)
            lst.add_options(self._row_options())
            lst.focus()
            if self._entries:
                threading.Thread(target=self._probe, daemon=True,
                                 name="tui-provider-probe").start()

        def _probe(self) -> None:
            from ..config import probe_provider
            dots = {}
            for entry in self._entries or []:
                try:
                    dots[entry["name"]] = probe_provider(
                        entry.get("base_url"), entry.get("kind"))
                except Exception:  # noqa: BLE001 - dot is best-effort
                    dots[entry["name"]] = False
            try:
                self.app.call_from_thread(self.set_dots, dots)
            except Exception:
                pass

        def set_dots(self, dots) -> None:
            for entry in self._entries or []:
                if entry["name"] in (dots or {}):
                    entry["dot"] = dots[entry["name"]]
            try:
                lst = self.query_one("#providers", OptionList)
                lst.clear_options()
                lst.add_options(self._row_options())
            except Exception:
                pass

        def choose(self, pid: str) -> None:
            if pid == ADD_PROVIDER_ID:
                run = getattr(self.app, "_provider_add", None)
                if callable(run):
                    run()
                return
            run = getattr(self.app, "_provider_chosen", None)
            if callable(run):
                run(pid)
            # _provider_chosen pushes ModelScreen (which replaces us).

        def on_option_list_option_selected(
                self, event: "OptionList.OptionSelected") -> None:
            if event.option_id:
                self.choose(event.option_id)

        def action_close(self) -> None:
            self.app.pop_screen()


    class ProviderTemplateScreen(ModalScreen):
        """Add-flow step 1: pick a template (or blank custom)."""

        BINDINGS = [("escape", "close", "Close")]

        CSS = ("ProviderTemplateScreen { align: center middle; } "
               "#tpl-box { width: 64; height: auto; "
               "border: thick $primary; background: $surface; padding: 1 2; }")

        def compose(self) -> "ComposeResult":
            with Vertical(id="tpl-box"):
                yield Label("[new provider] pick a template, esc=back",
                            id="tpl-title")
                yield OptionList(id="templates")

        def on_mount(self) -> None:
            from ..config import PROVIDER_TEMPLATES
            lst = self.query_one("#templates", OptionList)
            lst.add_options(
                [Option(f"  {tid}", id=tid)
                 for tid in sorted(PROVIDER_TEMPLATES)]
                + [Option("  blank (custom)", id=BLANK_TEMPLATE_ID)])
            lst.focus()

        def choose(self, tid: str) -> None:
            run = getattr(self.app, "_provider_template_chosen", None)
            if callable(run):
                run(tid)

        def on_option_list_option_selected(
                self, event: "OptionList.OptionSelected") -> None:
            if event.option_id:
                self.choose(event.option_id)

        def action_close(self) -> None:
            self.app.pop_screen()


    class ProviderAddScreen(ModalScreen):
        """Add-flow step 2: form prefilled from the template."""

        BINDINGS = [("escape", "close", "Close")]

        CSS = ("ProviderAddScreen { align: center middle; } "
               "#add-box { width: 72; height: auto; "
               "border: thick $primary; background: $surface; padding: 1 2; } "
               "#add-error { color: $error; text-style: bold; }")

        def __init__(self, prefill=None):
            super().__init__()
            if isinstance(prefill, str):
                self._prefill = prefill_from_template(prefill)
            elif isinstance(prefill, dict):
                self._prefill = dict(prefill)
            else:
                self._prefill = prefill_from_template(None)

        def compose(self) -> "ComposeResult":
            p = self._prefill
            with Vertical(id="add-box"):
                yield Label("[new provider] edit fields, enter=save, "
                            "esc=back", id="add-title")
                yield Label("name", id="add-name-lbl")
                yield Input(value=p.get("name", ""), id="add-name")
                yield Label("kind (openai/anthropic/nvidia)", id="add-kind-lbl")
                yield Input(value=p.get("kind", ""), id="add-kind")
                yield Label("base_url", id="add-base-lbl")
                yield Input(value=p.get("base_url", ""), id="add-base")
                yield Label("model", id="add-model-lbl")
                yield Input(value=p.get("model", ""), id="add-model")
                yield Label("api_key_env (empty = local, no key)",
                            id="add-key-lbl")
                yield Input(value=p.get("api_key_env", ""), id="add-key")
                yield Label("", id="add-error")
                yield Label("tab=next field, enter in any field saves, "
                            "esc=back without saving",
                            id="add-hint")
                with Vertical(id="add-btns"):
                    yield Button("Save", id="add-save", variant="primary")
                    yield Button("Back", id="add-back")

        def on_mount(self) -> None:
            try:
                self.query_one("#add-name", Input).focus()
            except Exception:
                pass

        def _data(self) -> dict:
            def _val(qid: str) -> str:
                try:
                    return self.query_one(qid, Input).value
                except Exception:
                    return ""
            return {"name": _val("#add-name"), "kind": _val("#add-kind"),
                    "base_url": _val("#add-base"), "model": _val("#add-model"),
                    "api_key_env": _val("#add-key")}

        def _submit(self) -> None:
            run = getattr(self.app, "_provider_add_save", None)
            err = run(self._data()) if callable(run) else "unavailable"
            if err:
                try:
                    self.query_one("#add-error", Label).update(
                        f"[error: {err}]")
                except Exception:
                    pass
            # On success the app pops this screen (plus template +
            # provider screens) after retargeting.

        def on_button_pressed(self, event: "Button.Pressed") -> None:
            if event.button.id == "add-save":
                self._submit()
            else:
                self.action_close()

        def on_input_submitted(
                self, event: "Input.Submitted") -> None:
            self._submit()

        def action_close(self) -> None:
            self.app.pop_screen()


    class ModelScreen(ModalScreen):
        """Step 2: model list (worker fetch) + custom input."""

        BINDINGS = [("escape", "close", "Close")]

        CSS = ("ModelScreen { align: center middle; } "
               "#model-box { width: 72; height: 26; "
               "border: thick $primary; background: $surface; padding: 1 2; }")

        def __init__(self, provider: str, current_model: str = "",
                     control=None, cfg=None, candidate=None):
            super().__init__()
            self._provider = provider
            self._current_model = current_model or ""
            self._control = control
            self._cfg = cfg
            # Candidate endpoint: the model list must come from the
            # endpoint about to be selected, never the live cfg.
            self._candidate = candidate

        def compose(self) -> "ComposeResult":
            base = (getattr(self._candidate, "base_url", None)
                    if self._candidate is not None else None)
            title = (f"[models: {self._provider} @ {base}]" if base
                     else f"[models: {self._provider}]")
            with Vertical(id="model-box"):
                yield Label(title, id="model-title")
                yield Label("fetching models...", id="model-status")
                yield OptionList(id="models")
                yield Input(value=self._current_model,
                            placeholder="custom model id, Enter to use",
                            id="model-input")
                yield Label("enter=use selected/typed, esc=back",
                            id="model-hint")

        def on_mount(self) -> None:
            self.query_one("#model-input", Input).focus()
            threading.Thread(target=self._fetch, daemon=True,
                             name="tui-models-picker").start()

        def _fetch_lines(self):
            # Candidate endpoint first: the list must describe the
            # provider being switched TO. Live cfg is the fallback
            # (older controls without the hook, or no candidate).
            if self._candidate is not None and self._control is not None:
                fn = getattr(self._control, "models_lines_for", None)
                if callable(fn):
                    try:
                        return list(fn(self._candidate))
                    except Exception as e:  # noqa: BLE001 - shown
                        return [f"[models] error fetching models: {e}"]
            try:
                if self._control is not None and hasattr(
                        self._control, "models_lines"):
                    return list(self._control.models_lines())
                if self._cfg is not None:
                    from ..__main__ import models_lines
                    return list(models_lines(self._cfg))
            except Exception as e:  # noqa: BLE001 - shown, not raised
                return [f"[models] error fetching models: {e}"]
            return ["[models] unavailable"]

        def _fetch(self) -> None:
            lines = self._fetch_lines()
            try:
                self.app.call_from_thread(self.set_models, lines)
            except Exception:
                pass

        def set_models(self, lines) -> None:
            try:
                status = self.query_one("#model-status", Label)
                lst = self.query_one("#models", OptionList)
            except Exception:
                return
            ids = parse_model_ids(lines)
            err = next((l for l in (lines or [])
                        if isinstance(l, str) and l.startswith("[models]")),
                       "")
            if ids:
                status.update(f"{err} (or type custom below)"
                              if err else f"{len(ids)} models")
                lst.add_options([Option(m, id=m) for m in ids])
                lst.add_option(Option("custom... (use input box)",
                                      id="__custom__"))
            else:
                status.update((err or "[models] no models listed — "
                               "type custom below"))

        def confirm(self, model: str) -> None:
            model = (model or "").strip() or self._current_model
            if model == "__custom__":
                try:
                    model = (self.query_one("#model-input",
                                            Input).value.strip()
                             or self._current_model)
                except Exception:
                    model = self._current_model
            if not model:
                return
            run = getattr(self.app, "_retarget_provider_model", None)
            if callable(run):
                run(self._provider, model)
            try:
                self.app.pop_screen()  # model screen
                # Also drop the provider screen underneath, if present.
                if isinstance(self.app.screen, ProviderScreen):
                    self.app.pop_screen()
            except Exception:
                pass

        def on_option_list_option_selected(
                self, event: "OptionList.OptionSelected") -> None:
            if event.option_id:
                self.confirm(event.option_id)

        def on_input_submitted(
                self, event: "Input.Submitted") -> None:
            self.confirm(event.value)

        def action_close(self) -> None:
            self.app.pop_screen()


    class HarnessApp(App):
        CSS = ("#transcript { height: 1fr; } #budget { height: 1; } "
               "#status { height: 1; } #input { height: 3; } "
               "#debug { height: 8; display: none; } "
               "#budget.warn { color: yellow; } #budget.over { color: red; }")

        BINDINGS = [("ctrl+d", "toggle_debug", "Debug tail"),
                    ("ctrl+s", "open_sessions", "Sessions"),
                    # ctrl+p is Textual's command palette (built-in wins),
                    # so the provider/model picker lives on ctrl+o.
                    ("ctrl+o", "pick_provider", "Provider/model"),
                    ("ctrl+e", "export_transcript", "Export log")]

        def __init__(self, loop, session, bridge: "TuiBridge",
                     provider_name: str = "", model: str = "",
                     initial: str | None = None, cfg=None, control=None,
                     debug_path=None, debug_path_getter=None):
            super().__init__()
            self._agent_loop = loop
            self._session = session
            self._bridge = bridge
            self._provider_name = provider_name
            self._model = model
            self._initial = initial
            self._cfg = cfg
            self._control = control
            self._debug_path_static = debug_path
            self._debug_path_getter = debug_path_getter
            self._title = (f"{session.id} {provider_name}/{model}"
                           ).strip()
            self._dedupe = TranscriptDedupe()
            self._turn_start: float | None = None
            self._phase = "main"
            self._debug_visible = False

        def compose(self) -> "ComposeResult":
            yield Header(show_clock=False)
            with Vertical():
                yield BudgetBar("", id="budget")
                yield RichLog(id="transcript", wrap=True)
                yield DebugPanel(id="debug", wrap=True)
                yield StatusLine("", id="status")
                yield Input(placeholder="Type a task, Enter to run.",
                            id="input")
            yield Footer()

        def on_mount(self) -> None:
            self.title = self._title or "harness"
            self._wire_approver()
            self.set_interval(0.1, self._poll)
            if self._initial:
                self._submit(self._initial)

        def _wire_approver(self) -> None:
            approver = getattr(self._agent_loop, "approver", None)
            if (isinstance(approver, TUIApprover)
                    and approver.decide is None
                    and not approver.auto_approve):
                approver.decide = self._modal_decide

        def _log(self, text: str) -> None:
            try:
                self._transcript_lines.append(text)
            except AttributeError:
                self._transcript_lines = [text]
            self.query_one("#transcript", RichLog).write(text)

        def _export_transcript(self) -> None:
            """Copyable record: dump transcript lines to a session file.

            RichLog has no text selection, so export is the copy path:
            open the logged file in any editor to select/copy.
            """
            try:
                lines = list(getattr(self, "_transcript_lines", []) or [])
                stamp = time.strftime("%Y%m%d-%H%M%S")
                path = self._session.dir / f"transcript-{stamp}.log"
                path.write_text("\n".join(lines) + "\n",
                                encoding="utf-8")
                self.query_one("#transcript", RichLog).write(
                    f"[transcript exported: {path}]")
                self._transcript_lines.append(
                    f"[transcript exported: {path}]")
            except Exception as e:  # noqa: BLE001 - show, don't crash
                try:
                    self.query_one("#transcript", RichLog).write(
                        f"[export failed: {e}]")
                except Exception:
                    pass

        def action_export_transcript(self) -> None:
            self._export_transcript()

        def _log_lines(self, lines) -> None:
            for line in lines or []:
                self._log(line)

        # Approval modal hook: runs on the Loop worker thread, shows the
        # modal on the UI thread, waits up to timeout. None -> deny.
        def _modal_decide(self, info: dict):
            box = {"event": threading.Event(), "answer": None}
            self.call_from_thread(self._show_approval, info, box)
            timeout = info.get("timeout") or 120
            box["event"].wait(timeout)
            return box["answer"]

        def _show_approval(self, info: dict, box: dict) -> None:
            self.push_screen(ApprovalScreen(info, box))

        def _set_status(self, text: str) -> None:
            self.query_one("#status", StatusLine).update(text)

        def _set_budget(self, text: str, status: str) -> None:
            bar = self.query_one("#budget", BudgetBar)
            bar.update(text)
            for cls in ("warn", "over"):
                bar.remove_class(cls)
            if status in ("warn", "over"):
                bar.add_class(status)

        def _tick_status(self) -> None:
            if self._turn_start is not None:
                self._set_status(format_status(
                    self._phase, time.monotonic() - self._turn_start))

        def _poll(self) -> None:
            entries = self._bridge.drain()
            for kind, data, _line in entries:
                if kind == "request" and isinstance(data, dict) \
                        and "tokens_est" in data:
                    self._turn_start = time.monotonic()
                    self._phase = data.get("phase") or "main"
                    bar = budget_bar_text(data)
                    if bar is not None:
                        self._set_budget(bar, budget_bar_status(data))
                elif kind in ("response", "tool", "error"):
                    self._turn_start = None  # request span ends here
                    self._set_status("")
            for line in self._dedupe.feed(entries):
                self._log(line)
            self._tick_status()
            if self._debug_visible:
                self._refresh_debug()

        # --- slash parity with the CLI REPL ---

        def _tracker(self):
            if self._control is not None:
                get = getattr(self._control, "get_tracker", None)
                if callable(get):
                    try:
                        return get()
                    except Exception:
                        return None
            return None

        def _sync_state(self) -> None:
            """Re-attach loop/session after a switch (like CLI attach)."""
            if self._control is None:
                return
            sync = getattr(self._control, "sync_state", None)
            if not callable(sync):
                return
            try:
                self._agent_loop, self._session = sync()
            except Exception as e:  # never break the turn loop
                self._log(f"[error: session switch failed: {e}]")
                return
            # Header shows the EFFECTIVE provider/model: picker retarget
            # mutates cfg, so prefer it (via control) over init values.
            try:
                get_pm = getattr(self._control, "get_provider_model", None)
                if callable(get_pm):
                    self._provider_name, self._model = get_pm()
                elif self._cfg is not None:
                    self._provider_name = getattr(
                        self._cfg, "provider", self._provider_name)
                    self._model = getattr(self._cfg, "model", self._model)
            except Exception:
                pass
            self._dedupe = TranscriptDedupe()  # don't leak pending lines
            self._wire_approver()  # attach() built a fresh approver
            self._title = (f"{self._session.id} "
                           f"{self._provider_name}/{self._model}").strip()
            self.title = self._title or "harness"

        def _handle_slash(self, text: str) -> bool:
            """Run a /command. True when text was a slash command."""
            parsed = commands.parse_slash(text)
            if parsed is None:
                return False
            cmd, rest = parsed
            if commands.is_quit(cmd):
                self.exit()
                return True
            if cmd in ("open", "session"):
                if not rest:
                    self._log("usage: /open <id>  (/list to see ids)")
                elif self._control is not None:
                    self._log_lines(self._control.do_open(rest))
                    self._sync_state()
                else:
                    self._log("usage: /open <id>  (/list to see ids)")
                return True
            if cmd == "new":
                if self._control is not None:
                    self._log_lines(self._control.do_new(rest))
                    self._sync_state()
                    if rest:
                        self._submit(rest)
                return True
            if cmd == "project":
                if self._control is not None:
                    self._log_lines(self._control.do_project(rest))
                    self._sync_state()
                return True
            if cmd == "models":
                self._fetch_models()
                return True
            if cmd == "help":
                self._log_lines(commands.local_lines(
                    cmd, rest, cfg=self._cfg, tracker=self._tracker(),
                    list_lines=(getattr(self._control, "list_lines",
                                        None)
                                if self._control is not None else None)))
                self._log_lines(TUI_KEYS_HELP)
                return True
            if cmd == "providers" and rest.startswith("save-current"):
                fn = (getattr(self._control, "save_current_lines", None)
                      if self._control is not None else None)
                parts = rest.split()
                name = parts[1] if len(parts) > 1 else ""
                if callable(fn):
                    self._log_lines(fn(name))
                else:
                    self._log("usage: /providers save-current <name>")
                return True
            lines = commands.local_lines(
                cmd, rest, cfg=self._cfg, tracker=self._tracker(),
                list_lines=(getattr(self._control, "list_lines", None)
                            if self._control is not None else None))
            if lines is not None:
                self._log_lines(lines)
                return True
            # State/worker commands without a control: explain, don't hang.
            self._log(commands.unknown_hint(cmd))
            return True

        def _fetch_models(self) -> None:
            """Network-backed /models view; never blocks the UI thread."""
            self._set_status("fetching models...")
            self._log("[models] fetching...")

            def _work():
                try:
                    if self._control is not None:
                        lines = self._control.models_lines()
                    elif self._cfg is not None:
                        from ..__main__ import models_lines
                        lines = models_lines(self._cfg)
                    else:
                        lines = ["[models] unavailable"]
                except Exception as e:  # noqa: BLE001 - show, don't crash
                    lines = [f"[models] error fetching models: {e}"]
                self.call_from_thread(self._models_done, lines)

            threading.Thread(target=_work, daemon=True,
                             name="tui-models").start()

        def _models_done(self, lines) -> None:
            self._set_status("")
            self._log_lines(lines)

        # --- session + provider/model pickers (ctrl+s / ctrl+o) ---

        def _picker_rows(self):
            try:
                fn = getattr(self._control, "sessions_info", None)
                if callable(fn):
                    return list(fn())
                if self._cfg is not None:
                    from ..__main__ import sessions_info
                    return list(sessions_info(self._cfg))
            except Exception:
                pass
            return []

        def _open_session_picker(self) -> None:
            self.push_screen(SessionPickerScreen(self._picker_rows()))

        def _open_picked_session(self, sid: str) -> None:
            if self._control is not None:
                self._log_lines(self._control.do_open(sid))
                self._sync_state()

        def _picker_new(self, rest: str = "") -> None:
            if self._control is not None:
                self._log_lines(self._control.do_new(rest))
                self._sync_state()

        def _open_provider_picker(self) -> None:
            self.push_screen(ProviderScreen(
                current=self._provider_name,
                entries=provider_entry_rows(self._cfg)
                if self._cfg is not None else []))

        def _provider_chosen(self, provider: str) -> None:
            cand = None
            fn = (getattr(self._control, "candidate_for", None)
                  if self._control is not None else None)
            if callable(fn):
                try:
                    cand = fn(provider)
                except Exception:
                    cand = None
            if cand is not None:
                current_model = getattr(cand, "model", "") or ""
            else:
                providers = (getattr(self._cfg, "providers", None)
                             if self._cfg is not None else None) or {}
                entry = providers.get(provider) if isinstance(
                    providers, dict) else None
                current_model = ((entry or {}).get("model")
                                 if isinstance(entry, dict) else None)
                current_model = current_model or self._model
            self.push_screen(ModelScreen(
                provider, current_model,
                control=self._control, cfg=self._cfg, candidate=cand))

        def _provider_add(self) -> None:
            self.push_screen(ProviderTemplateScreen())

        def _provider_template_chosen(self, template_id: str) -> None:
            if template_id == BLANK_TEMPLATE_ID:
                template_id = None
            self.push_screen(ProviderAddScreen(prefill=template_id))

        def _provider_add_save(self, data: dict):
            """Validate + save + activate + retarget. None when ok,
            else an inline error string (screen stays open)."""
            from ..config import (config_path, save_provider_entry,
                                  validate_provider_entry)
            name = (data.get("name") or "").strip()
            entry = {"kind": (data.get("kind") or "").strip(),
                     "base_url": (data.get("base_url") or "").strip() or None,
                     "model": (data.get("model") or "").strip(),
                     "api_key_env": ((data.get("api_key_env") or "").strip()
                                     or None)}
            existing = (getattr(self._cfg, "providers", None)
                        if self._cfg is not None else None) or {}
            try:
                clean = validate_provider_entry(
                    name, entry, existing
                    if isinstance(existing, dict) else {},
                    require_unique=True)
            except ValueError as e:
                return str(e)
            try:
                save_provider_entry(config_path(), name, clean,
                                    activate=True)
            except (OSError, ValueError) as e:
                return f"save failed: {e}"
            if isinstance(existing, dict):
                existing[name] = clean
            if not self._retarget_provider_model(name, clean["model"]):
                return "retarget failed (see log)"
            # Drop add + template + provider screens, back to main.
            try:
                for _ in range(3):
                    self.pop_screen()
            except Exception:
                pass
            return None

        def _retarget_provider_model(self, provider: str,
                                     model: str) -> bool:
            """Retarget live loop; missing key keeps old, shows error."""
            fn = (getattr(self._control, "do_retarget", None)
                  if self._control is not None else None)
            if not callable(fn):
                self._log("[retarget] unavailable (no control)")
                return False
            try:
                lines = fn(provider, model)
            except SystemExit as e:
                # _require_key path: keep old provider, show why.
                msg = e.code if isinstance(e.code, str) else e
                self._log(f"[retarget] {msg}")
                return False
            except Exception as e:  # noqa: BLE001 - show, don't crash
                self._log(f"[retarget error: {e}]")
                return False
            self._sync_state()  # picks up box['loop'] + new labels
            self._log_lines(lines)
            return True

        def action_open_sessions(self) -> None:
            self._open_session_picker()

        def action_pick_provider(self) -> None:
            self._open_provider_picker()

        # --- debug tail toggle (best-effort, never breaks the turn) ---

        def _debug_path(self):
            try:
                if self._debug_path_getter is not None:
                    return self._debug_path_getter()
            except Exception:
                pass
            return self._debug_path_static

        def _refresh_debug(self) -> None:
            try:
                panel = self.query_one("#debug", DebugPanel)
                panel.refresh_from(self._debug_path(),
                                   DEBUG_TAIL_LINES)
            except Exception:
                pass  # missing widget/file must never break the turn

        def action_toggle_debug(self) -> None:
            self._debug_visible = not self._debug_visible
            try:
                panel = self.query_one("#debug", DebugPanel)
                panel.styles.display = ("block" if self._debug_visible
                                        else "none")
            except Exception:
                pass
            if self._debug_visible:
                if self._debug_path() is None:
                    self._log("[debug] no debug log (run with --debug)")
                else:
                    for line in debug_panel_lines(self._debug_path(),
                                                  DEBUG_TAIL_LINES):
                        if line.startswith("[debug]"):
                            self._log(line)
                            break
                self._refresh_debug()

        def on_input_submitted(self, event: "Input.Submitted") -> None:
            text = event.value.strip()
            event.input.value = ""
            if text:
                self._submit(text)

        def _submit(self, text: str) -> None:
            self._log(f"> {text}")
            if self._handle_slash(text):
                return
            self._turn_start = time.monotonic()
            self._phase = "main"
            self._tick_status()
            run_turn_in_thread(
                self._agent_loop, self._session, text,
                on_done=lambda _r: self.call_from_thread(
                    self._turn_done),
                on_error=lambda e: self.call_from_thread(
                    self._turn_failed, f"{type(e).__name__}: {e}",
                    "".join(traceback.format_exception(e))),
            )

        def _turn_done(self) -> None:
            self._turn_start = None
            self._set_status("")

        def _turn_failed(self, msg: str, detail: str = "") -> None:
            self._turn_start = None
            self._set_status("")
            self._log(f"[error: {msg}]")
            self._log(f"[error details: {self._write_error_log(msg, detail)}]")

        def _write_error_log(self, msg: str, detail: str = "") -> str:
            """Best-effort copyable record (transcript pane can't copy)."""
            try:
                path = self._session.dir / "tui-errors.log"
                with path.open("a", encoding="utf-8") as f:
                    f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                            f"{msg}\n{(detail or '').rstrip()}\n\n")
                return str(path)
            except Exception:
                return "<could not write tui-errors.log>"

    def run_app(loop, session, bridge, **kw) -> int:
        HarnessApp(loop, session, bridge, **kw).run()
        return 0

else:  # fallback when the extra is missing

    class HarnessApp:  # type: ignore[no-redef]
        def __init__(self, *a, **k):
            raise SystemExit(
                "TUI needs the extra: pip install -r requirements-tui.txt "
                "then run: python -m harness --tui")

    def run_app(*a, **k) -> int:
        print("TUI needs the extra: pip install -r requirements-tui.txt",
              flush=True)
        return 2
