"""Minimal Textual app (Phase 1): transcript + input + status.

Worker threads call loop.run_turn; UI updates only via
call_from_thread. Never touches StdinPump. Without textual installed,
HarnessApp degrades to an install hint.
"""
from __future__ import annotations

try:
    from textual.app import App, ComposeResult
    from textual.containers import Vertical
    from textual.widgets import Footer, Header, Input, RichLog, Static

    _HAS = True
except ImportError:  # textual extra missing
    _HAS = False

if _HAS:
    from .bridge import TuiBridge, run_turn_in_thread


    class HarnessApp(App):
        CSS = ("#transcript { height: 1fr; } #status { height: 1; } "
               "#input { height: 3; }")

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
            self._busy = False

        def compose(self) -> "ComposeResult":
            yield Header(show_clock=False)
            with Vertical():
                yield RichLog(id="transcript", wrap=True)
                yield Static("", id="status")
                yield Input(placeholder="Type a task, Enter to run.",
                            id="input")
            yield Footer()

        def on_mount(self) -> None:
            self.title = self._title or "harness"
            self.set_interval(0.1, self._poll)
            if self._initial:
                self._submit(self._initial)

        def _log(self, text: str) -> None:
            self.query_one("#transcript", RichLog).write(text)

        def _poll(self) -> None:
            for kind, _data, line in self._bridge.drain():
                if kind == "request":
                    self.query_one("#status", Static).update("thinking…")
                    self._busy = True
                elif kind in ("response", "error"):
                    self.query_one("#status", Static).update("")
                    self._busy = False
                if line:
                    self._log(line)

        def on_input_submitted(self, event: "Input.Submitted") -> None:
            text = event.value.strip()
            event.input.value = ""
            if text:
                self._submit(text)

        def _submit(self, text: str) -> None:
            self._log(f"> {text}")
            self.query_one("#status", Static).update("thinking…")
            self._busy = True
            run_turn_in_thread(
                self._loop, self._session, text,
                on_done=lambda _r: self.call_from_thread(
                    self.query_one("#status", Static).update, ""),
                on_error=lambda e: self.call_from_thread(
                    self._log, f"[error: {e}]"),
            )

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
