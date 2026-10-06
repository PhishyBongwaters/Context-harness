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
    from textual.widgets import Footer, Header, Input, Label, RichLog, Static

    _HAS = True
except ImportError:  # textual extra missing
    _HAS = False

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


    class HarnessApp(App):
        CSS = ("#transcript { height: 1fr; } #budget { height: 1; } "
               "#status { height: 1; } #input { height: 3; } "
               "#debug { height: 8; display: none; } "
               "#budget.warn { color: yellow; } #budget.over { color: red; } "
               "#approval-box { padding: 1 2; }")

        BINDINGS = [("ctrl+d", "toggle_debug", "Debug tail")]

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
            self.query_one("#transcript", RichLog).write(text)

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
