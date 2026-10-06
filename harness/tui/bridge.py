"""TUI event bridge: stdlib-only, no Textual import here.

Loop (worker thread) -> on_event(kind, data) -> queue.Queue -> UI thread
polls and renders. Never touches UI objects directly.
"""
from __future__ import annotations

import queue
import threading

# Pure rendering helpers live in widgets.py (importable without the
# textual extra); re-exported here so existing `bridge` imports keep
# working with identical objects.
from .widgets import (TranscriptDedupe, budget_bar_status, budget_bar_text,
                       dedupe_entries, format_event, format_status)

__all__ = ["TuiBridge", "TranscriptDedupe", "budget_bar_status",
           "budget_bar_text", "dedupe_entries", "format_event",
           "format_status", "run_turn_in_thread"]


class TuiBridge:
    """Thread-safe on_event subscriber feeding the UI thread.

    Suitable as Loop(on_event=bridge). UI polls drain() or reads
    .queue directly; worker threads never touch UI objects.
    Each entry is (kind, data, line) where line is format_event output
    (may be None for quiet events).
    """

    def __init__(self) -> None:
        self.queue: queue.Queue = queue.Queue()

    def __call__(self, kind: str, data) -> None:
        try:
            line = format_event(kind, data)
        except Exception:
            line = None
        self.queue.put((kind, data, line))

    def drain(self) -> list:
        """Non-blocking drain of pending entries, in FIFO order."""
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out


def run_turn_in_thread(loop, session, text: str,
                       on_done=None, on_error=None) -> threading.Thread:
    """Run loop.run_turn off the UI thread; never touches UI directly.

    on_done(result) / on_error(exc) fire on the WORKER thread; the UI
    must re-dispatch via call_from_thread.
    """
    def _work():
        try:
            result = loop.run_turn(session, text)
        except Exception as e:  # noqa: BLE001 - report to UI
            if on_error is not None:
                on_error(e)
            return
        if on_done is not None:
            on_done(result)

    t = threading.Thread(target=_work, daemon=True, name="tui-turn")
    t.start()
    return t
