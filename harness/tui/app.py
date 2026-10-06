"""Textual app (Phase 2): transcript + budget bar + status + modal.

Worker threads run loop.run_turn; UI updates only via call_from_thread.
Never touches StdinPump. Without textual installed, degrades to a hint.
"""
from __future__ import annotations

import threading
import time

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
    from .bridge import (TranscriptDedupe, budget_bar_status,
                         budget_bar_text, format_status, run_turn_in_thread)


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
               "#budget.warn { color: yellow; } #budget.over { color: red; } "
               "#approval-box { padding: 1 2; }")

        def __init__(self, loop, session, bridge: "TuiBridge",
                     provider_name: str = "", model: str = "",
                     initial: str | None = None):
            super().__init__()
            self._loop = loop
            self._session = session
            self._bridge = bridge
            self._title = (f"{session.id} {provider_name}/{model}"
                           ).strip()
            self._initial = initial
            self._dedupe = TranscriptDedupe()
            self._turn_start: float | None = None
            self._phase = "main"

        def compose(self) -> "ComposeResult":
            yield Header(show_clock=False)
            with Vertical():
                yield Static("", id="budget")
                yield RichLog(id="transcript", wrap=True)
                yield Static("", id="status")
                yield Input(placeholder="Type a task, Enter to run.",
                            id="input")
            yield Footer()

        def on_mount(self) -> None:
            self.title = self._title or "harness"
            approver = getattr(self._loop, "approver", None)
            if (isinstance(approver, TUIApprover)
                    and approver.decide is None
                    and not approver.auto_approve):
                approver.decide = self._modal_decide
            self.set_interval(0.1, self._poll)
            if self._initial:
                self._submit(self._initial)

        def _log(self, text: str) -> None:
            self.query_one("#transcript", RichLog).write(text)

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
            self.query_one("#status", Static).update(text)

        def _set_budget(self, text: str, status: str) -> None:
            bar = self.query_one("#budget", Static)
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

        def on_input_submitted(self, event: "Input.Submitted") -> None:
            text = event.value.strip()
            event.input.value = ""
            if text:
                self._submit(text)

        def _submit(self, text: str) -> None:
            self._log(f"> {text}")
            self._turn_start = time.monotonic()
            self._phase = "main"
            self._tick_status()
            run_turn_in_thread(
                self._loop, self._session, text,
                on_done=lambda _r: self.call_from_thread(
                    self._turn_done),
                on_error=lambda e: self.call_from_thread(
                    self._turn_failed, str(e)),
            )

        def _turn_done(self) -> None:
            self._turn_start = None
            self._set_status("")

        def _turn_failed(self, msg: str) -> None:
            self._turn_start = None
            self._set_status("")
            self._log(f"[error: {msg}]")

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
